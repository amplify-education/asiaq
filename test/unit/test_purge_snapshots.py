"""Tests disco_purge_snapshots"""
from unittest import TestCase

import datetime
import random
import sys
import pytz
from mock import patch, MagicMock

from bin.disco_purge_snapshots import run

# the current date to use for tests
NOW_MOCK = datetime.datetime(2016, 1, 14, 0, 0, 0, tzinfo=pytz.UTC)


class DiscoPurgeSnapshotsTest(TestCase):
    """Test disco_purge_snapshots"""
    def setUp(self):
        self.snapshots = [
            self._create_mock_snap('2016-01-01T00:00:00.000Z', hostclass='mhcfoo', env='ci'),
            self._create_mock_snap('2016-01-02T00:00:00.000Z', hostclass='mhcfoo', env='ci'),
            self._create_mock_snap('2016-01-03T00:00:00.000Z', hostclass='mhcfoo', env='ci'),
            self._create_mock_snap('2016-01-14T00:00:00.000Z',
                                   description='Created by CreateImage(i-8364e044) for ami-12345678'),
            self._create_mock_snap('2016-01-01T00:00:00.000Z',
                                   description='Created by CreateImage(i-8364e044) for ami-abcdef12')
        ]

    def _get_mock_ec2_conn(self):
        mock = MagicMock()
        mock.get_all_snapshots.return_value = self.snapshots
        mock.get_all_images.return_value = [self._create_ami_mock('ami-abcdef12')]

        return mock

    def _create_mock_snap(self, create_time, image_id=None, hostclass=None, env=None, description=None):
        mock = MagicMock()
        mock.start_time = create_time
        mock.tags = {}
        mock.id = 'snap-' + str(random.randrange(0, 9999999))
        mock.description = description or ''
        if image_id:
            mock.description = 'Created by CreateImage for %s' % image_id

        if hostclass:
            mock.tags['hostclass'] = hostclass

        if env:
            mock.tags['env'] = env
        return mock

    def _create_ami_mock(self, ami_id):
        mock = MagicMock()
        mock.id = ami_id

        return mock

    @patch('bin.disco_purge_snapshots.NOW', NOW_MOCK)
    def test_purge_with_keep_days_and_old(self):
        """Test that --keep-days overrides --old"""
        with patch('boto.connect_ec2', return_value=self._get_mock_ec2_conn()):
            sys.argv = ['disco_purge_snapshots.py', '--old', '--keep-days', '11']
            run()
            self.assertEquals(1, self.snapshots[0].delete.call_count)
            self.assertEquals(1, self.snapshots[1].delete.call_count)
            self.assertEquals(0, self.snapshots[2].delete.call_count)
            self.assertEquals(0, self.snapshots[3].delete.call_count)
            self.assertEquals(0, self.snapshots[4].delete.call_count)

    @patch('bin.disco_purge_snapshots.NOW', NOW_MOCK)
    def test_purge_with_keep_days(self):
        """Test purging snapshots by date"""
        with patch('boto.connect_ec2', return_value=self._get_mock_ec2_conn()):
            sys.argv = ['disco_purge_snapshots.py', '--keep-days', '11']
            run()
            self.assertEquals(1, self.snapshots[0].delete.call_count)
            self.assertEquals(1, self.snapshots[1].delete.call_count)
            self.assertEquals(0, self.snapshots[2].delete.call_count)
            self.assertEquals(0, self.snapshots[3].delete.call_count)
            self.assertEquals(0, self.snapshots[4].delete.call_count)

    @patch('bin.disco_purge_snapshots.NOW', NOW_MOCK)
    def test_purge_with_keep_num(self):
        """Test purging snapshots by date but keeping a set number of them"""
        with patch('boto.connect_ec2', return_value=self._get_mock_ec2_conn()):
            sys.argv = ['disco_purge_snapshots.py', '--keep-days', '11', '--keep-num', '2']
            run()
            self.assertEquals(1, self.snapshots[0].delete.call_count)
            self.assertEquals(0, self.snapshots[1].delete.call_count)
            self.assertEquals(0, self.snapshots[2].delete.call_count)
            self.assertEquals(0, self.snapshots[3].delete.call_count)
            self.assertEquals(0, self.snapshots[4].delete.call_count)

    def test_purge_stray_ami(self):
        """Test purging stray ami snapshots"""
        with patch('boto.connect_ec2', return_value=self._get_mock_ec2_conn()):
            sys.argv = ['disco_purge_snapshots.py', '--stray-ami']
            run()
            self.assertEquals(0, self.snapshots[0].delete.call_count)
            self.assertEquals(0, self.snapshots[1].delete.call_count)
            self.assertEquals(0, self.snapshots[2].delete.call_count)
            self.assertEquals(1, self.snapshots[3].delete.call_count)
            self.assertEquals(0, self.snapshots[4].delete.call_count)
