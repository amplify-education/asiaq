"""Contains DiscoGroup class that is above all other group classes used"""
import logging

from .base_group import BaseGroup
from .disco_autoscale import DiscoAutoscale
from .disco_elastigroup import DiscoElastigroup

logger = logging.getLogger(__name__)


class DiscoGroup(BaseGroup):
    """Implementation of DiscoGroup regardless of the type of group"""

    def __init__(self, environment_name):
        """Implementation of BaseGroup in AWS"""
        self.environment_name = environment_name
        self._autoscale = DiscoAutoscale(environment_name=self.environment_name)
        self._elastigroup = DiscoElastigroup(environment_name=self.environment_name)
        super(DiscoGroup, self).__init__()

    def get_existing_group(self, hostclass=None, group_name=None, throw_on_two_groups=True):
        asg_group = self._autoscale.get_existing_group(
            hostclass=hostclass,
            group_name=group_name,
            throw_on_two_groups=throw_on_two_groups
        )
        spot_group = self._elastigroup.get_existing_group(
            hostclass=hostclass,
            group_name=group_name,
            throw_on_two_groups=throw_on_two_groups
        )
        if asg_group and spot_group:
            return sorted([asg_group.__dict__, spot_group], key=lambda grp: grp['name'], reverse=True)[0]
        elif asg_group:
            return asg_group.__dict__
        elif spot_group:
            return spot_group
        else:
            logger.info('No group found')

    def get_existing_groups(self, hostclass=None, group_name=None):
        asg_groups = self._autoscale.get_existing_groups()
        asg_groups = [group.__dict__ for group in asg_groups]
        spot_groups = self._elastigroup.get_existing_groups()
        return asg_groups + spot_groups

    def get_instances(self, hostclass=None, group_name=None):
        asg_instances = self._autoscale.get_instances(hostclass=hostclass, group_name=group_name)
        asg_instances = [instance.__dict__ for instance in asg_instances]
        spot_instances = self._elastigroup.get_instances(hostclass=hostclass, group_name=group_name)
        return asg_instances + spot_instances

    def delete_groups(self, hostclass=None, group_name=None, force=False):
        self._autoscale.delete_groups(hostclass=hostclass, group_name=group_name, force=force)
        self._elastigroup.delete_groups(hostclass=hostclass, group_name=group_name, force=force)

    def scaledown_groups(self, hostclass=None, group_name=None, wait=False, noerror=False):
        self._autoscale.scaledown_groups(
            hostclass=hostclass,
            group_name=group_name,
            wait=wait,
            noerror=noerror
        )
        self._elastigroup.scaledown_groups(
            hostclass=hostclass,
            group_name=group_name,
            wait=wait,
            noerror=noerror
        )
