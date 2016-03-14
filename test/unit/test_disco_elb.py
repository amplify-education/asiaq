"""Tests of disco_elb"""
from unittest import TestCase
from mock import MagicMock
from moto import mock_elb
from disco_aws_automation import DiscoELB

TEST_ENV_NAME = 'unittestenv'
TEST_HOSTCLASS = 'mhcunit'
TEST_VPC_ID = 'vpc-56e10e3d'  # the hard coded VPC Id that moto will always return
TEST_DOMAIN_NAME = 'test.example.com'


def _get_vpc_mock():
    vpc_mock = MagicMock()
    vpc_mock.environment_name = TEST_ENV_NAME
    vpc_mock.vpc = MagicMock()
    vpc_mock.vpc.id = TEST_VPC_ID
    return vpc_mock


class DiscoELBTests(TestCase):
    """Test DiscoELB"""

    def setUp(self):
        self.disco_elb = DiscoELB(_get_vpc_mock(), route53=MagicMock(), acm=MagicMock(), iam=MagicMock())
        self.disco_elb.acm.get_certificate_arn = MagicMock(return_value="arn:aws:acm::123:blah")
        self.disco_elb.iam.get_certificate_arn = MagicMock(return_value="arn:aws:iam::123:blah")

    def _create_elb(self, hostclass=None, public=False, tls=False,
                    idle_timeout=None, connection_draining_timeout=None,
                    sticky_app_cookie=None):
        return self.disco_elb.get_or_create_elb(
            hostclass=hostclass or TEST_HOSTCLASS,
            security_groups=['sec-1'],
            subnets=['sub-1'],
            hosted_zone_name=TEST_DOMAIN_NAME,
            health_check_url="/",
            instance_protocol="HTTP",
            instance_port=80,
            elb_protocol="HTTPS" if tls else "HTTP",
            elb_port=443 if tls else 80,
            elb_public=public,
            sticky_app_cookie=sticky_app_cookie,
            idle_timeout=idle_timeout,
            connection_draining_timeout=connection_draining_timeout,
            tags={'tag_key': 'tag_value'}
        )

    @mock_elb
    def test_get_certificate_arn_prefers_acm(self):
        '''get_certificate_arn() prefers an ACM provided certificate'''
        self.assertEqual(self.disco_elb.get_certificate_arn("dummy"), "arn:aws:acm::123:blah")

    @mock_elb
    def test_get_certificate_arn_fallback_to_iam(self):
        '''get_certificate_arn() uses an IAM certificate if no ACM cert available'''
        self.disco_elb.acm.get_certificate_arn = MagicMock(return_value=None)
        self.assertEqual(self.disco_elb.get_certificate_arn("dummy"), "arn:aws:iam::123:blah")

    @mock_elb
    def test_get_cname(self):
        '''Make sure get_cname returns what we expect'''
        self.assertEqual(self.disco_elb.get_cname(TEST_HOSTCLASS, TEST_DOMAIN_NAME),
                         "mhcunit-unittestenv.test.example.com")

    @mock_elb
    def test_get_elb_with_create(self):
        """Test creating a ELB"""
        self._create_elb()
        self.assertEquals(
            len(self.disco_elb.elb_client.describe_load_balancers()['LoadBalancerDescriptions']), 1)

    @mock_elb
    def test_get_elb_with_update(self):
        """Updating an ELB doesn't add create a new ELB"""
        self._create_elb()
        self._create_elb()
        self.assertEquals(
            len(self.disco_elb.elb_client.describe_load_balancers()['LoadBalancerDescriptions']), 1)

    @mock_elb
    def test_get_elb_internal(self):
        """Test creation an internal private ELB"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb()
        self.disco_elb.elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': 'arn:aws:acm::123:blah'
            }],
            Subnets=['sub-1'],
            SecurityGroups=['sec-1'],
            Scheme='internal')

    @mock_elb
    def test_get_elb_internal_no_tls(self):
        """Test creation an internal private ELB"""
        self.disco_elb.acm.get_certificate_arn = MagicMock(return_value=None)
        self.disco_elb.iam.get_certificate_arn = MagicMock(return_value=None)
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb()
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': ''
            }],
            Subnets=['sub-1'],
            SecurityGroups=['sec-1'],
            Scheme='internal')

    @mock_elb
    def test_get_elb_external(self):
        """Test creation a publically accessible ELB"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(public=True)
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': 'arn:aws:acm::123:blah'
            }],
            Subnets=['sub-1'],
            SecurityGroups=['sec-1'])

    @mock_elb
    def test_get_elb_with_tls(self):
        """Test creation an ELB with TLS"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(tls=True)
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            Listeners=[{
                'Protocol': 'HTTPS',
                'LoadBalancerPort': 443,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': 'arn:aws:acm::123:blah'
            }],
            Subnets=['sub-1'],
            SecurityGroups=['sec-1'],
            Scheme='internal')

    @mock_elb
    def test_get_elb_with_idle_timeout(self):
        """Test creating an ELB with an idle timeout"""
        client = self.disco_elb.elb_client
        client.modify_load_balancer_attributes = MagicMock(wraps=client.modify_load_balancer_attributes)

        self._create_elb(idle_timeout=100)

        client.modify_load_balancer_attributes.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            LoadBalancerAttributes={'ConnectionDraining': {'Enabled': False, 'Timeout': 0},
                                    'ConnectionSettings': {'IdleTimeout': 100}}
        )

    @mock_elb
    def test_get_elb_with_connection_draining(self):
        """Test creating ELB with connection draining"""
        client = self.disco_elb.elb_client
        client.modify_load_balancer_attributes = MagicMock(wraps=client.modify_load_balancer_attributes)

        self._create_elb(connection_draining_timeout=100)

        client.modify_load_balancer_attributes.assert_called_once_with(
            LoadBalancerName='unittestenv-mhcunit',
            LoadBalancerAttributes={'ConnectionDraining': {'Enabled': True, 'Timeout': 100}}
        )

    @mock_elb
    def test_delete_elb(self):
        """Test deleting an ELB"""
        self._create_elb()
        self.disco_elb.delete_elb(TEST_HOSTCLASS)
        load_balancers = self.disco_elb.elb_client.describe_load_balancers()['LoadBalancerDescriptions']
        self.assertEquals(len(load_balancers), 0)

    @mock_elb
    def test_get_existing_elb(self):
        """Test get_elb for a hostclass"""
        self._create_elb()
        self.assertIsNotNone(self.disco_elb.get_elb(TEST_HOSTCLASS))

    @mock_elb
    def test_list(self):
        """Test getting the list of ELBs"""
        self._create_elb(hostclass='mhcbar')
        self._create_elb(hostclass='mhcfoo')
        self.assertEquals(len(self.disco_elb.list()), 2)

    @mock_elb
    def test_elb_delete(self):
        """Test deletion of ELBs"""
        self._create_elb(hostclass='mhcbar')
        self.disco_elb.delete_elb(hostclass='mhcbar')
        self.assertEquals(len(self.disco_elb.list()), 0)

    @mock_elb
    def test_destroy_all_elbs(self):
        """Test deletion of all ELBs"""
        self._create_elb(hostclass='mhcbar')
        self._create_elb(hostclass='mhcfoo')
        self.disco_elb.destroy_all_elbs()
        self.assertEquals(len(self.disco_elb.list()), 0)

    @mock_elb
    def test_tagging_elb(self):
        """Test tagging an ELB"""
        client = self.disco_elb.elb_client
        client.add_tags = MagicMock(wraps=client.add_tags)

        self._create_elb()

        client.add_tags.assert_called_once_with(LoadBalancerNames=['unittestenv-mhcunit'],
                                                Tags=[{'Value': 'tag_value', 'Key': 'tag_key'}])
