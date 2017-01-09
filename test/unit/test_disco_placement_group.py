"""Tests of disco_placement_group"""

from unittest import TestCase

from mock import MagicMock

from disco_aws_automation.disco_placement_group import DiscoPlacementGroup


class DiscoPlacementGroupTests(TestCase):
    """Test DiscoPlacementGroup"""

    def setUp(self):
        ec2 = MagicMock()

        self.placement_groups = {}

        ec2.create_placement_group.side_effect = self._create_placement_group
        ec2.delete_placement_group.side_effect = self._delete_placement_group
        ec2.describe_placement_groups.side_effect = self._describe_placement_groups

        self.placement = DiscoPlacementGroup(environment_name='unittestenv', ec2=ec2)

    # pylint is displeased with boto3 argument names
    # pylint: disable=invalid-name
    def _create_placement_group(self, GroupName=None, Strategy=None):
        self.placement_groups[GroupName] = {
            'GroupName': GroupName,
            'Strategy': Strategy,
            'State': 'available'
        }

    # pylint: disable=invalid-name
    def _delete_placement_group(self, GroupName):
        self.placement_groups.pop(GroupName)

    # pylint: disable=invalid-name
    def _describe_placement_groups(self, GroupNames=None):
        if not GroupNames:
            groups = self.placement_groups.values()
        else:
            groups = [self.placement_groups[key] for key in self.placement_groups.keys()
                      if key in GroupNames]

        return {'PlacementGroups': groups}

    def test_get(self):
        """Test getting a placement group by its user friendly name"""
        group = self.placement.get('group1')

        self.assertIsNone(group)

        self._create_placement_group('unittestenv-group1')

        group = self.placement.get('group1')

        self.assertEquals('unittestenv-group1', group['GroupName'])

    def test_get_or_create(self):
        """Test that a placement group is only created if it doesn't already exist"""

        self.placement.get_or_create("group1")
        self.placement.get_or_create("group1")
        self.assertEquals(1, len(self.placement_groups))

    def test_delete(self):
        """Test deleting a placement group"""
        self._create_placement_group('unittestenv-group1')

        self.placement.delete("group1")
        self.assertEquals(0, len(self.placement_groups))

    def test_delete_all(self):
        """Test deleting all of the placement groups for an environment"""
        self._create_placement_group('unittestenv-group1')
        self._create_placement_group('unittestenv-group2')
        self._create_placement_group('otherenv-group1')

        self.placement.delete_all()

        self.assertTrue('otherenv-group1' in self.placement_groups)
        self.assertEquals(1, len(self.placement_groups))

    def test_list(self):
        """Test getting all of the placement groups for an environment"""
        self._create_placement_group('unittestenv-group1')
        self._create_placement_group('otherenv-group1')

        groups = self.placement.list()

        self.assertEquals(1, len(groups))
        self.assertEquals('unittestenv-group1', groups[0]['GroupName'])
