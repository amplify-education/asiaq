"""Tests of disco_elb"""
from unittest import TestCase
from mock import MagicMock
from moto import mock_elb
from disco_aws_automation import DiscoELB, CommandError

TEST_ENV_NAME = 'unittestenv'
TEST_HOSTCLASS = 'mhcunit'
TEST_VPC_ID = 'vpc-56e10e3d'  # the hard coded VPC Id that moto will always return
TEST_DOMAIN_NAME = 'test.example.com'
TEST_CERTIFICATE_ARN_ACM = "arn:aws:acm::123:blah"
TEST_CERTIFICATE_ARN_IAM = "arn:aws:acm::123:blah"
# With these constants, you could do some significant testing of setting and clearing stickiness policies,
# were it not for the fact that moto's ELB support is insufficient for the task.
MOCK_POLICY_NAME = "mock-sticky-policy"
MOCK_APP_STICKY_POLICY = {
    u'PolicyAttributeDescriptions': [{u'AttributeName': 'CookieName', u'AttributeValue': 'JSESSIONID'}],
    u'PolicyName': MOCK_POLICY_NAME,
    u'PolicyTypeName': 'AppCookieStickinessPolicyType'
}

MOCK_ELB_STICKY_POLICY = {
    u'PolicyAttributeDescriptions': [{u'AttributeName': 'CookieExpirationPeriod', u'AttributeValue': '0'}],
    u'PolicyName': MOCK_POLICY_NAME,
    u'PolicyTypeName': 'LBCookieStickinessPolicyType'
}


def _get_vpc_mock():
    vpc_mock = MagicMock()
    vpc_mock.environment_name = TEST_ENV_NAME
    vpc_mock.get_vpc_id.return_value = TEST_VPC_ID
    return vpc_mock


class DiscoELBTests(TestCase):
    """Test DiscoELB"""

    def setUp(self):
        self.route53 = MagicMock()
        self.acm = MagicMock()
        self.iam = MagicMock()
        self.disco_elb = DiscoELB(_get_vpc_mock(), route53=self.route53, acm=self.acm, iam=self.iam)
        self.acm.get_certificate_arn.return_value = TEST_CERTIFICATE_ARN_ACM
        self.iam.get_certificate_arn.return_value = TEST_CERTIFICATE_ARN_IAM

    # pylint: disable=too-many-arguments
    def _create_elb(self, hostclass=None, public=False, tls=False,
                    instance_protocol='HTTP', instance_port=80,
                    elb_protocols='HTTP', elb_ports='80',
                    idle_timeout=None, connection_draining_timeout=None,
                    sticky_app_cookie=None, existing_cookie_policy=None):
        sticky_policies = [existing_cookie_policy] if existing_cookie_policy else []
        mock_describe = MagicMock(return_value={'PolicyDescriptions': sticky_policies})
        self.disco_elb.elb_client.describe_load_balancer_policies = mock_describe

        return self.disco_elb.get_or_create_elb(
            hostclass=hostclass or TEST_HOSTCLASS,
            security_groups=['sec-1'],
            subnets=[],
            hosted_zone_name=TEST_DOMAIN_NAME,
            health_check_url="/" if instance_protocol.upper() in ('HTTP', 'HTTPS') else "",
            instance_protocol=instance_protocol,
            instance_port=instance_port,
            elb_protocols="HTTPS" if tls else elb_protocols,
            elb_ports='443' if tls else elb_ports,
            elb_public=public,
            sticky_app_cookie=sticky_app_cookie,
            idle_timeout=idle_timeout,
            connection_draining_timeout=connection_draining_timeout)

    @mock_elb
    def test_get_certificate_arn_prefers_acm(self):
        '''get_certificate_arn() prefers an ACM provided certificate'''
        self.assertEqual(self.disco_elb.get_certificate_arn("dummy"), TEST_CERTIFICATE_ARN_ACM)

    @mock_elb
    def test_get_certificate_arn_fallback_to_iam(self):
        '''get_certificate_arn() uses an IAM certificate if no ACM cert available'''
        self.acm.get_certificate_arn.return_value = None
        self.assertEqual(self.disco_elb.get_certificate_arn("dummy"), TEST_CERTIFICATE_ARN_IAM)

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
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Scheme='internal',
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_internal_no_tls(self):
        """Test creation an internal private ELB"""
        self.acm.get_certificate_arn.return_value = None
        self.iam.get_certificate_arn.return_value = None
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb()
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Scheme='internal',
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_external(self):
        """Test creation a publically accessible ELB"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(public=True)
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_with_tls(self):
        """Test creation an ELB with TLS"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(tls=True)
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'HTTPS',
                'LoadBalancerPort': 443,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': TEST_CERTIFICATE_ARN_ACM
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Scheme='internal',
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_with_tcp(self):
        """Test creation an ELB with TCP"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(instance_protocol='TCP', instance_port=25,
                         elb_protocols='TCP', elb_ports=25)
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'TCP',
                'LoadBalancerPort': 25,
                'InstanceProtocol': 'TCP',
                'InstancePort': 25
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Scheme='internal',
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_with_multiple_ports(self):
        """Test creating an ELB that listens on multiple ports"""
        elb_client = self.disco_elb.elb_client
        elb_client.create_load_balancer = MagicMock(wraps=elb_client.create_load_balancer)
        self._create_elb(instance_protocol='HTTP', instance_port=80,
                         elb_protocols='HTTP, HTTPS', elb_ports='80, 443')
        elb_client.create_load_balancer.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
            Listeners=[{
                'Protocol': 'HTTP',
                'LoadBalancerPort': 80,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80
            }, {
                'Protocol': 'HTTPS',
                'LoadBalancerPort': 443,
                'InstanceProtocol': 'HTTP',
                'InstancePort': 80,
                'SSLCertificateId': TEST_CERTIFICATE_ARN_ACM
            }],
            Subnets=[],
            SecurityGroups=['sec-1'],
            Scheme='internal',
            Tags=[
                {"Key": "hostclass", "Value": 'mhcunit'},
                {"Key": "environment", "Value": 'unittestenv'},
                {"Key": "is_testing", "Value": '0'}
            ])

    @mock_elb
    def test_get_elb_mismatched_ports_protocols(self):
        """Test that creating an ELB fails when using a different number of ELB ports and protocols"""
        self.assertRaises(CommandError,
                          self._create_elb,
                          elb_protocols='HTTP, HTTPS',
                          elb_ports='80')

    @mock_elb
    def test_get_elb_with_idle_timeout(self):
        """Test creating an ELB with an idle timeout"""
        client = self.disco_elb.elb_client
        client.modify_load_balancer_attributes = MagicMock(wraps=client.modify_load_balancer_attributes)

        self._create_elb(idle_timeout=100)

        client.modify_load_balancer_attributes.assert_called_once_with(
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
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
            LoadBalancerName=DiscoELB.get_elb_id('unittestenv', 'mhcunit'),
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
    def test_wait_for_instance_health(self):
        """Test that we can wait for instances attached to an ELB to enter a specific state"""
        self._create_elb(hostclass='mhcbar')
        elb_id = self.disco_elb.get_elb_id(TEST_ENV_NAME, 'mhcbar')
        instances = [{"InstanceId": "i-123123aa"}]
        self.disco_elb.elb_client.register_instances_with_load_balancer(LoadBalancerName=elb_id,
                                                                        Instances=instances)
        self.disco_elb.wait_for_instance_health_state(hostclass='mhcbar')
