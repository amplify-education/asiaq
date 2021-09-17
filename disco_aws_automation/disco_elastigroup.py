"""Contains DiscoElastigroup class that orchestrates AWS Spotinst Elastigroups"""
import copy
import logging
import time
import os

from base64 import b64encode
from itertools import groupby

import boto3

from disco_aws_automation.resource_helper import throttled_call, tag2dict
from .spotinst_client import SpotinstClient
from .base_group import BaseGroup
from .exceptions import TooManyAutoscalingGroups, SpotinstException, TimeoutError

logger = logging.getLogger(__name__)

# max time to wait in seconds for instances to become healthy after a roll
GROUP_ROLL_TIMEOUT = 1200


class DiscoElastigroup(BaseGroup):
    """Class orchestrating elastigroups"""

    def __init__(self, environment_name):
        self.environment_name = environment_name

        if os.environ.get('SPOTINST_TOKEN'):
            self.spotinst_client = SpotinstClient(
                token=os.environ.get('SPOTINST_TOKEN'),
                environment_name=environment_name
            )
        else:
            self.spotinst_client = None
            logger.warn('Create environment variable "SPOTINST_TOKEN" in order to use SpotInst')
        super(DiscoElastigroup, self).__init__()

    def is_spotinst_enabled(self):
        """Return True if SpotInst should be used"""

        # if the spotinst client doesn't exist (meaning the token is missing) then don't use spotinst
        return self.spotinst_client is not None

    def _get_new_groupname(self, hostclass):
        """Returns a new elastigroup name when given a hostclass"""
        return self.environment_name + '_' + hostclass + "_" + str(int(time.time()))

    def _filter_by_environment(self, groups):
        """Filters elastigroups by environment"""
        return [
            group for group in groups
            if group['name'].startswith("{0}_".format(self.environment_name))
        ]

    def _get_hostclass(self, group_name):
        """Returns the hostclass when given an elastigroup name"""
        # group names follow a <env>_hostclass_<id> pattern. hostclass names could have underscores
        # so we need to be careful about how we split out the hostclass name
        parts = group_name.split('_')[1:-1]
        return '_'.join(parts)

    def _get_spotinst_groups(self, hostclass=None, group_name=None):
        groups = self.spotinst_client.get_groups()
        session = boto3.session.Session()

        return [group for group in groups
                if group['name'].startswith(self.environment_name) and
                (not group_name or group['name'] == group_name) and
                (not hostclass or self._get_hostclass(group['name']) == hostclass) and
                session.region_name in group['compute']['availabilityZones'][0]['name']]

    def get_existing_groups(self, hostclass=None, group_name=None):
        # get a dict for each group that matches the structure that would be returned by DiscoAutoscale
        # this dict needs to have at least all the fields that the interface specifies
        groups = []
        for group in self._get_spotinst_groups(hostclass, group_name):
            launch_spec = group['compute']['launchSpecification']

            # Need this mess because loadBalancersConfig could be missing or might return None
            load_balancer_configs = launch_spec.get('loadBalancersConfig', {}).get('loadBalancers', []) or []

            groups.append({
                'name': group['name'],
                'min_size': group['capacity']['minimum'],
                'max_size': group['capacity']['maximum'],
                'desired_capacity': group['capacity']['target'],
                'launch_config_name': None,
                'termination_policies': [],
                'vpc_zone_identifier': ','.join(
                    subnet for zone in group['compute']['availabilityZones'] for subnet in zone['subnetIds']
                ),
                'load_balancers': [elb['name'] for elb in load_balancer_configs
                                   if elb['type'] == 'CLASSIC'],
                'target_groups': [tg['arn'] for tg in load_balancer_configs
                                  if tg['type'] == 'TARGET_GROUP'],
                'image_id': launch_spec['imageId'],
                'id': group['id'],
                'type': 'spot',
                # blockDeviceMappings will be None instead of a empty list if there is no ELB
                'blockDeviceMappings': (launch_spec.get('blockDeviceMappings') or []),
                'scheduling': group.get('scheduling', {'tasks': []}),
                'tags': {tag['tagKey']: tag['tagValue'] for tag in launch_spec.get('tags', [])}
            })
        groups.sort(key=lambda grp: grp['name'], reverse=True)
        return groups

    def get_existing_group(self, hostclass=None, group_name=None, throw_on_two_groups=True):
        """
        Returns the elastigroup dict for the given hostclass or group name, or None if
        no elastigroup exists.

        If two or more elastigroups exist for a hostclass, then this method will throw an exception,
        unless 'throw_on_two_groups' is False. Then if there are two groups the most recently created
        elastigroup will be returned. If there are more than two elastigroups, this method will
        always throw an exception.
        """
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        if not groups:
            return None
        elif len(groups) == 1 or (len(groups) == 2 and not throw_on_two_groups):
            return groups[0]
        else:
            raise TooManyAutoscalingGroups("There are too many elastigroups for {}.".format(hostclass))

    def _get_group_instances(self, group_id):
        """Returns list of instance ids in a group"""
        return self.spotinst_client.get_group_status(group_id)

    def get_instances(self, hostclass=None, group_name=None):
        """Returns elastigroup instances for hostclass in the current environment"""
        next_token = None
        instances = []

        filters = [{
            'Name': 'tag:spotinst',
            'Values': ['True']
        }, {
            'Name': 'tag:environment',
            'Values': [self.environment_name]
        }, {
            'Name': 'instance-state-name',
            'Values': ['pending', 'running', 'shutting-down', 'stopping', 'stopped']
        }]

        if hostclass:
            filters.append({
                'Name': 'tag:hostclass',
                'Values': [hostclass]
            })

        if group_name:
            filters.append({
                'Name': 'tag:group_name',
                'Values': [group_name]
            })

        while True:
            args = {'Filters': filters}
            if next_token:
                args['NextToken'] = next_token
            response = throttled_call(self.boto3_ec.describe_instances, **args)
            for reservation in response.get('Reservations'):
                for instance in reservation.get('Instances'):
                    instances.append({
                        'instance_id': instance['InstanceId'],
                        'group_name': tag2dict(instance['Tags']).get('group_name')
                    })

            next_token = response.get('NextToken')

            if not next_token:
                break

        return instances

    def list_groups(self):
        """Returns list of objects for display purposes for all groups"""
        groups = self.get_existing_groups()
        return [
            {
                'name': group['name'],
                'image_id': group['image_id'],
                'group_cnt': len(self._get_group_instances(group['id'])),
                'min_size': group['min_size'],
                'desired_capacity': group['desired_capacity'],
                'max_size': group['max_size'],
                'type': group['type'],
                'tags': group['tags']
            }
            for group in groups
        ]

    def _create_elastigroup_config(self, desired_size, min_size, max_size, instance_type,
                                   subnets, load_balancers, target_groups, security_groups,
                                   instance_monitoring, ebs_optimized, image_id, key_name,
                                   associate_public_ip_address, user_data, tags, instance_profile_name,
                                   block_device_mappings, group_name, spotinst_reserve):
        # Pylint thinks this function has too many arguments and too many local variables (it does)
        # pylint: disable=too-many-arguments, too-many-locals
        """Create new elastigroup configuration"""
        strategy = {
            'availabilityVsCost': "availabilityOriented",
            'utilizeReservedInstances': True,
            'fallbackToOd': True,
            "revertToSpot": {
                "performAt": "timeWindow",
                # time is in UTC. This is 6AM EST
                "timeWindows": [
                    "Sun:10:00-Sun:11:00",
                    "Mon:10:00-Mon:11:00",
                    "Tue:10:00-Tue:11:00",
                    "Wed:10:00-Wed:11:00",
                    "Thu:10:00-Thu:11:00",
                    "Fri:10:00-Fri:11:00",
                    "Sat:10:00-Sat:11:00"
                ]
            }
        }

        strategy.update(self._get_risk_config(spotinst_reserve))

        _min_size = min_size or 0
        _max_size = max([min_size, max_size, desired_size, 0])
        _desired_capacity = desired_size or max_size

        capacity = {
            'target': _desired_capacity,
            'minimum': _min_size,
            'maximum': _max_size,
            'unit': "instance"
        }

        compute = {
            "instanceTypes": self._get_instance_type_config(instance_type),
            "product": "Linux/UNIX"
        }

        compute['availabilityZones'] = [
            {
                'name': zone,
                'subnetIds': [subnet['SubnetId'] for subnet in zone_subnets]
            }
            for zone, zone_subnets in groupby(subnets, key=lambda subnet: subnet['AvailabilityZone'])
        ] if subnets else None

        bdms = self._get_block_device_config(block_device_mappings)

        network_interfaces = [
            {"deleteOnTermination": True,
             "deviceIndex": 0,
             "associatePublicIpAddress": associate_public_ip_address}
        ] if associate_public_ip_address else None

        launch_specification = {
            "loadBalancersConfig": self._get_load_balancer_config(load_balancers, target_groups),
            "securityGroupIds": security_groups,
            "monitoring": instance_monitoring,
            "ebsOptimized": ebs_optimized,
            "imageId": image_id,
            "keyPair": key_name,
            "blockDeviceMappings": bdms or None,
            "networkInterfaces": network_interfaces,
            "userData": b64encode(str(user_data)) if user_data else None,
            "iamRole": {
                "name": instance_profile_name
            } if instance_profile_name else None
        }

        tags = tags or {}
        tags['group_name'] = group_name
        launch_specification['tags'] = self._create_elastigroup_tags(tags)

        compute["launchSpecification"] = launch_specification

        group = {
            "name": group_name,
            "description": "Spotinst elastigroup: {}".format(group_name),
            "strategy": strategy,
            "capacity": capacity,
            "compute": compute
        }

        logger.info("Creating elastigroup config for elastigroup '%s'", group_name)

        elastigroup_config = {"group": group}
        return elastigroup_config

    def _create_elastigroup_tags(self, tags):
        """Given a python dictionary, it returns a list of elastigroup tags"""
        spotinst_tags = [{'tagKey': key, 'tagValue': str(value)}
                         for key, value in tags.iteritems()] if tags else []

        spotinst_tags.append({'tagKey': 'spotinst', 'tagValue': 'True'})

        return spotinst_tags

    def create_or_update_group(self, hostclass, desired_size=None, min_size=None, max_size=None,
                               instance_type=None, load_balancers=None, target_groups=None, subnets=None,
                               security_groups=None, instance_monitoring=None, ebs_optimized=None,
                               image_id=None, key_name=None, associate_public_ip_address=None, user_data=None,
                               tags=None, instance_profile_name=None, block_device_mappings=None,
                               group_name=None, create_if_exists=False, termination_policies=None,
                               spotinst=False, spotinst_reserve=None):
        # Pylint thinks this function has too many arguments and too many local variables
        # pylint: disable=R0913, R0914
        """Updates an existing elastigroup if it exists, otherwise this creates a new elastigroup."""
        if not spotinst:
            raise SpotinstException('DiscoElastiGroup must be used for creating SpotInst groups')

        existing_groups = self._get_spotinst_groups(hostclass, group_name)
        if existing_groups and not create_if_exists:
            group = existing_groups[0]
            self._modify_group(
                group, desired_size=desired_size, min_size=min_size, max_size=max_size,
                image_id=image_id, tags=tags, instance_profile_name=instance_profile_name,
                block_device_mappings=block_device_mappings, spotinst_reserve=spotinst_reserve,
                load_balancers=load_balancers, target_groups=target_groups, instance_type=instance_type,
                user_data=user_data
            )

            return {'name': group['name']}

        return self._create_group(
            desired_size=desired_size,
            min_size=min_size,
            max_size=max_size,
            instance_type=instance_type,
            load_balancers=load_balancers,
            target_groups=target_groups,
            subnets=subnets,
            security_groups=security_groups,
            instance_monitoring=instance_monitoring,
            ebs_optimized=ebs_optimized,
            image_id=image_id,
            key_name=key_name,
            associate_public_ip_address=associate_public_ip_address,
            user_data=user_data,
            tags=tags,
            instance_profile_name=instance_profile_name,
            block_device_mappings=block_device_mappings,
            group_name=self._get_new_groupname(hostclass),
            spotinst_reserve=spotinst_reserve
        )

    # pylint: disable=too-many-arguments, too-many-locals
    def _create_group(self, desired_size=None, min_size=None, max_size=None,
                      instance_type=None, load_balancers=None, target_groups=None, subnets=None,
                      security_groups=None, instance_monitoring=None, ebs_optimized=None, image_id=None,
                      key_name=None, associate_public_ip_address=None, user_data=None, tags=None,
                      instance_profile_name=None, block_device_mappings=None, group_name=None,
                      spotinst_reserve=None):

        group_config = self._create_elastigroup_config(
            desired_size=desired_size,
            min_size=min_size,
            max_size=max_size,
            instance_type=instance_type,
            load_balancers=load_balancers,
            target_groups=target_groups,
            subnets=subnets,
            security_groups=security_groups,
            instance_monitoring=instance_monitoring,
            ebs_optimized=ebs_optimized,
            image_id=image_id,
            key_name=key_name,
            associate_public_ip_address=associate_public_ip_address,
            user_data=user_data,
            tags=tags,
            instance_profile_name=instance_profile_name,
            block_device_mappings=block_device_mappings,
            group_name=group_name,
            spotinst_reserve=spotinst_reserve
        )

        new_group = self.spotinst_client.create_group(group_config)
        new_group_name = new_group['name']

        return {'name': new_group_name}

    def _modify_group(self, existing_group, desired_size=None, min_size=None, max_size=None,
                      image_id=None, tags=None, instance_profile_name=None, block_device_mappings=None,
                      spotinst_reserve=None, load_balancers=None, target_groups=None, instance_type=None,
                      user_data=None):
        new_config = copy.deepcopy(existing_group)

        if min_size is not None:
            new_config['capacity']['minimum'] = min_size
        if max_size is not None:
            new_config['capacity']['maximum'] = max_size
        if desired_size is not None:
            new_config['capacity']['target'] = desired_size
        if spotinst_reserve is not None:
            new_config['strategy'] = self._get_risk_config(spotinst_reserve)
        if tags is not None:
            tags['group_name'] = existing_group['name']
            new_config['compute']['launchSpecification']['tags'] = self._create_elastigroup_tags(tags)
        if image_id is not None:
            new_config['compute']['launchSpecification']['imageId'] = image_id
        if block_device_mappings is not None:
            launch_spec = new_config['compute']['launchSpecification']
            launch_spec['blockDeviceMappings'] = self._get_block_device_config(block_device_mappings)
        if instance_profile_name is not None:
            new_config['compute']['launchSpecification']['iamRole'] = {
                'name': instance_profile_name
            }
        if instance_type is not None:
            new_config['compute']['instanceTypes'] = self._get_instance_type_config(instance_type)
        if user_data is not None:
            new_config['compute']['launchSpecification']['userData'] = b64encode(str(user_data))

        # remove fields that can't be updated
        new_config['capacity'].pop('unit', None)
        new_config['compute'].pop('product', None)
        new_config.pop('createdAt', None)
        new_config.pop('updatedAt', None)

        group_id = new_config.pop('id')

        self.spotinst_client.update_group(group_id, {'group': new_config})

        if load_balancers or target_groups:
            self.update_elb(load_balancers, target_groups, group_name=existing_group['name'])

    def _delete_group(self, group_id):
        """Delete an elastigroup by group id"""
        self.spotinst_client.delete_group(group_id)

    def delete_groups(self, hostclass=None, group_name=None, force=False):
        """Delete all elastigroups based on hostclass"""
        # We need argument `force` to match method in autoscale
        # pylint: disable=unused-argument
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            logger.info("Deleting group %s", group['name'])
            self._delete_group(group_id=group['id'])

    def scaledown_groups(self, hostclass=None, group_name=None, wait=False, noerror=False):
        """
        Scales down number of instances in a hostclass's elastigroup, or the given elastigroup, to zero.
        If wait is true, this function will block until all instances are terminated, or it will raise
        a WaiterError if this process times out, unless noerror is True.

        Returns true if the elastigroups were successfully scaled down, False otherwise.
        """
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            group_update = {
                "group": {
                    "capacity": {
                        "target": 0,
                        "minimum": 0,
                        "maximum": 0
                    }
                }
            }
            logger.info("Scaling down group %s", group['name'])
            self.spotinst_client.update_group(group['id'], group_update)

            if wait:
                self.wait_instance_termination(group_name=group_name, group=group, noerror=noerror)

    def terminate(self, instance_id, decrement_capacity=True):
        """
        Terminates an instance using the autoscaling API.

        When decrement_capacity is True this allows us to avoid
        autoscaling immediately replacing a terminated instance.
        """
        pass

    def delete_all_recurring_group_actions(self, hostclass=None, group_name=None):
        """Deletes all recurring scheduled actions for a hostclass"""
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for existing_group in groups:
            logger.info("Deleting scheduled actions for autoscaling group %s", existing_group['name'])

            group_config = {
                'group': {
                    'scheduling': None
                }
            }

            self.spotinst_client.update_group(existing_group['id'], group_config)

    def create_recurring_group_action(self, recurrance, min_size=None, desired_capacity=None, max_size=None,
                                      hostclass=None, group_name=None):
        """Creates a recurring scheduled action for a hostclass"""
        existing_groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for existing_group in existing_groups:
            logger.info("Creating scheduled action for hostclass %s, group_name %s", hostclass, group_name)
            task = {
                'cronExpression': recurrance,
                'taskType': 'scale'
            }

            if min_size is not None:
                task['scaleMinCapacity'] = min_size

            if max_size is not None:
                task['scaleMaxCapacity'] = max_size

            if desired_capacity is not None:
                task['scaleTargetCapacity'] = desired_capacity

            existing_schedule = existing_group['scheduling']

            # don't create tasks that already exist
            if task in existing_schedule['tasks']:
                continue

            existing_schedule['tasks'].append(task)

            group_config = {
                'group': {
                    'scheduling': existing_schedule
                }
            }

            self.spotinst_client.update_group(existing_group['id'], group_config)

    def update_elb(self, elb_names, target_groups, hostclass=None, group_name=None):
        """Updates an existing autoscaling group to use a different set of load balancers"""
        # pylint: disable=arguments-differ
        existing_group = self.get_existing_group(hostclass=hostclass, group_name=group_name)

        if not existing_group:
            logger.warning(
                "Auto Scaling group %s does not exist. Cannot change %s ELB(s)",
                hostclass or group_name,
                ', '.join(elb_names)
            )
            return set(), set()

        new_lbs = set(elb_names) - set(existing_group['load_balancers'])
        extra_lbs = set(existing_group['load_balancers']) - set(elb_names)

        if new_lbs or extra_lbs:
            logger.info(
                "Updating ELBs for group %s from [%s] to [%s]",
                existing_group['name'],
                ", ".join(existing_group['load_balancers']),
                ", ".join(elb_names)
            )

        elb_configs = [
            {
                'name': elb,
                'type': 'CLASSIC'
            } for elb in elb_names
        ]

        new_tgs = set(target_groups) - set(existing_group['target_groups'])
        extra_tgs = set(existing_group['target_groups']) - set(target_groups)

        if new_tgs or extra_tgs:
            logger.info(
                "Updating Target Groups for group %s from [%s] to [%s]",
                existing_group['name'],
                ", ".join(existing_group['target_groups']),
                ", ".join(target_groups)
            )

        target_group_configs = [
            {
                'arn': target_group,
                'type': 'TARGET_GROUP'
            } for target_group in target_groups
        ]

        new_configs = elb_configs + target_group_configs
        group_config = {
            'group': {
                'compute': {
                    'launchSpecification': {
                        'loadBalancersConfig': {
                            'loadBalancers': new_configs
                        }
                    }
                }
            }
        }

        self.spotinst_client.update_group(existing_group['id'], group_config)

        return new_lbs, extra_lbs, new_tgs, extra_tgs

    def get_launch_config(self, hostclass=None, group_name=None):
        """Return launch config info for a hostclass, None otherwise"""
        existing_groups = self._get_spotinst_groups(hostclass=hostclass, group_name=group_name)

        if not existing_groups:
            return None

        on_demand_type = existing_groups[0]['compute']['instanceTypes']['ondemand']

        # the first spot type is always the ondemand type so strip it out to avoid returning it twice
        spot_types = existing_groups[0]['compute']['instanceTypes']['spot'][1:]

        instance_type = ':'.join([on_demand_type] + spot_types)

        return {
            'instance_type': instance_type
        }

    def clean_configs(self):
        """Delete unused Launch Configurations in current environment"""
        raise Exception('Elastigroups don\'t have launch configs')

    def get_configs(self, names=None):
        """Returns Launch Configurations in current environment"""
        raise Exception('Elastigroups don\'t have launch configs')

    def delete_config(self, config_name):
        """Delete a specific Launch Configuration"""
        raise Exception('Elastigroups don\'t have launch configs')

    def list_policies(self, group_name=None, policy_types=None, policy_names=None):
        """Returns all autoscaling policies"""
        raise Exception('Scaling for Elastigroups is not implemented')

    # pylint: disable=too-many-arguments
    def create_policy(self, group_name, policy_name, policy_type="SimpleScaling", adjustment_type=None,
                      min_adjustment_magnitude=None, scaling_adjustment=None, cooldown=600,
                      metric_aggregation_type=None, step_adjustments=None, estimated_instance_warmup=None):
        """
        Creates a new autoscaling policy, or updates an existing one if the autoscaling group name and
        policy name already exist. Handles the logic of constructing the correct autoscaling policy request,
        because not all parameters are required.
        """
        raise Exception('Scaling for Elastigroups is not implemented')

    def delete_policy(self, policy_name, group_name):
        """Deletes an autoscaling policy"""
        raise Exception('Scaling for Elastigroups is not implemented')

    def update_snapshot(self, snapshot_id, snapshot_size, hostclass=None, group_name=None):
        """Updates all of a hostclasses existing autoscaling groups to use a different snapshot"""
        existing_group = self.get_existing_group(hostclass, group_name)

        if not existing_group:
            raise Exception(
                'Elastigroup for %s hostclass and %s group name does not exist' %
                (str(hostclass), str(group_name))
            )

        block_device_mappings = existing_group['blockDeviceMappings']

        # find which device uses snapshots. throw errors if none found or more than 1 found
        snapshot_devices = [device['ebs'] for device in block_device_mappings
                            if device.get('ebs', {}).get('snapshotId')]

        if not snapshot_devices:
            raise Exception("Hostclass %s does not mount a snapshot" % hostclass)
        elif len(snapshot_devices) > 1:
            raise Exception(
                "Unsupported configuration: hostclass %s has multiple snapshot based devices." % hostclass
            )

        snapshot_device = snapshot_devices[0]
        old_snapshot_id = snapshot_device['snapshotId']

        if old_snapshot_id == snapshot_id:
            logger.debug(
                "Autoscaling group %s is already referencing latest snapshot %s",
                hostclass or group_name,
                snapshot_id
            )
            return

        snapshot_device['snapshotId'] = snapshot_id
        snapshot_device['volumeSize'] = snapshot_size

        group_config = {
            'group': {
                'compute': {
                    'launchSpecification': {
                        'blockDeviceMappings': block_device_mappings
                    }
                }
            }
        }

        logger.info(
            "Updating %s group's snapshot from %s to %s",
            hostclass or group_name,
            old_snapshot_id,
            snapshot_id
        )

        self.spotinst_client.update_group(existing_group['id'], group_config)

    def _roll_group(self, group_id, batch_percentage=100, grace_period=GROUP_ROLL_TIMEOUT,
                    health_check_type='EC2', wait=False):
        """
        Recreate the instances in a Elastigroup
        :param group_id (str): Elastigroup ID to roll
        :param batch_percentage (int): Percentage of instances to roll at a time (0-100)
        :param grace_period (int): Time in seconds to wait for new instances to become healthy
        :param wait (boolean): True to wait for roll operation to finish
        :raises TimeoutError if grace_period has expired
        """
        self.spotinst_client.roll_group(group_id, batch_percentage, grace_period, health_check_type)

        if wait:
            # wait for the deploy to appear in list
            time.sleep(10)

            deployments = self.spotinst_client.get_deployments(group_id)
            deploy_id = deployments[-1]['id']

            current_time = time.time()

            # wait an extra amount of time after grace_period has ended to give time for roll to finish
            stop_time = current_time + grace_period + 300

            while current_time < stop_time:
                roll_status = self.spotinst_client.get_roll_status(group_id, deploy_id)
                if roll_status['status'] not in ('in_progress', 'starting'):
                    if roll_status['status'] != 'finished':
                        logger.error("Roll of group %s did not complete successfully with status %s",
                                     group_id, roll_status['status'])
                    break

                logger.info("Waiting for %s group to roll in order to update settings", group_id)
                time.sleep(10)
                current_time = time.time()

            if current_time >= stop_time:
                raise TimeoutError(
                    "Timed out after waiting %s seconds for rolling deploy of %s" %
                    (grace_period, group_id)
                )

    def _get_instance_type_config(self, instance_types):
        return {
            "ondemand": instance_types.split(':')[0],
            "spot": instance_types.split(':')
        }

    def _get_load_balancer_config(self, load_balancers, target_groups):
        lbs = [{"name": elb, "type": "CLASSIC"} for elb in load_balancers] if load_balancers else []
        tgs = [{"arn": tg, "type": "TARGET_GROUP"} for tg in target_groups] if target_groups else []
        return {
            "loadBalancers": lbs + tgs
        } if load_balancers or target_groups else None

    def _get_risk_config(self, spotinst_reserve):
        if not spotinst_reserve:
            return {
                'risk': 100,
                'onDemandCount': None
            }

        if str(spotinst_reserve).endswith('%'):
            return {
                'risk': 100 - int(spotinst_reserve.strip('%')),
                'onDemandCount': None
            }
        return {
            'risk': None,
            'onDemandCount': int(spotinst_reserve)
        }

    def _get_block_device_config(self, block_device_mappings):
        bdms = []
        for block_device_mapping in block_device_mappings or []:
            for name, device in block_device_mapping.iteritems():
                if device.ephemeral_name:
                    bdms.append({
                        'deviceName': name,
                        'virtualName': device.ephemeral_name
                    })
                elif any([device.size, device.iops, device.snapshot_id]):
                    bdm = {'deviceName': name, 'ebs': {'deleteOnTermination': device.delete_on_termination}}
                    if device.size:
                        bdm['ebs']['volumeSize'] = device.size
                    if device.iops:
                        bdm['ebs']['iops'] = device.iops
                    if device.volume_type:
                        bdm['ebs']['volumeType'] = device.volume_type
                    if device.snapshot_id:
                        bdm['ebs']['snapshotId'] = device.snapshot_id
                    bdms.append(bdm)
        return bdms or None
