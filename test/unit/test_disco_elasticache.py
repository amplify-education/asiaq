"""
Tests of disco_elasticache
"""
from unittest import TestCase
from mock import MagicMock, PropertyMock, call
from disco_aws_automation import DiscoElastiCache
from test.helpers.matchers import MatchAnything
from test.helpers.patch_disco_aws import get_mock_config


def _get_mock_vpc():
    vpc = MagicMock()

    subnet = MagicMock()
    subnet.id = 'fake_subnet'

    meta_network = MagicMock()
    meta_network.security_group.id = 'fake_security'
    meta_network.subnets = [subnet]

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


class DiscoElastiCacheTests(TestCase):
    """Test DiscoElastiCache"""

    def setUp(self):
        self.elasticache = DiscoElastiCache(
            vpc=_get_mock_vpc(), aws=_get_mock_aws(), route53=_get_mock_route53())
        self.elasticache.route53 = MagicMock()

        type(self.elasticache).config = PropertyMock(return_value=get_mock_config({
            'unittest:new-cache': {
                'instance_type': 'cache.m1.small',
                'engine': 'redis',
                'engine_version': '2.8.6',
                'port': '1000',
                'parameter_group': 'default',
                'num_nodes': '5',
                'auto_failover': 'true'
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

        self.replication_groups = {
            'unittest-old-cache': {
                'ReplicationGroupId': 'unittest-old-cache',
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'old-cache.example.com'
                    }
                }]
            },
            'unittest-cache2': {
                'ReplicationGroupId': 'unittest-cache2',
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'cache2.example.com'
                    }
                }]
            },
            'unittest2-cache': {
                'ReplicationGroupId': 'unittest2-cache'
            }
        }

        def _create_replication_group(**kwargs):
            self.replication_groups[kwargs['ReplicationGroupId']] = {
                'ReplicationGroupId': kwargs['ReplicationGroupId'],
                'NodeGroups': [{
                    'PrimaryEndpoint': {
                        'Address': 'foo.example.com'
                    }
                }]
            }

        # pylint doesn't like Boto3's argument names
        # pylint: disable=C0103
        def _describe_replication_groups(ReplicationGroupId=None):
            if ReplicationGroupId in self.replication_groups.keys():
                return {
                    'ReplicationGroups': [self.replication_groups[ReplicationGroupId]]
                }
            elif ReplicationGroupId is None:
                return {
                    'ReplicationGroups': self.replication_groups.values()
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

        ids = [cluster['ReplicationGroupId'] for cluster in clusters]
        self.assertEquals(set(['unittest-old-cache', 'unittest-cache2']), set(ids))

    def test_create_redis_cluster(self):
        """Test modifying a redis cluster"""
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
            ReplicationGroupId='unittest-new-cache',
            SecurityGroupIds=['fake_security'],
            Tags=[{'Value': 'example_team', 'Key': 'product_line'},
                  {'Value': MatchAnything(), 'Key': 'owner'},
                  {'Value': 'new-cache', 'Key': 'name'}]
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
            ReplicationGroupId='unittest-old-cache'
        )

    def test_update_all(self):
        """Test updating multiple clusters at once"""
        self.elasticache.update_all()

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
            ReplicationGroupId='unittest-new-cache',
            SecurityGroupIds=['fake_security'],
            Tags=[{'Value': 'example_team', 'Key': 'product_line'},
                  {'Value': MatchAnything(), 'Key': 'owner'},
                  {'Value': 'new-cache', 'Key': 'name'}]
        )

        self.elasticache.conn.modify_replication_group.assert_called_once_with(
            ApplyImmediately=True,
            AutomaticFailoverEnabled=True,
            CacheParameterGroupName='default',
            EngineVersion='2.8.6',
            ReplicationGroupId='unittest-old-cache'
        )

    def test_delete_cache_cluster(self):
        """Test deleting a cache cluster"""
        self.elasticache.delete('old-cache')

        self.elasticache.conn.delete_replication_group.assert_called_once_with(
            ReplicationGroupId='unittest-old-cache'
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
            call(ReplicationGroupId='unittest-old-cache'),
            call(ReplicationGroupId='unittest-cache2')
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
