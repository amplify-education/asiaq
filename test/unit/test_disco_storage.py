"""
Tests of disco_aws
"""
from unittest import TestCase
import random

import dateutil.parser as dateparser
import boto3
from mock import MagicMock, call
from moto import mock_ec2

from disco_aws_automation import DiscoStorage


class DiscoStorageTests(TestCase):
    """Test DiscoStorage class"""

    def setUp(self):
        self.storage = DiscoStorage(environment_name='unittestenv')

    def _create_snapshot(self, hostclass, env):
        client = boto3.client('ec2')
        volume = client.create_volume(
            Size=100,
            AvailabilityZone='fake-zone-1'
        )

        snapshot = client.create_snapshot(VolumeId=volume['VolumeId'])

        client.create_tags(Resources=[snapshot['SnapshotId']],
                           Tags=[{'Key': 'hostclass', 'Value': hostclass},
                                 {'Key': 'env', 'Value': env}])

        return snapshot

    def test_is_ebs_optimized(self):
        """is_ebs_optimized works"""
        self.assertTrue(self.storage.is_ebs_optimized("m4.xlarge"))
        self.assertFalse(self.storage.is_ebs_optimized("t2.micro"))

    @mock_ec2
    def test_get_latest_snapshot_no_snap(self):
        """get_latest_snapshot() returns None if no snapshots exist for hostclass"""
        self.assertIsNone(self.storage.get_latest_snapshot("mhcfoo"))

    def mock_snap(self, hostclass, when=None, snap_id=None):
        """Creates MagicMock for a snapshot"""
        ret = MagicMock()
        ret.tags = {"hostclass": hostclass}
        ret.start_time = when if when else dateparser.parse("2016-01-19 16:38:48+00:00")
        ret.id = snap_id if snap_id else 'snap-' + str(random.randrange(0, 9999999))
        ret.volume_size = random.randrange(1, 9999)
        return ret

    def test_get_latest_snapshot_with_snaps(self):
        """get_latest_snapshot() returns correct snapshot if many exist"""
        snap_list = [
            self.mock_snap("mhcfoo", dateparser.parse("2016-01-15 16:38:48+00:00")),
            self.mock_snap("mhcfoo", dateparser.parse("2016-01-19 16:38:48+00:00")),
            self.mock_snap("mhcfoo", dateparser.parse("2016-01-17 16:38:48+00:00"))]
        self.storage.connection.get_all_snapshots = MagicMock(return_value=snap_list)
        self.assertEqual(self.storage.get_latest_snapshot("mhcfoo"), snap_list[1])

    def test_create_snapshot_bdm_syntax(self):
        """create_snapshot_bdm() calls functions with correct syntax"""
        dev = self.storage.create_snapshot_bdm(self.mock_snap("mhcbar"), 5)
        self.assertEqual(dev.iops, 5)

    @mock_ec2
    def test_get_all_snapshots(self):
        """Test getting all of the snapshots for an environment"""
        self._create_snapshot('foo', 'unittestenv')
        self._create_snapshot('foo', 'otherenv')

        self.assertEquals(1, len(self.storage.get_snapshots()))

    @mock_ec2
    def test_delete_snapshot(self):
        """Test deleting a snapshot"""
        snapshot = self._create_snapshot('foo', 'unittestenv')
        self.storage.delete_snapshot(snapshot['SnapshotId'])

        self.assertEquals(0, len(self.storage.get_snapshots()))

        snapshot = self._create_snapshot('foo', 'otherenv')
        self.storage.delete_snapshot(snapshot['SnapshotId'])
        self.assertEquals(1, len(DiscoStorage(environment_name='otherenv').get_snapshots()))

    @mock_ec2
    def test_cleanup_ebs_snapshots(self):
        """Test deleting old snapshots"""
        self._create_snapshot('foo', 'unittestenv')
        self._create_snapshot('foo', 'unittestenv')
        self._create_snapshot('foo', 'unittestenv')
        self._create_snapshot('foo', 'otherenv')
        self._create_snapshot('foo', 'otherenv')
        self._create_snapshot('foo', 'otherenv')

        self.storage.cleanup_ebs_snapshots(keep_last_n=2)

        self.assertEquals(2, len(self.storage.get_snapshots()))
        self.assertEquals(3, len(DiscoStorage(environment_name='otherenv').get_snapshots()))

    @mock_ec2
    def test_clean_up_ebs_snapshots_by_date(self):
        """Test deleting snapshots older than a certain day"""
        snap_list = [
            self.mock_snap("mhcfoo", "2016-01-01 00:00:00+00:00", "snap1"),
            self.mock_snap("mhcfoo", "2016-01-07 00:00:00+00:00", "snap2"),
            self.mock_snap("mhcfoo", "2016-01-14 00:00:00+00:00", "snap3")]

        self.storage.connection.get_all_snapshots = MagicMock(return_value=snap_list)
        self.storage._get_date_now = MagicMock(return_value=dateparser.parse("2016-01-14 00:00:00+00:00"))
        self.storage.delete_snapshot = MagicMock()

        self.storage.cleanup_ebs_snapshots(keep_last_n=1, keep_last_days=7)

        self.storage.delete_snapshot.assert_has_calls([call('snap1')], any_order=True)
        self.assertEquals(1, self.storage.delete_snapshot.call_count)

    @mock_ec2
    def test_create_ebs_snapshot(self):
        """Test creating a snapshot"""
        self.storage.create_ebs_snapshot('mhcfoo', 250)

        snapshots = self.storage.get_snapshots('mhcfoo')

        self.assertEquals(250, snapshots[0].volume_size)
