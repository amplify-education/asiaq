"""Contains DiscoAutoscale class that orchestrates AWS Autoscaling"""
import logging
import random
import time

import boto
import boto.ec2
import boto.ec2.autoscale
import boto.ec2.autoscale.launchconfig
import boto.ec2.autoscale.group
from boto.exception import BotoServerError
import boto3

from .base_group import BaseGroup
from .resource_helper import throttled_call, get_boto3_paged_results, tag2dict
from .exceptions import TooManyAutoscalingGroups

logger = logging.getLogger(__name__)


DEFAULT_TERMINATION_POLICIES = ["OldestLaunchConfiguration"]


class DiscoAutoscale(BaseGroup):
    """Class orchestrating autoscaling"""

    def __init__(self, environment_name, autoscaling_connection=None, boto3_autoscaling_connection=None):
        self.environment_name = environment_name
        self._connection = autoscaling_connection or None  # lazily initialized
        self._boto3_autoscale = boto3_autoscaling_connection or None  # lazily initialized
        super(DiscoAutoscale, self).__init__()

    @property
    def connection(self):
        """Lazily create boto autoscaling connection"""
        if not self._connection:
            self._connection = boto.ec2.autoscale.AutoScaleConnection(use_block_device_types=True)
        return self._connection

    @property
    def boto3_autoscale(self):
        """Lazily create boto3 autoscaling connection"""
        if not self._boto3_autoscale:
            self._boto3_autoscale = boto3.client('autoscaling')
        return self._boto3_autoscale

    def get_new_groupname(self, hostclass):
        """Returns a new autoscaling group name when given a hostclass"""
        return self.environment_name + '_' + hostclass + "_" + str(int(time.time()))

    def get_launch_config_name(self, hostclass):
        """Create new launchconfig group name"""
        return '{0}_{1}_{2}'.format(self.environment_name, hostclass, str(random.randrange(0, 9999999)))

    def _filter_by_environment(self, items):
        """Filters launch configs by environment"""
        for item in items:
            try:
                if item.name.startswith("{0}_".format(self.environment_name)):
                    yield item
            except AttributeError:
                logger.warning("Skipping unparseable item=%s", vars(item))

    def _filter_autoscale_by_environment(self, items):
        """Filters autoscaling groups by environment ONLY BOTO3"""
        for item in items:
            try:
                if item['AutoScalingGroupName'].startswith("{0}_".format(self.environment_name)):
                    yield item
            except AttributeError:
                logger.warning("Skipping unparseable item=%s", vars(item))

    def _filter_instance_by_environment(self, items):
        """Filter instances by environment via their group_name"""
        for item in items:
            try:
                if item.group_name.startswith("{0}_".format(self.environment_name)):
                    yield item
            except AttributeError:
                logger.warning("Skipping unparseable item=%s", vars(item))

    def _get_hostclass(self, groupname):
        """Returns the hostclass when given an autoscaling group name"""
        # group names follow a <env>_hostclass_<id> pattern. hostclass names could have underscores
        # so we need to be careful about how we split out the hostclass name
        parts = groupname.split('_')[1:-1]
        return '_'.join(parts)

    def _get_group_generator(self, group_names=None):
        """Yields groups in current environment"""
        if group_names:
            groups = get_boto3_paged_results(
                self.boto3_autoscale.describe_auto_scaling_groups,
                results_key='AutoScalingGroups',
                next_token_key='NextToken',
                AutoScalingGroupNames=group_names
            )
        else:
            groups = get_boto3_paged_results(
                self.boto3_autoscale.describe_auto_scaling_groups,
                results_key='AutoScalingGroups',
                next_token_key='NextToken'
            )

        for group in self._filter_autoscale_by_environment(groups):
            yield {
                'name': group.get('AutoScalingGroupName'),
                'min_size': group.get('MinSize'),
                'max_size': group.get('MaxSize'),
                'desired_capacity': group.get('DesiredCapacity'),
                'launch_config_name': group.get('LaunchConfigurationName'),
                'termination_policies': group.get('TerminationPolicies'),
                'vpc_zone_identifier': group.get('VPCZoneIdentifier'),
                'load_balancers': group.get('LoadBalancerNames'),
                'target_groups': group.get('TargetGroupARNs'),
                'type': 'asg',
                'tags': tag2dict(group.get('Tags'))
            }

    def _get_instance_generator(self, hostclass=None, group_name=None):
        """Yields autoscaled instances in current environment"""
        next_token = None
        while True:
            instances = throttled_call(
                self.connection.get_all_autoscaling_instances, next_token=next_token)
            for instance in self._filter_instance_by_environment(instances):
                filters = [
                    not hostclass or self._get_hostclass(instance.group_name) == hostclass,
                    not group_name or instance.group_name == group_name]
                if all(filters):
                    yield {
                        'instance_id': instance.instance_id,
                        'group_name': instance.group_name
                    }
            next_token = instances.next_token
            if not next_token:
                break

    def get_instances(self, hostclass=None, group_name=None):
        """Returns autoscaled instances in the current environment"""
        return list(self._get_instance_generator(hostclass=hostclass, group_name=group_name))

    def _get_config_generator(self, names=None):
        """Yields Launch Configurations in current environment"""
        next_token = None
        while True:
            configs = throttled_call(self.connection.get_all_launch_configurations,
                                     names=names, next_token=next_token)
            for config in self._filter_by_environment(configs):
                yield config
            next_token = configs.next_token
            if not next_token:
                break

    def get_configs(self, names=None):
        """Returns Launch Configurations in current environment"""
        return list(self._get_config_generator(names=names))

    def get_config(self, *args, **kwargs):
        """Returns a new launch configuration"""
        config = boto.ec2.autoscale.launchconfig.LaunchConfiguration(
            connection=self.connection, *args, **kwargs
        )
        throttled_call(self.connection.create_launch_configuration, config)
        return config

    def delete_config(self, config_name):
        """Delete a specific Launch Configuration"""
        throttled_call(self.connection.delete_launch_configuration, config_name)
        logger.info("Deleting launch configuration %s", config_name)

    def clean_configs(self):
        """Delete unused Launch Configurations in current environment"""
        logger.info("Cleaning up unused launch configurations in %s", self.environment_name)
        for config in self._get_config_generator():
            try:
                self.delete_config(config.name)
            except BotoServerError:
                pass

    def delete_groups(self, hostclass=None, group_name=None, force=False):
        """
        Delete autoscaling groups, filtering on either hostclass or the group_name.

        If force is True, autoscaling groups will be forcibly destroyed, even if they are currently in use.
        Defaults to False.
        """
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            try:
                logger.info("Deleting group %s", group['name'])
                throttled_call(
                    self.boto3_autoscale.delete_auto_scaling_group,
                    AutoScalingGroupName=group['name'],
                    ForceDelete=force
                )

                self.delete_config(group['launch_config_name'])
            except BotoServerError as exc:
                logger.info("Unable to delete group %s due to: %s. Force delete is set to %s",
                            group['name'], exc.message, force)

    def scaledown_groups(self, hostclass=None, group_name=None, wait=False, noerror=False):
        """
        Scales down number of instances in a hostclass's autoscaling group, or the given autoscaling group,
        to zero. If wait is true, this function will block until all instances are terminated, or it will
        raise a WaiterError if this process times out, unless noerror is True.

        Returns true if the autoscaling groups were successfully scaled down, False otherwise.
        """
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            logger.info("Scaling down group %s", group['name'])
            throttled_call(
                self.boto3_autoscale.update_auto_scaling_group,
                AutoScalingGroupName=group['name'],
                MaxSize=0,
                MinSize=0,
                DesiredCapacity=0
            )

            if wait:
                self.wait_instance_termination(group_name=group_name, group=group, noerror=noerror)

    @staticmethod
    def create_autoscale_tags(group_name, tags):
        """Given a python dictionary return list of boto autoscale Tag objects"""
        return [boto.ec2.autoscale.Tag(key=key, value=value, resource_id=group_name, propagate_at_launch=True)
                for key, value in tags.iteritems()] if tags else None

    def modify_group(self, group, launch_config, vpc_zone_id=None,
                     min_size=None, max_size=None, desired_size=None,
                     termination_policies=None, tags=None,
                     load_balancers=None, target_groups=None):
        """Update an existing autoscaling group"""
        # pylint: disable=R0913
        group['launch_config_name'] = launch_config
        changes = {'LaunchConfigurationName': launch_config}
        if vpc_zone_id:
            group['vpc_zone_identifier'] = vpc_zone_id
            changes['VPCZoneIdentifier'] = vpc_zone_id
        if min_size is not None:
            group['min_size'] = min_size
            changes['MinSize'] = min_size
        if max_size is not None:
            group['max_size'] = max_size
            changes['MaxSize'] = max_size
        if desired_size is not None:
            group['desired_capacity'] = desired_size
            changes['DesiredCapacity'] = desired_size
        if termination_policies:
            group['termination_policies'] = termination_policies
            changes['TerminationPolicies'] = termination_policies

        throttled_call(
            self.boto3_autoscale.update_auto_scaling_group,
            AutoScalingGroupName=group['name'],
            **changes
        )

        if tags:
            throttled_call(
                self.connection.create_or_update_tags,
                DiscoAutoscale.create_autoscale_tags(group['name'], tags)
            )
        if target_groups:
            self.update_tg(target_groups=target_groups, group_name=group['name'])

        if load_balancers:
            self.update_elb(elb_names=load_balancers, group_name=group['name'])

        return group

    def create_group(self, hostclass, launch_config, vpc_zone_id,
                     min_size=None, max_size=None, desired_size=None,
                     termination_policies=None, tags=None,
                     load_balancers=None, target_groups=None):
        """
        Create an autoscaling group.

        The group must not already exist. Use get_group() instead if you want to update a group if it
        exits or create it if it does not.
        """
        # pylint: disable=R0913, R0914
        _min_size = min_size or 0
        _max_size = max([min_size, max_size, desired_size, 0])
        _desired_capacity = desired_size or max_size
        termination_policies = termination_policies or DEFAULT_TERMINATION_POLICIES
        group_name = self.get_new_groupname(hostclass)
        boto2_group = boto.ec2.autoscale.group.AutoScalingGroup(
            connection=self.connection,
            name=group_name,
            launch_config=launch_config,
            load_balancers=load_balancers,
            default_cooldown=None,
            health_check_type=None,
            health_check_period=None,
            placement_group=None,
            vpc_zone_identifier=vpc_zone_id,
            desired_capacity=_desired_capacity,
            min_size=_min_size,
            max_size=_max_size,
            tags=DiscoAutoscale.create_autoscale_tags(group_name, tags),
            termination_policies=termination_policies,
            instance_id=None
        )
        throttled_call(self.connection.create_auto_scaling_group, boto2_group)

        if target_groups:
            self.update_tg(target_groups=target_groups, group_name=boto2_group.name)

        return {
            'name': boto2_group.name,
            'min_size': boto2_group.min_size,
            'max_size': boto2_group.max_size,
            'desired_capacity': boto2_group.desired_capacity,
            'launch_config_name': boto2_group.launch_config_name,
            'termination_policies': boto2_group.termination_policies,
            'vpc_zone_identifier': boto2_group.vpc_zone_identifier,
            'load_balancers': boto2_group.load_balancers
        }

    # pylint: disable=too-many-arguments
    def get_group(self, hostclass, launch_config, vpc_zone_id=None,
                  min_size=None, max_size=None, desired_size=None,
                  termination_policies=None, tags=None,
                  load_balancers=None, target_groups=None, create_if_exists=False,
                  group_name=None):
        """
        Returns autoscaling group.
        This updates an existing autoscaling group if it exists,
        otherwise this creates a new autoscaling group.

        NOTE: Deleting tags is not currently supported.
        """
        # Check if an autoscaling group already exists.
        existing_group = self.get_existing_group(hostclass=hostclass, group_name=group_name)
        if create_if_exists or not existing_group:
            group = self.create_group(
                hostclass=hostclass, launch_config=launch_config, vpc_zone_id=vpc_zone_id,
                min_size=min_size, max_size=max_size, desired_size=desired_size,
                termination_policies=termination_policies, tags=tags, load_balancers=load_balancers,
                target_groups=target_groups)
        else:
            group = self.modify_group(
                group=existing_group, launch_config=launch_config,
                vpc_zone_id=vpc_zone_id, min_size=min_size, max_size=max_size, desired_size=desired_size,
                termination_policies=termination_policies, tags=tags, load_balancers=load_balancers,
                target_groups=target_groups)

        # Create default scaling policies
        self.create_policy(
            group_name=group['name'],
            policy_name='up',
            policy_type='SimpleScaling',
            adjustment_type='PercentChangeInCapacity',
            scaling_adjustment='10',
            min_adjustment_magnitude='1'
        )
        self.create_policy(
            group_name=group['name'],
            policy_name='down',
            policy_type='SimpleScaling',
            adjustment_type='PercentChangeInCapacity',
            scaling_adjustment='-10',
            min_adjustment_magnitude='1'
        )
        return group

    def create_or_update_group(self, hostclass, desired_size=None, min_size=None, max_size=None,
                               instance_type=None, load_balancers=None, target_groups=None, subnets=None,
                               security_groups=None, instance_monitoring=None, ebs_optimized=None,
                               image_id=None, key_name=None, associate_public_ip_address=None, user_data=None,
                               tags=None, instance_profile_name=None, block_device_mappings=None,
                               group_name=None, create_if_exists=False, termination_policies=None,
                               spotinst=False, spotinst_reserve=None):
        """
        Create a new autoscaling group or update an existing one
        """
        # Pylint thinks this function has too many arguments and too many local variables
        # We need unused argument to match method in autoscale
        # pylint: disable=R0913, R0914
        # pylint: disable=unused-argument
        if spotinst:
            raise Exception('DiscoAutoscale cannot be used to create SpotInst groups')

        launch_config = self.get_config(
            name=self.get_launch_config_name(hostclass),
            image_id=image_id,
            key_name=key_name,
            security_groups=security_groups,
            block_device_mappings=block_device_mappings,
            instance_type=instance_type.split(':')[0],
            instance_monitoring=instance_monitoring,
            instance_profile_name=instance_profile_name,
            ebs_optimized=ebs_optimized,
            user_data=user_data,
            associate_public_ip_address=associate_public_ip_address
        )

        group = self.get_group(
            hostclass=hostclass,
            launch_config=launch_config.name,
            vpc_zone_id=",".join([subnet['SubnetId'] for subnet in subnets]),
            min_size=min_size,
            max_size=max_size,
            desired_size=desired_size,
            termination_policies=termination_policies,
            tags=tags,
            load_balancers=load_balancers,
            target_groups=target_groups,
            create_if_exists=create_if_exists,
            group_name=group_name
        )

        return {'name': group['name']}

    def get_existing_groups(self, hostclass=None, group_name=None):
        """
        Returns all autoscaling groups for a given hostclass, sorted by most recent creation. If no
        autoscaling groups can be found, returns an empty list.
        """
        groups = list(self._get_group_generator(group_names=[group_name]))
        filtered_groups = [group for group in groups
                           if not hostclass or self._get_hostclass(group['name']) == hostclass]
        filtered_groups.sort(key=lambda grp: grp['name'], reverse=True)
        return filtered_groups

    def get_existing_group(self, hostclass=None, group_name=None, throw_on_two_groups=True):
        """
        Returns the autoscaling group object for the given hostclass or group name, or None if no autoscaling
        group exists.

        If two or more autoscaling groups exist for a hostclass, then this method will throw an exception,
        unless 'throw_on_two_groups' is False. Then if there are two groups the most recently created
        autoscaling group will be returned. If there are more than two autoscaling groups, this method will
        always throw an exception.
        """
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        if not groups:
            return None
        elif len(groups) == 1 or (len(groups) == 2 and not throw_on_two_groups):
            return groups[0]
        else:
            raise TooManyAutoscalingGroups("There are too many autoscaling groups for {}.".format(hostclass))

    def list_groups(self):
        """Returns list of objects for display purposes for all groups"""
        groups = self.get_existing_groups()
        instances = self.get_instances()
        grp_list = []
        for group in groups:
            launch_cfg = list(self.get_configs(names=[group['launch_config_name']]))
            grp_dict = {
                'name': group['name'].ljust(35 + len(self.environment_name)),
                'image_id': launch_cfg[0].image_id if launch_cfg else '',
                'group_cnt': len([instance for instance in instances
                                  if instance['group_name'] == group['name']]),
                'min_size': group['min_size'],
                'desired_capacity': group['desired_capacity'],
                'max_size': group['max_size'],
                'type': group['type'],
                'tags': group['tags']
            }
            grp_list.append(grp_dict)
        return grp_list

    def terminate(self, instance_id, decrement_capacity=True):
        """
        Terminates an instance using the autoscaling API.

        When decrement_capacity is True this allows us to avoid
        autoscaling immediately replacing a terminated instance.
        """
        throttled_call(self.connection.terminate_instance,
                       instance_id, decrement_capacity=decrement_capacity)

    def get_launch_configs(self, hostclass=None, group_name=None):
        """Returns all launch configurations for a hostclass if any exist, None otherwise"""
        group_list = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        if group_list:
            return self.get_configs(names=[
                group['launch_config_name']
                for group in group_list
                if group['launch_config_name']
            ])
        return None

    def get_launch_config(self, hostclass=None, group_name=None):
        """Return launch config info for a hostclass, None otherwise"""
        config = self._get_launch_config(hostclass=hostclass, group_name=group_name)

        if config:
            return {
                'instance_type': config.instance_type
            }

        return None

    def _get_launch_config(self, hostclass=None, group_name=None):
        config_list = self.get_launch_configs(hostclass=hostclass, group_name=group_name)
        return config_list[0] if config_list else None

    def list_policies(self, group_name=None, policy_types=None, policy_names=None):
        """Returns all autoscaling policies"""
        arguments = {}
        if group_name:
            arguments["AutoScalingGroupName"] = group_name
        if policy_types:
            arguments["PolicyTypes"] = policy_types
        if policy_names:
            arguments["PolicyNames"] = policy_names

        results = throttled_call(self.boto3_autoscale.describe_policies, **arguments)
        next_token = results.get('NextToken')

        # Next token will be an empty string if we're done
        while next_token:
            # Get additional results
            arguments['NextToken'] = next_token
            additional_results = throttled_call(self.boto3_autoscale.describe_policies, **arguments)

            # Extend the existing results and setup the next token for another iteration
            results['ScalingPolicies'] += additional_results['ScalingPolicies']
            next_token = additional_results.get('NextToken')

        policies = []

        for result in results['ScalingPolicies']:
            group_name = result['AutoScalingGroupName']
            if group_name.startswith(self.environment_name):
                # The usage of 'or' is because those keys are present but sometimes contain empty values, so
                # its needed to use 'or' to make sure that those empty values become our conventionally
                # accepted '-' empty values.
                policies.append({
                    'ASG': group_name,
                    'Name': result['PolicyName'],
                    'Type': result['PolicyType'],
                    'Adjustment Type': result['AdjustmentType'],
                    'Scaling Adjustment': result.get('ScalingAdjustment'),
                    'Step Adjustments': result.get('StepAdjustments'),
                    'Min Adjustment': result.get('MinAdjustmentMagnitude'),
                    'Cooldown': result.get('Cooldown'),
                    'Warmup': result.get('EstimatedInstanceWarmup'),
                    'Alarms': result.get('Alarms')
                })

        return policies

    def create_policy(
            self,
            group_name,
            policy_name,
            policy_type="SimpleScaling",
            adjustment_type=None,
            min_adjustment_magnitude=None,
            scaling_adjustment=None,
            cooldown=600,
            metric_aggregation_type=None,
            step_adjustments=None,
            estimated_instance_warmup=None
    ):
        """
        Creates a new autoscaling policy, or updates an existing one if the autoscaling group name and
        policy name already exist. Handles the logic of constructing the correct autoscaling policy request,
        because not all parameters are required.
        """
        arguments = {
            "AutoScalingGroupName": group_name,
            "PolicyName": policy_name,
            "PolicyType": policy_type,
            "AdjustmentType": adjustment_type
        }

        if policy_type == "SimpleScaling":
            arguments["ScalingAdjustment"] = int(scaling_adjustment)
            arguments["Cooldown"] = int(cooldown)
            # Special case here, where if we just blindly add min adjustment magnitude we actually get errors
            # from boto3 unless the adjustment type is as such.
            if adjustment_type == "PercentChangeInCapacity":
                arguments["MinAdjustmentMagnitude"] = int(min_adjustment_magnitude)
        elif policy_type == "StepScaling":
            arguments["MetricAggregationType"] = metric_aggregation_type
            arguments["StepAdjustments"] = step_adjustments
            arguments["EstimatedInstanceWarmup"] = int(estimated_instance_warmup)

        logger.info(
            "Creating autoscaling policy '%s' in autoscaling group '%s'",
            policy_name,
            group_name
        )

        logger.debug("Autoscaling policy parameters: %s", arguments)

        return throttled_call(self.boto3_autoscale.put_scaling_policy, **arguments)

    def delete_policy(self, policy_name, group_name):
        """Deletes an autoscaling policy"""
        return throttled_call(
            self.boto3_autoscale.delete_policy,
            PolicyName=policy_name,
            AutoScalingGroupName=group_name
        )

    def delete_all_recurring_group_actions(self, hostclass=None, group_name=None):
        """Deletes all recurring scheduled actions for a hostclass"""
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            actions = throttled_call(self.connection.get_all_scheduled_actions, as_group=group['name'])
            recurring_actions = [action for action in actions if action.recurrence is not None]
            if recurring_actions:
                logger.info("Deleting scheduled actions for autoscaling group %s", group['name'])
                for action in recurring_actions:
                    throttled_call(
                        self.connection.delete_scheduled_action,
                        scheduled_action_name=action.name,
                        autoscale_group=group['name']
                    )

    def create_recurring_group_action(self, recurrance, min_size=None, desired_capacity=None, max_size=None,
                                      hostclass=None, group_name=None):
        """Creates a recurring scheduled action for a hostclass"""
        groups = self.get_existing_groups(hostclass=hostclass, group_name=group_name)
        for group in groups:
            action_name = "{0}_{1}".format(group['name'], recurrance.replace('*', 'star').replace(' ', '_'))
            logger.info("Creating scheduled action %s", action_name)
            throttled_call(self.connection.create_scheduled_group_action,
                           as_group=group['name'], name=action_name,
                           min_size=min_size,
                           desired_capacity=desired_capacity,
                           max_size=max_size,
                           recurrence=recurrance)

    @staticmethod
    def _get_snapshot_dev(launch_config, hostclass):
        """Returns the snapshot device config"""
        snapshot_devs = [key for key, value in launch_config.block_device_mappings.iteritems()
                         if value.snapshot_id]
        if not snapshot_devs:
            raise Exception("Hostclass {0} does not mount a snapshot".format(hostclass))
        elif len(snapshot_devs) > 1:
            raise Exception("Unsupported configuration: hostclass {0} has multiple snapshot based devices."
                            .format(hostclass))
        return snapshot_devs[0]

    def _create_new_launchconfig(self, hostclass, launch_config):
        """Creates a launch configuration"""
        return self.get_config(
            name='{0}_{1}_{2}'.format(self.environment_name, hostclass, str(random.randrange(0, 9999999))),
            image_id=launch_config.image_id,
            key_name=launch_config.key_name,
            security_groups=launch_config.security_groups,
            block_device_mappings=[launch_config.block_device_mappings],
            instance_type=launch_config.instance_type,
            instance_monitoring=launch_config.instance_monitoring,
            instance_profile_name=launch_config.instance_profile_name,
            ebs_optimized=launch_config.ebs_optimized,
            user_data=launch_config.user_data,
            associate_public_ip_address=launch_config.associate_public_ip_address)

    def update_snapshot(self, snapshot_id, snapshot_size, hostclass=None, group_name=None):
        """Updates all of a hostclasses existing autoscaling groups to use a different snapshot"""
        launch_config = self._get_launch_config(hostclass=hostclass, group_name=group_name)
        if not launch_config:
            raise Exception("Can't locate hostclass {0}".format(hostclass or group_name))
        snapshot_bdm = launch_config.block_device_mappings[
            DiscoAutoscale._get_snapshot_dev(launch_config, hostclass)]
        if snapshot_bdm.snapshot_id != snapshot_id:
            old_snapshot_id = snapshot_bdm.snapshot_id
            snapshot_bdm.snapshot_id = snapshot_id
            snapshot_bdm.size = snapshot_size
            self.modify_group(self.get_existing_group(hostclass=hostclass, group_name=group_name),
                              self._create_new_launchconfig(hostclass, launch_config).name)
            logger.info(
                "Updating %s group's snapshot from %s to %s", hostclass or group_name, old_snapshot_id,
                snapshot_id)
        else:
            logger.debug(
                "Autoscaling group %s is already referencing latest snapshot %s", hostclass or group_name,
                snapshot_id)

    def update_elb(self, elb_names, hostclass=None, group_name=None):
        """Updates an existing autoscaling group to use a different set of load balancers"""
        group = self.get_existing_group(hostclass=hostclass, group_name=group_name)

        if not group:
            logger.warning("Auto Scaling group %s does not exist. Cannot change %s ELB(s)",
                           hostclass or group_name, ', '.join(elb_names))
            return (set(), set())

        new_lbs = set(elb_names) - set(group['load_balancers'])
        extras = set(group['load_balancers']) - set(elb_names)
        if new_lbs or extras:
            logger.info("Updating ELBs for group %s from [%s] to [%s]",
                        group['name'], ", ".join(group['load_balancers']), ", ".join(elb_names))
        if new_lbs:
            throttled_call(self.boto3_autoscale.attach_load_balancers,
                           AutoScalingGroupName=group['name'],
                           LoadBalancerNames=list(new_lbs))
        if extras:
            throttled_call(self.boto3_autoscale.detach_load_balancers,
                           AutoScalingGroupName=group['name'],
                           LoadBalancerNames=list(extras))
        return (new_lbs, extras)

    def update_tg(self, target_groups, hostclass=None, group_name=None):
        """Updates an existing autoscaling group to use a different set of target_groups"""
        group = self.get_existing_group(hostclass=hostclass, group_name=group_name)
        if not group:
            logger.warning("Auto Scaling group %s does not exist. Cannot change %s Target Groups(s)",
                           hostclass or group_name, ', '.join(target_groups))
            return (set(), set())
        new_tgs = set(target_groups) - set(group['target_groups'])
        extras = set(group['target_groups']) - set(target_groups)

        if new_tgs or extras:
            logger.info("Updating Target Groups for group %s from [%s] to [%s]",
                        group['name'], ", ".join(group['target_groups']), ", ".join(target_groups))
        if new_tgs:
            throttled_call(self.boto3_autoscale.attach_load_balancer_target_groups,
                           AutoScalingGroupName=group['name'],
                           TargetGroupARNs=list(new_tgs))
        if extras:
            throttled_call(self.boto3_autoscale.detach_load_balancer_target_groups,
                           AutoScalingGroupName=group['name'],
                           TargetGroupARNs=list(extras))

        return (new_tgs, extras)
