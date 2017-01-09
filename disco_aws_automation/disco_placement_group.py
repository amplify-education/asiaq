"""
Manage Placement Groups
"""
import logging

import boto3
import botocore

from disco_aws_automation.resource_helper import throttled_call


class DiscoPlacementGroup(object):
    """
    A simple class to manage Placement Groups
    """

    def __init__(self, environment_name, ec2=None):
        self.environment_name = environment_name
        self._ec2_client = ec2

    @property
    def ec2_client(self):
        """
        Lazily creates boto3 EC2 Connection
        """
        if not self._ec2_client:
            self._ec2_client = boto3.client('ec2')
        return self._ec2_client

    def get_or_create(self, name):
        """Get a placement group or create it if it doesn't exist"""
        if not self.get(name):
            self.create(name)

        return self.get(name)

    def create(self, name):
        """Create a placement group"""
        group_id = self._get_id(name)

        logging.info("Creating placement group %s", group_id)
        throttled_call(self.ec2_client.create_placement_group, GroupName=group_id, Strategy='cluster')

    def delete(self, name):
        """Delete a placement group by name"""
        group_id = self._get_id(name)

        logging.info("Deleting placement group %s", group_id)
        throttled_call(self.ec2_client.delete_placement_group, GroupName=group_id)

    def get(self, name):
        """Get a placement group or None if it does not exist"""
        group_id = self._get_id(name)

        try:
            response = throttled_call(self.ec2_client.describe_placement_groups, GroupNames=[group_id])
            placement_groups = response.get('PlacementGroups', [])

            return placement_groups[0] if placement_groups else None
        except botocore.exceptions.ClientError:
            return None

    def list(self):
        """Get all placement groups in the current environment"""
        response = throttled_call(self.ec2_client.describe_placement_groups)

        placement_groups = response.get('PlacementGroups', [])

        return [group for group in placement_groups
                if group['GroupName'].startswith(self.environment_name + '-')]

    def delete_all(self):
        """Delete all placement groups in the current environment"""
        for placement_group in self.list():
            group_id = placement_group['GroupName']
            throttled_call(self.ec2_client.delete_placement_group, GroupName=group_id)

    def _get_id(self, name):
        """Get the group name to use with AWS from a user friendly name"""
        return self.environment_name + '-' + name
