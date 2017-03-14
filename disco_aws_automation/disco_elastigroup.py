"""Contains DiscoElastigroup class that orchestrates AWS Spotinst Elastigroups"""
import logging
import time
import json
import os

from base64 import b64encode

import requests
import boto3

from .base_group import BaseGroup
from .exceptions import TooManyAutoscalingGroups, SpotinstException

logger = logging.getLogger(__name__)

SPOTINST_API = 'https://api.spotinst.io/aws/ec2/group'


class DiscoElastigroup(BaseGroup):
    """Class orchestrating elastigroups"""

    def __init__(self, environment_name, session=None, account_id=None):
        self.environment_name = environment_name
        self._session = session
        self._account_id = account_id
        super(DiscoElastigroup, self).__init__()

    @property
    def token(self):
        """
        Returns spotinst auth token from environment variable SPOTINST_TOKEN

        Environment variable example:
        SPOTINST_TOKEN=d7e6c5abb51bb04fcaa411b7b70cce414c931bf719f7db0674b296e588630515
        """
        try:
            token = os.environ['SPOTINST_TOKEN']
            return token
        except KeyError:
            logger.info('Create environment variable "SPOTINST_TOKEN"')

    @property
    def session(self):
        """Lazily create session object"""
        if not self._session:
            self._session = requests.Session()

            # Insert auth token in header
            self._session.headers.update(
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer {}".format(self.token)
                }
            )

        return self._session

    @property
    def account_id(self):
        """Account id of the current IAM user"""
        if not self._account_id:
            self._account_id = boto3.client('sts').get_caller_identity().get('Account')
        return self._account_id

    def _spotinst_call(self, path='/', data=None, method='get'):
        if method not in ['get', 'post', 'put', 'delete']:
            raise Exception('Method {} is not supported'.format(method))
        method_to_call = getattr(self.session, method)
        response = method_to_call(SPOTINST_API + path, data=json.dumps(data) if data else None)
        if response.status_code == 200:
            return response
        else:
            raise SpotinstException('Spotinst API error. Path: {} - Status code: {} - Reason: {}'.
                                    format(path, response.status_code, response.reason))

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
        return group_name.split('_')[1]

    def get_existing_groups(self, hostclass=None, group_name=None):
        """
        Returns all elastigroups for a given hostclass or group name, sorted by most recent creation. If no
        elastigroup can be found, returns an empty list.
        """
        try:
            groups = self._spotinst_call().json()['response']['items']
            groups = [group for group in groups if group['name'].startswith(self.environment_name)]
        except SpotinstException:
            raise
        except KeyError:
            return []
        if group_name:
            groups = [group for group in groups if group['name'] == group_name]
        filtered_groups = [group for group in groups
                           if not hostclass or self._get_hostclass(group['name']) == hostclass]
        filtered_groups.sort(key=lambda grp: grp['name'], reverse=True)
        return filtered_groups

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
        try:
            return self._spotinst_call(path='/' + group_id + '/status').json()['response']['items']
        except SpotinstException as err:
            logger.info('Unable to get Spotinst group instances: %s', err.message)
            return []

    def get_instances(self, hostclass=None, group_name=None):
        """Returns elastigroup instances for hostclass in the current environment"""
        all_groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        groups_id_name = {
            group['id']: group['name'] for group in all_groups
        }
        instances = []
        for group_id in groups_id_name:
            group_instances = self._get_group_instances(group_id)
            for instance in group_instances:
                instance.update({'instance_id': instance['instanceId'],
                                 'group_name': groups_id_name[group_id]})
            instances += group_instances
        return instances

    def list_groups(self):
        """Returns list of objects for display purposes for all groups"""
        groups = self.get_existing_groups()
        return [{'name': group['name'],
                 'image_id': group['compute']['launchSpecification']['imageId'],
                 'group_cnt': len(self._get_group_instances(group['id'])),
                 'min_size': group['capacity']['minimum'],
                 'desired_capacity': group['capacity']['target'],
                 'max_size': group['capacity']['maximum'],
                 'type': 'spot'} for group in groups]

    def _create_elastigroup_config(self, hostclass, availability_vs_cost, desired_size, min_size, max_size,
                                   instance_type, zones, load_balancers, security_groups, instance_monitoring,
                                   ebs_optimized, image_id, key_name, associate_public_ip_address, user_data,
                                   tags, instance_profile_name, block_device_mappings, group_name):
        # Pylint thinks this function has too many arguments and too many local variables
        # pylint: disable=R0913, R0914
        # We need unused argument to match method in autoscale
        # pylint: disable=unused-argument
        """Create new elastigroup configuration"""
        strategy = {'risk': 100, 'availabilityVsCost': availability_vs_cost,
                    'utilizeReservedInstances': True, 'fallbackToOd': True}
        capacity = {'target': desired_size, 'minimum': min_size, 'maximum': max_size, 'unit': "instance"}

        compute = {"instanceTypes": {
            "ondemand": instance_type.split(':')[0],
            "spot": instance_type.split(':')
        }, "availabilityZones": [{'name': zone, 'subnetIds': [subnet_id]}
                                 for zone, subnet_id in zones.iteritems()], "product": "Linux/UNIX"}

        bdms = []
        for name, ebs in block_device_mappings[0].iteritems():
            if any([ebs.size, ebs.iops, ebs.snapshot_id]):
                bdm = {'deviceName': name, 'ebs': {'deleteOnTermination': ebs.delete_on_termination}}
                if ebs.size:
                    bdm['ebs']['volumeSize'] = ebs.size
                if ebs.iops:
                    bdm['ebs']['iops'] = ebs.iops
                if ebs.volume_type:
                    bdm['ebs']['volumeType'] = ebs.volume_type
                if ebs.snapshot_id:
                    bdm['ebs']['snapshotId'] = ebs.snapshot_id
                bdms.append(bdm)
            else:
                bdms = None

        network_interfaces = [
            {"deleteOnTermination": True,
             "deviceIndex": 0,
             "associatePublicIpAddress": associate_public_ip_address}
        ] if associate_public_ip_address else None

        if load_balancers:
            elbs = [{"name": elb, "type": "CLASSIC"} for elb in load_balancers]
        else:
            elbs = None

        launch_specification = {
            "loadBalancersConfig": {
                "loadBalancers": elbs
            },
            "securityGroupIds": security_groups,
            "monitoring": instance_monitoring,
            "ebsOptimized": ebs_optimized,
            "imageId": image_id,
            "keyPair": key_name,
            "blockDeviceMappings": bdms,
            "networkInterfaces": network_interfaces,
            "userData": b64encode(str(user_data)),
            "tags": self._create_elastigroup_tags(tags),
            "iamRole": {
                "arn": "arn:aws:iam::{}:instance-profile/{}".format(self.account_id, instance_profile_name)
            }
        }

        compute["launchSpecification"] = launch_specification

        group = {
            "name": group_name,
            "description": "Spotinst elastigroup: {}".format(group_name),
            "strategy": strategy,
            "capacity": capacity,
            "compute": compute
        }

        logger.info(
            "Creating elastigroup config for elastigroup '%s'", group_name)

        elastigroup_config = {"group": group}
        return elastigroup_config

    def _create_elastigroup_tags(self, tags):
        """Given a python dictionary, it returns a list of elastigroup tags"""
        return [{'tagKey': key, 'tagValue': str(value)}
                for key, value in tags.iteritems()] if tags else None

    def _create_az_subnets_dict(self, subnets):
        """ Create a dictionary with key AZ and value subnet id"""
        zones = {}
        for subnet in subnets:
            zones[subnet['AvailabilityZone']] = subnet['SubnetId']
        return zones

    def wait_for_instance_id(self, group_name):
        """Wait for instance id(s) of an elastigroup to become available"""
        while not all([instance['instance_id'] for instance in self.get_instances(group_name=group_name)]):
            logger.info('Waiting for instance id(s) of %s to become available', group_name)
            time.sleep(10)

    def update_group(self, hostclass, desired_size=None, min_size=None, max_size=None, instance_type=None,
                     load_balancers=None, subnets=None, security_groups=None, instance_monitoring=None,
                     ebs_optimized=None, image_id=None, key_name=None, associate_public_ip_address=None,
                     user_data=None, tags=None, instance_profile_name=None, block_device_mappings=None,
                     group_name=None, create_if_exists=False, termination_policies=None):
        # Pylint thinks this function has too many arguments and too many local variables
        # pylint: disable=R0913, R0914
        # We need unused argument to match method in autoscale
        # pylint: disable=unused-argument
        """Updates an existing elastigroup if it exists,
        otherwise this creates a new elastigroup."""
        group_config = self._create_elastigroup_config(
            hostclass=hostclass,
            availability_vs_cost="balanced",
            desired_size=desired_size,
            min_size=min_size,
            max_size=max_size,
            instance_type=instance_type,
            load_balancers=load_balancers,
            zones=self._create_az_subnets_dict(subnets),
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
            group_name=group_name or self._get_new_groupname(hostclass)
        )
        group = self.get_existing_group(hostclass, group_name)
        if group and not create_if_exists:
            group_id = group['id']
            # Remove fields not allowed in update
            del group_config['group']['capacity']['unit']
            del group_config['group']['compute']['product']
            try:
                self._spotinst_call(path='/' + group_id, data=group_config, method='put')
                return {'name': group['name']}
            except SpotinstException as err:
                logger.info('Unable to update Spotinst group: %s', err.message)
        else:
            try:
                new_group = self._spotinst_call(data=group_config, method='post').json()
                new_group_name = new_group['response']['items'][0]['name']
                return {'name': new_group_name}
            except SpotinstException as err:
                logger.info('Unable to create Spotinst group: %s', err.message)

    def _delete_group(self, group_id, force=False):
        """Delete an elastigroup by group id"""
        # We need argument `force` to match method in autoscale
        # pylint: disable=unused-argument
        try:
            self._spotinst_call(path='/' + group_id, method='delete')
        except SpotinstException as err:
            logger.info('Unable to delete Spotinst group: %s', err.message)

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
            try:
                self._spotinst_call(path='/' + group['id'], data=group_update, method='put')
            except SpotinstException:
                raise

            if wait:
                self.wait_instance_termination(group_name=group_name, group=group, noerror=noerror)

    def _get_group_id_from_instance_id(self, instance_id):
        groups = self.get_existing_groups()
        for group in groups:
            group_id = group['id']
            instance_ids = [instance['instanceId'] for instance in self._get_group_instances(group_id)]
            if instance_id in instance_ids:
                return group_id
