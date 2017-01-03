"""
Tests of disco_aws
"""
from unittest import TestCase
import random

import dateutil.parser as dateparser
import boto3
from mock import MagicMock
from moto import mock_ec2

from disco_aws_automation import DiscoStorage


class DiscoStorageTests(TestCase):
    """Test DiscoStorage class"""

    def setUp(self):
        self.storage = DiscoStorage(environment_name='unittestenv')

    def _create_snapshot(self, hostclass, env, encrypted=True):
        client = boto3.client('ec2')
        volume = client.create_volume(
            Size=100,
            AvailabilityZone='fake-zone-1',
            Encrypted=encrypted
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

    def mock_snap(self, hostclass, when=None):
        """Creates MagicMock for a snapshot"""
        ret = MagicMock()
        ret.tags = {"hostclass": hostclass}
        ret.start_time = when if when else dateparser.parse("2016-01-19 16:38:48+00:00")
        ret.id = 'snap-' + str(random.randrange(0, 9999999))
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
        self._create_snapshot('foo', 'encryptedenv', True)

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
        self._create_snapshot('foo', 'encryptedenv', True)
        self._create_snapshot('foo', 'encryptedenv', True)
        self._create_snapshot('foo', 'encryptedenv', True)

        self.storage.cleanup_ebs_snapshots(keep_last_n=2)

        self.assertEquals(2, len(self.storage.get_snapshots()))
        self.assertEquals(3, len(DiscoStorage(environment_name='otherenv').get_snapshots()))

    @mock_ec2
    def test_create_ebs_snapshot(self):
        """Test creating a snapshot (encrypted by default)"""
        self.storage.create_ebs_snapshot('mhcfoo', 250, 'mock_productline')

        snapshots = self.storage.get_snapshots('mhcfoo')

        self.assertEquals(250, snapshots[0].volume_size)
        self.assertEquals(True, snapshots[0].encrypted)
        self.assertEquals('mock_productline', snapshots[0].tags['productline'])

    @mock_ec2
    def test_create_ebs_snapshot_unencrypted(self):
        """Test creating an unencrypted snapshot"""
        self.storage.create_ebs_snapshot('mhcfoo', 250, 'mock_productline', False)

        snapshots = self.storage.get_snapshots('mhcfoo')

        self.assertEquals(250, snapshots[0].volume_size)
        self.assertEquals(False, snapshots[0].encrypted)
        self.assertEquals('mock_productline', snapshots[0].tags['productline'])

    @mock_ec2
    def test_take_snapshot(self):
        """Test taking a snapshot of an attached volume"""
        client = boto3.client('ec2')
        ec2 = boto3.resource('ec2')
        instance = ec2.create_instances(ImageId='mock_image_id',
                                        MinCount=1,
                                        MaxCount=1)[0]
        client.create_tags(Resources=[instance.instance_id],
                           Tags=[{'Key': 'environment',
                                  'Value': 'unittestenv'},
                                 {'Key': 'hostclass',
                                  'Value': 'mhcmock'},
                                 {'Key': 'productline',
                                  'Value': 'mock_productline'}])

        volume = client.create_volume(
            Size=100,
            AvailabilityZone='fake-zone-1'
        )
        client.attach_volume(
            VolumeId=volume['VolumeId'],
            InstanceId=instance.instance_id,
            Device='/dev/sdb'
        )

        snapshot_id = self.storage.take_snapshot(volume_id=volume['VolumeId'])

        snapshots = self.storage.get_snapshots('mhcmock')
        self.assertEquals(len(snapshots), 1)
        self.assertEquals(snapshots[0].id, snapshot_id)
        self.assertEquals(snapshots[0].volume_size, 100)
        self.assertEquals(snapshots[0].tags, {'env': 'unittestenv',
                                              'hostclass': 'mhcmock',
                                              'productline': 'mock_productline'})
