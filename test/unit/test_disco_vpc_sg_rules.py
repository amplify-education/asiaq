"""Tests of disco_vpc_sg_rules"""

import unittest
from netaddr import IPNetwork
from mock import MagicMock, call, patch, PropertyMock

from disco_aws_automation.disco_vpc import DiscoVPC
from disco_aws_automation.disco_vpc_sg_rules import DiscoVPCSecurityGroupRules

from test.helpers.patch_disco_aws import TEST_ENV_NAME, get_mock_config


# pylint: disable=unused-argument
@patch('disco_aws_automation.disco_vpc.DiscoRDS.update_all_clusters_in_vpc')
@patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
@patch('disco_aws_automation.disco_vpc.DiscoSNS')
@patch('disco_aws_automation.disco_vpc.DiscoVPCGateways')
@patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
@patch('boto3.client')
@patch('boto3.resource')
@patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
@patch('disco_aws_automation.disco_vpc.get_random_free_subnet')
def _get_vpc_mock(random_subnet_mock=None, meta_network_mock=None, boto3_resource_mock=None,
                  boto3_client_mock=None, config_mock=None,
                  gateways_mock=None, sns_mock=None, endpoints_mock=None, rds_mock=None):

    config_mock.return_value = get_mock_config({
        'envtype:auto-vpc-type': {
            'ip_space': '10.0.0.0/24',
            'vpc_cidr_size': '26',
            'intranet_cidr': 'auto',
            'tunnel_cidr': 'auto',
            'dmz_cidr': 'auto',
            'maintenance_cidr': 'auto',
            'ntp_server': '10.0.0.5'
        }
    })

    # pylint: disable=C0103
    def _create_vpc_mock(CidrBlock):
        return {'Vpc': {'CidrBlock': CidrBlock,
                        'VpcId': 'mock_vpc_id',
                        'DhcpOptionsId': 'mock_dhcp_options_id'}}

    random_subnet_mock.return_value = IPNetwork('10.0.0.0/26')

    client_mock = MagicMock()
    client_mock.create_vpc.side_effect = _create_vpc_mock
    client_mock.describe_dhcp_options.return_value = {'DhcpOptions': [MagicMock()]}
    boto3_client_mock.return_value = client_mock

    ret = DiscoVPC(TEST_ENV_NAME, 'auto-vpc-type')
    return ret


def _create_network_mock(name, security_group_id):

    def _create_sg_rule_tuple_mock(protocol, ports, sg_source_id=None, cidr_source=None):
        return security_group_id, protocol, ports[0], ports[1], sg_source_id, cidr_source

    security_group_mock = MagicMock()
    security_group_mock.id = security_group_id

    network_mock = MagicMock()
    network_mock.name = name
    network_mock.security_group = security_group_mock
    network_mock.create_sg_rule_tuple = _create_sg_rule_tuple_mock

    return network_mock


class DiscoVPCSecurityGroupRulesTests(unittest.TestCase):
    """Test DiscoVPCSecurityGroupRules"""

    def setUp(self):
        self.mock_vpc = _get_vpc_mock()
        self.disco_vpc_sg_rules = DiscoVPCSecurityGroupRules(self.mock_vpc, self.mock_vpc.boto3_ec2)

    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.networks', new_callable=PropertyMock)
    def test_update_meta_network_sg_rules(self, networks_mock, config_mock):
        """ Verify creating all new security group rules """
        config_mock.return_value = get_mock_config({
            'envtype:auto-vpc-type': {
                'intranet_sg_rules': 'tcp all 0:65535, udp intranet 0:65535, tcp dmz 2181',
                'tunnel_sg_rules': 'tcp all 25 80 443, tcp maintenance 22, udp all 123',
                'dmz_sg_rules': 'tcp maintenance 22 3212, tcp 66.104.227.162/32 80 443, '
                                'tcp 38.117.159.162/32 80 443, tcp 64.106.168.244/32 80 443',
                'maintenance_sg_rules': 'tcp maintenance 22, tcp 66.104.227.162/32 0:65535, '
                                        'tcp 38.117.159.162/32 0:65535',
                'customer_ports': '80 443',
                'customer_cidr': '0.0.0.0/0'
            }
        })

        mock_intranet = _create_network_mock('intranet', 'intranet_sg')
        mock_dmz = _create_network_mock('dmz', 'dmz_sg')
        mock_tunnel = _create_network_mock('tunnel', 'tunnel_sg')
        mock_maintenance = _create_network_mock('maintenance', 'maintenance_sg')

        networks_mock.return_value = {mock_intranet.name: mock_intranet,
                                      mock_dmz.name: mock_dmz,
                                      mock_tunnel.name: mock_tunnel,
                                      mock_maintenance.name: mock_maintenance}
        self.disco_vpc_sg_rules.update_meta_network_sg_rules()

        expected_intranet_sg_rules = [
            (mock_intranet.security_group.id, 'tcp', 0, 65535, mock_tunnel.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 0, 65535, mock_intranet.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 0, 65535, mock_dmz.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 0, 65535, mock_maintenance.security_group.id, None),
            (mock_intranet.security_group.id, 'udp', 0, 65535, mock_intranet.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 2181, 2181, mock_dmz.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 80, 80, mock_dmz.security_group.id, None),
            (mock_intranet.security_group.id, 'tcp', 443, 443, mock_dmz.security_group.id, None),
            (mock_intranet.security_group.id, 'icmp', -1, -1, None, '10.0.0.0/26'),
            (mock_intranet.security_group.id, 'udp', 53, 53, None, '10.0.0.0/26')]
        mock_intranet.update_sg_rules.assert_called_once_with(expected_intranet_sg_rules, False)

        expected_tunnel_sg_rules = [
            (mock_tunnel.security_group.id, 'tcp', 25, 25, mock_tunnel.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 25, 25, mock_intranet.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 25, 25, mock_dmz.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 25, 25, mock_maintenance.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 80, 80, mock_tunnel.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 80, 80, mock_intranet.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 80, 80, mock_dmz.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 80, 80, mock_maintenance.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 443, 443, mock_tunnel.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 443, 443, mock_intranet.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 443, 443, mock_dmz.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 443, 443, mock_maintenance.security_group.id, None),
            (mock_tunnel.security_group.id, 'tcp', 22, 22, mock_maintenance.security_group.id, None),
            (mock_tunnel.security_group.id, 'udp', 123, 123, mock_tunnel.security_group.id, None),
            (mock_tunnel.security_group.id, 'udp', 123, 123, mock_intranet.security_group.id, None),
            (mock_tunnel.security_group.id, 'udp', 123, 123, mock_dmz.security_group.id, None),
            (mock_tunnel.security_group.id, 'udp', 123, 123, mock_maintenance.security_group.id, None),
            (mock_tunnel.security_group.id, 'icmp', -1, -1, None, '10.0.0.0/26'),
            (mock_tunnel.security_group.id, 'udp', 53, 53, None, '10.0.0.0/26')]
        mock_tunnel.update_sg_rules.assert_called_once_with(expected_tunnel_sg_rules, False)

        expected_dmz_sg_rules = [
            (mock_dmz.security_group.id, 'tcp', 22, 22, mock_maintenance.security_group.id, None),
            (mock_dmz.security_group.id, 'tcp', 3212, 3212, mock_maintenance.security_group.id, None),
            (mock_dmz.security_group.id, 'tcp', 80, 80, None, '66.104.227.162/32'),
            (mock_dmz.security_group.id, 'tcp', 443, 443, None, '66.104.227.162/32'),
            (mock_dmz.security_group.id, 'tcp', 80, 80, None, '38.117.159.162/32'),
            (mock_dmz.security_group.id, 'tcp', 443, 443, None, '38.117.159.162/32'),
            (mock_dmz.security_group.id, 'tcp', 80, 80, None, '64.106.168.244/32'),
            (mock_dmz.security_group.id, 'tcp', 443, 443, None, '64.106.168.244/32'),
            (mock_dmz.security_group.id, 'tcp', 80, 80, None, '0.0.0.0/0'),
            (mock_dmz.security_group.id, 'tcp', 80, 80, mock_dmz.security_group.id, None),
            (mock_dmz.security_group.id, 'tcp', 443, 443, None, '0.0.0.0/0'),
            (mock_dmz.security_group.id, 'tcp', 443, 443, mock_dmz.security_group.id, None),
            (mock_dmz.security_group.id, 'icmp', -1, -1, None, '10.0.0.0/26'),
            (mock_dmz.security_group.id, 'udp', 53, 53, None, '10.0.0.0/26')]
        mock_dmz.update_sg_rules.assert_called_once_with(expected_dmz_sg_rules, False)

        expected_maintenance_sg_rules = [
            (mock_maintenance.security_group.id, 'tcp', 22, 22, mock_maintenance.security_group.id, None),
            (mock_maintenance.security_group.id, 'tcp', 0, 65535, None, '66.104.227.162/32'),
            (mock_maintenance.security_group.id, 'tcp', 0, 65535, None, '38.117.159.162/32'),
            (mock_maintenance.security_group.id, 'icmp', -1, -1, None, '10.0.0.0/26'),
            (mock_maintenance.security_group.id, 'udp', 53, 53, None, '10.0.0.0/26')]
        mock_maintenance.update_sg_rules.assert_called_once_with(expected_maintenance_sg_rules, False)

    def test_destroy(self):
        """ Verify the security group in a VPC are properly deleted """
        security_group = {
            'IpPermissions': [
                {'IpProtocol': 'tcp', 'FromPort': 123, 'ToPort': 1234},
                {'IpProtocol': 'tcp', 'FromPort': 80, 'ToPort': 808},
                {'IpProtocol': 'tcp', 'FromPort': 8080, 'ToPort': 80808},
                {'IpProtocol': 'udp', 'FromPort': 23, 'ToPort': 234}
            ],
            'GroupId': 'sg_id',
            'GroupName': 'sg_name'}
        self.mock_vpc.boto3_ec2.describe_security_groups.return_value = {'SecurityGroups': [security_group]}

        self.disco_vpc_sg_rules.destroy()

        expected_revoke_calls = []
        for permission in security_group['IpPermissions']:
            expected_revoke_calls.append(call(GroupId=security_group['GroupId'],
                                              IpPermissions=[{'ToPort': permission['ToPort'],
                                                              'IpProtocol': permission['IpProtocol'],
                                                              'FromPort': permission['FromPort']}]))
        self.mock_vpc.boto3_ec2.revoke_security_group_ingress.assert_has_calls(expected_revoke_calls)
        self.mock_vpc.boto3_ec2.delete_security_group.assert_called_once_with(
            GroupId=security_group['GroupId'])
