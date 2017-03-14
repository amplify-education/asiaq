"""Base Group (abstract)"""
import logging

from abc import ABCMeta, abstractmethod

from botocore.exceptions import WaiterError
import boto3

from .resource_helper import throttled_call

logger = logging.getLogger(__name__)


class BaseGroup(object):
    """Abstract class definition for AWS groups"""
    __metaclass__ = ABCMeta

    def __init__(self):
        self._boto3_ec = None

    @abstractmethod
    def get_existing_group(self, hostclass, group_name, throw_on_two_groups):
        """Get list of group objects for a hostclass"""
        return

    @abstractmethod
    def get_existing_groups(self, hostclass, group_name):
        """Get list of all group objects for a hostclass"""
        return

    @abstractmethod
    def list_groups(self):
        """Returns list of objects for display purposes for all groups"""
        return

    @abstractmethod
    def get_instances(self, hostclass=None, group_name=None):
        """Get list of instances in groups"""
        return

    @abstractmethod
    def delete_groups(self, hostclass, group_name, force):
        """Delete groups of a hostclass"""
        return

    @abstractmethod
    def scaledown_groups(self, hostclass, group_name, wait, noerror):
        """Scale down number of instances in a group"""
        return

    @property
    def boto3_ec(self):
        """Lazily create boto3 ec2 connection"""
        if not self._boto3_ec:
            self._boto3_ec = boto3.client('ec2')
        return self._boto3_ec

    def wait_instance_termination(self, group_name=None, group=None, noerror=False):
        """Wait for instance to be terminated during scaledown"""
        waiter = throttled_call(self.boto3_ec.get_waiter, 'instance_terminated')
        instance_ids = [inst['id'] for inst in self.get_instances(group_name=group_name)]

        try:
            logger.info("Waiting for scaledown of group %s", group['name'])
            waiter.wait(InstanceIds=instance_ids)
        except WaiterError:
            if noerror:
                logger.exception("Unable to wait for scaling down of %s", group_name)
                return False
            else:
                raise

        return True
