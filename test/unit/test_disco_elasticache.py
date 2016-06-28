"""
Tests of disco_elasticache
"""
from unittest import TestCase
from mock import MagicMock, PropertyMock, call
from disco_aws_automation import DiscoElastiCache
from test.helpers.patch_disco_aws import get_mock_config


def _get_mock_vpc():
    vpc = MagicMock()

    disco_subnet = MagicMock()
    disco_subnet.subnet_dict = {'SubnetId': 'fake_subnet'}

    meta_network = MagicMock()
    meta_network.security_group.id = 'fake_security'
    meta_network.disco_subnets = {'zone1': disco_subnet}

    vpc.networks = {
        'intranet': meta_network
    }

    vpc.environment_name = 'unittest'

    return vpc


def _get_mock_aws():
    aws = MagicMock()

    aws.get_default_meta_network.return_value = 'intranet'
    aws.get_default_domain_name.return_value = 'example.com'
    aws.get_default_product_line.return_value = 'example_team'

    return aws


def _get_mock_route53():
    route53 = MagicMock()
    return route53


class MatchAnything(object):
    """Helper class to use with assertions that can match any value"""

    def __eq__(self, other):
        return True


class DiscoElastiCacheTests(TestCase):
    """Test DiscoElastiCache"""

    def setUp(self):
        self.elasticache = DiscoElastiCache(
            vpc=_get_mock_vpc(), aws=_get_mock_aws(), route53=_get_mock_route53())
        self.elasticache.route53 = MagicMock()

        DiscoElastiCache.config = PropertyMock(return_value=get_mock_config({
            'unittest:new-cache': {
                'instance_type': 'cache.m1.small',
                'engine': 'redis',
                'engine_version': '2.8.6',
                'port': '1000',
                'parameter_group': 'default',
                'num_nodes': '5',
                'auto_failover': 'true',
                'maintenance_window': 'sun:10:00-sun:11:00'
            },
            'unittest:snapshot-cache': {
                'instance_type': 'cache.m1.small',
                'engine': 'redis',
                'engine_version': '2.8.6',
                'port': '1000',
                'parameter_group': 'default',
                'num_nodes': '5',
                'auto_failover': 'true',
                'maintenance_window': 'sun:10:00-sun:11:00',
                'snapshot': 'true'
            },
            'unittest:old-cache': {
                'instance_type': 'cache.m1.small',
                'engine': 'redis',
                'engine_version': '2.8.6',
                'port': '1000',
                'parameter_group': 'default',
                'num_nodes': '5',
                'auto_failover': 'true'
            }
        }))
        self.elasticache.conn = MagicMock()

        self.replication_groups = [
            {
                'ReplicationGroupId': self.elasticache._get_redis_replication_group_id('old-cache'),
                'Description': 'unittest-old-cache',
                'Status': 'available',
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'old-cache.example.com'
                    }
                }]
            },
            {
                'ReplicationGroupId': self.elasticache._get_redis_replication_group_id('cache2'),
                'Description': 'unittest-cache2',
                'Status': 'available',
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'cache2.example.com'
                    }
                }]
            },
            {
                'ReplicationGroupId': self.elasticache._get_redis_replication_group_id('cache'),
                'Description': 'unittest2-cache',
                'Status': 'available'
            }
        ]

        def _create_replication_group(**kwargs):
            self.replication_groups.append({
                'ReplicationGroupId': kwargs['ReplicationGroupId'],
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'foo.example.com'
                    }
                }]
            })

        # pylint doesn't like Boto3's argument names
        # pylint: disable=C0103
        def _describe_replication_groups(ReplicationGroupId=None):
            if ReplicationGroupId is None:
                return {
                    'ReplicationGroups': self.replication_groups
                }
            else:
                found_groups = [group for group in self.replication_groups
                                if group['ReplicationGroupId'] == ReplicationGroupId]
                return {
                    'ReplicationGroups': found_groups
                }

        # pylint: disable=C0103
        def _describe_cache_subnet_groups(CacheSubnetGroupName=None):
            if CacheSubnetGroupName:
                return {
                    'CacheSubnetGroups': [{
                        'CacheSubnetGroupName': 'unittest-intranet'
                    }]
                }
            elif CacheSubnetGroupName is None:
                return {
                    'CacheSubnetGroups': [{
                        'CacheSubnetGroupName': 'unittest-intranet'
                    }, {
                        'CacheSubnetGroupName': 'unittest-build'
                    }]
                }

        self.elasticache.conn.describe_replication_groups.side_effect = _describe_replication_groups
        self.elasticache.conn.describe_cache_subnet_groups.side_effect = _describe_cache_subnet_groups
        self.elasticache.conn.create_replication_group.side_effect = _create_replication_group

    def test_list(self):
        """Test getting list of cache clusters"""
        clusters = self.elasticache.list()

        self.assertEquals(len(clusters), 2)

        ids = [cluster['Description'] for cluster in clusters]
        self.assertEquals(set(['unittest-old-cache', 'unittest-cache2']), set(ids))

    def test_create_redis_cluster(self):
        """Test creating a redis cluster"""
        self.elasticache.update('new-cache')

        self.elasticache.conn.create_replication_group.assert_called_once_with(
            AutomaticFailoverEnabled=True,
            CacheNodeType='cache.m1.small',
            CacheParameterGroupName='default',
            CacheSubnetGroupName='unittest-intranet',
            Engine='redis',
            EngineVersion='2.8.6',
            NumCacheClusters=5,
            Port=1000,
            ReplicationGroupDescription='unittest-new-cache',
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('new-cache'),
            SecurityGroupIds=['fake_security'],
            PreferredMaintenanceWindow='sun:10:00-sun:11:00',
            Tags=[{'Key': 'product_line', 'Value': 'example_team'},
                  {'Key': 'owner', 'Value': MatchAnything()},
                  {'Key': 'name', 'Value': 'new-cache'},
                  {'Key': 'environment', 'Value': 'unittest'}]
        )
        subdomain = 'new-cache-unittest.example.com'
        self.elasticache.route53.create_record.assert_called_once_with(
            'example.com', subdomain, 'CNAME', 'foo.example.com'
        )

    def test_modify_redis_cluster(self):
        """Test modifying a redis cluster"""
        self.elasticache.update('old-cache')

        self.elasticache.conn.modify_replication_group.assert_called_once_with(
            ApplyImmediately=True,
            AutomaticFailoverEnabled=True,
            CacheParameterGroupName='default',
            EngineVersion='2.8.6',
            PreferredMaintenanceWindow='sat:05:00-sat:06:00',
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('old-cache')
        )

    def test_update_all(self):
        """Test updating multiple clusters at once"""
        self.elasticache.conn.describe_snapshots.side_effect = self._describe_snapshots

        self.elasticache.update_all()

        self.elasticache.conn.create_replication_group.assert_any_call(
            AutomaticFailoverEnabled=True,
            CacheNodeType='cache.m1.small',
            CacheParameterGroupName='default',
            CacheSubnetGroupName='unittest-intranet',
            Engine='redis',
            EngineVersion='2.8.6',
            NumCacheClusters=5,
            Port=1000,
            ReplicationGroupDescription='unittest-new-cache',
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('new-cache'),
            SecurityGroupIds=['fake_security'],
            PreferredMaintenanceWindow='sun:10:00-sun:11:00',
            Tags=[{'Key': 'product_line', 'Value': 'example_team'},
                  {'Key': 'owner', 'Value': MatchAnything()},
                  {'Key': 'name', 'Value': 'new-cache'},
                  {'Key': 'environment', 'Value': 'unittest'}]
        )

        self.elasticache.conn.create_replication_group.assert_any_call(
            AutomaticFailoverEnabled=True,
            CacheNodeType='cache.m1.small',
            CacheParameterGroupName='default',
            CacheSubnetGroupName='unittest-intranet',
            Engine='redis',
            EngineVersion='2.8.6',
            NumCacheClusters=5,
            Port=1000,
            ReplicationGroupDescription='unittest-snapshot-cache',
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('snapshot-cache'),
            SecurityGroupIds=['fake_security'],
            PreferredMaintenanceWindow='sun:10:00-sun:11:00',
            Tags=[{'Key': 'product_line', 'Value': 'example_team'},
                  {'Key': 'owner', 'Value': MatchAnything()},
                  {'Key': 'name', 'Value': 'snapshot-cache'},
                  {'Key': 'environment', 'Value': 'unittest'}],
            SnapshotName='unittest-snapshot-cache-2'
        )

        self.elasticache.conn.modify_replication_group.assert_called_once_with(
            ApplyImmediately=True,
            AutomaticFailoverEnabled=True,
            CacheParameterGroupName='default',
            EngineVersion='2.8.6',
            PreferredMaintenanceWindow='sat:05:00-sat:06:00',
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('old-cache')
        )

    def test_delete_cache_cluster(self):
        """Test deleting a cache cluster"""
        self.elasticache.delete('old-cache')

        self.elasticache.conn.delete_replication_group.assert_called_once_with(
            ReplicationGroupId=self.elasticache._get_redis_replication_group_id('old-cache')
        )

        self.elasticache.route53.delete_records_by_value.assert_called_once_with(
            'CNAME', 'old-cache.example.com'
        )

    def test_create_subnet_group(self):
        """Test creating a subnet group"""
        self.elasticache._create_subnet_group('intranet')
        self.elasticache.conn.create_cache_subnet_group.assert_called_once_with(
            CacheSubnetGroupDescription='unittest-intranet',
            CacheSubnetGroupName='unittest-intranet',
            SubnetIds=['fake_subnet']
        )

    def test_delete_all_cache_clusters(self):
        """Test deleting all cache clusters in environment"""
        self.elasticache.delete_all_cache_clusters()

        delete_group_calls = [
            call(ReplicationGroupId=self.elasticache._get_redis_replication_group_id('old-cache')),
            call(ReplicationGroupId=self.elasticache._get_redis_replication_group_id('cache2'))
        ]

        self.elasticache.conn.delete_replication_group.assert_has_calls(delete_group_calls, any_order=True)

        delete_dns_calls = [
            call('CNAME', 'old-cache.example.com'),
            call('CNAME', 'cache2.example.com')
        ]

        self.elasticache.route53.delete_records_by_value.assert_has_calls(delete_dns_calls, any_order=True)

    def test_delete_all_subnet_groups(self):
        """Test deleting all subnet groups in environment"""
        self.elasticache.delete_all_subnet_groups()

        delete_group_calls = [
            call(CacheSubnetGroupName='unittest-intranet'),
            call(CacheSubnetGroupName='unittest-build')
        ]

        self.elasticache.conn.delete_cache_subnet_group.assert_has_calls(delete_group_calls, any_order=True)

    def test_get_replication_group_id(self):
        """Test that replication group Ids follow the AWS rules for Ids"""
        group_id = self.elasticache._get_redis_replication_group_id('test-name')

        self.assertLessEqual(16, len(group_id))
        self.assertTrue(group_id[0].isalpha())

    def test_list_snapshots(self):
        """Test getting list of snapshots"""
        self.elasticache.conn.describe_snapshots.side_effect = self._describe_snapshots

        snapshots = self.elasticache.list_snapshots()
        self.assertEquals(len(list(snapshots)), 3)

        snapshots = self.elasticache.list_snapshots('snapshot-cache')
        self.assertEquals(len(list(snapshots)), 2)

    def test_get_latest_snapshot(self):
        """Test finding the latest snapshot"""
        latest_snapshot = self.elasticache.get_latest_snapshot('snapshot-cache')
        self.assertIsNone(latest_snapshot)

        self.elasticache.conn.describe_snapshots.side_effect = self._describe_snapshots
        latest_snapshot = self.elasticache.get_latest_snapshot('snapshot-cache')
        self.assertEquals(latest_snapshot['SnapshotName'], 'unittest-snapshot-cache-2')

    @staticmethod
    def _describe_snapshots():
        from datetime import datetime

        return {
            'Snapshots': [
                {
                    'SnapshotName': 'unittest-snapshot-cache-1',
                    'CacheClusterId': 'clustera',
                    'SnapshotStatus': 'available',
                    'NodeSnapshots': [
                        {
                            'CacheNodeId': '001',
                            'CacheSize': '5 M',
                            'SnapshotCreateTime': datetime(2016, 1, 1)
                        }
                    ]
                }, {
                    'SnapshotName': 'unittest-snapshot-cache-2',
                    'CacheClusterId': 'clustera',
                    'SnapshotStatus': 'available',
                    'NodeSnapshots': [
                        {
                            'CacheNodeId': '001',
                            'CacheSize': '5 M',
                            'SnapshotCreateTime': datetime(2016, 2, 1)
                        }
                    ]
                }, {
                    'SnapshotName': 'unittest-snapshot-other-cache-1',
                    'CacheClusterId': 'clusterb',
                    'SnapshotStatus': 'available',
                    'NodeSnapshots': [
                        {
                            'CacheNodeId': '001',
                            'CacheSize': '6 M',
                            'SnapshotCreateTime': datetime(2016, 3, 1)
                        }
                    ]
                }
            ]
        }
