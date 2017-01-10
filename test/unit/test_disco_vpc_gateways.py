"""Tests of disco_vpc_gateways"""

import unittest
from netaddr import IPNetwork
from mock import MagicMock, patch, PropertyMock

from disco_aws_automation.disco_vpc import DiscoVPC
from disco_aws_automation.disco_vpc_gateways import DiscoVPCGateways

from test.helpers.patch_disco_aws import get_mock_config


MOCK_IGW_ID = 'mock_igw_id'
MOCK_VGW_ID = 'mock_vgw_id'
MOCK_VPC_ID = 'mock_vpc_1_id'


# pylint: disable=unused-argument
@patch('disco_aws_automation.disco_vpc.DiscoSNS')
@patch('disco_aws_automation.disco_vpc.DiscoVPCGateways')
@patch('boto3.client')
@patch('boto3.resource')
def _get_vpc_mock(boto3_resource_mock=None,
                  boto3_client_mock=None,
                  gateways_mock=None, sns_mock=None):

    client_mock = MagicMock()
    client_mock.describe_dhcp_options.return_value = {'DhcpOptions': [MagicMock()]}
    boto3_client_mock.return_value = client_mock

    return DiscoVPC('mock-vpc-1', 'sandbox',
                    {'CidrBlock': '10.0.0.0/26', 'VpcId': MOCK_VPC_ID})


class DiscoVPCGatewaysTests(unittest.TestCase):
    """Test DiscoVPCGateways"""

    def setUp(self):
        self.mock_vpc = _get_vpc_mock()
        with patch('disco_aws_automation.disco_vpc_gateways.DiscoEIP'):
            self.disco_vpc_gateways = DiscoVPCGateways(self.mock_vpc, self.mock_vpc.boto3_ec2)

    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('disco_aws_automation.disco_vpc_gateways.time')
    def test_update_gateways_and_routes(self, time_mock,
                                        config_mock, meta_network_mock):
        """ Verify Internet and VPN gateways and the routes to them are created properly """

        config_mock.return_value = get_mock_config({
            'envtype:sandbox': {
                'ip_space': '10.0.0.0/24',
                'vpc_cidr_size': '26',
                'intranet_cidr': 'auto',
                'tunnel_cidr': 'auto',
                'dmz_cidr': 'auto',
                'maintenance_cidr': 'auto',
                'ntp_server': '10.0.0.5',
                'tunnel_igw_routes': '0.0.0.0/0',
                'dmz_igw_routes': '66.104.227.162/32 38.117.159.162/32 64.106.168.244/32',
                'maintenance_vgw_routes': '10.1.0.22/32 10.1.0.24/32'}
        })

        network_intranet_mock = MagicMock()
        network_dmz_mock = MagicMock()
        network_maintenance_mock = MagicMock()
        network_tunnel_mock = MagicMock()

        def _meta_network_mock(name, vpc, network_cidr=None, boto3_connection=None):
            if name == 'intranet':
                ret = network_intranet_mock
            elif name == 'dmz':
                ret = network_dmz_mock
            elif name == 'maintenance':
                ret = network_maintenance_mock
            elif name == 'tunnel':
                ret = network_tunnel_mock
            else:
                return None

            ret.name = name
            ret.vpc = vpc
            if network_cidr:
                ret.network_cidr = IPNetwork(network_cidr)
            else:
                ret.network_cidr = IPNetwork('10.0.0.0/26')

            return ret

        meta_network_mock.side_effect = _meta_network_mock

        self.mock_vpc.boto3_ec2.describe_internet_gateways.return_value = {
            'InternetGateways': [{'InternetGatewayId': MOCK_IGW_ID}]
        }

        self.mock_vpc.boto3_ec2.describe_vpn_gateways.return_value = {
            'VpnGateways': [{'VpnGatewayId': MOCK_VGW_ID,
                             'VpcAttachments': [
                                 {'State': 'attached',
                                  'VpcId': MOCK_VPC_ID}]}]
        }
        # End of setting up test

        # Calling method under test
        self.disco_vpc_gateways.update_gateways_and_routes()

        # Verifying correct behavior
        network_intranet_mock.update_gateways_and_routes.assert_called_once_with([], False)
        network_dmz_mock.update_gateways_and_routes.assert_called_once_with(
            [('66.104.227.162/32', MOCK_IGW_ID),
             ('38.117.159.162/32', MOCK_IGW_ID),
             ('64.106.168.244/32', MOCK_IGW_ID)], False)
        network_maintenance_mock.update_gateways_and_routes.assert_called_once_with(
            [('10.1.0.22/32', MOCK_VGW_ID), ('10.1.0.24/32', MOCK_VGW_ID)], False)
        network_tunnel_mock.update_gateways_and_routes.assert_called_once_with(
            [('0.0.0.0/0', MOCK_IGW_ID)], False)
        self.mock_vpc.boto3_ec2.attach_vpn_gateway.assert_called_once_with(
            VpcId=MOCK_VPC_ID, VpnGatewayId=MOCK_VGW_ID)

    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    def test_update_nat_gateways_and_routes(self, config_mock, meta_network_mock):
        """ Verify NAT gateways and the routes to them are created properly """

        config_mock.return_value = get_mock_config({
            'envtype:sandbox': {
                'ip_space': '10.0.0.0/24',
                'vpc_cidr_size': '26',
                'intranet_cidr': 'auto',
                'tunnel_cidr': 'auto',
                'dmz_cidr': 'auto',
                'maintenance_cidr': 'auto',
                'ntp_server': '10.0.0.5',
                'tunnel_nat_gateways': '10.1.0.4,10.1.0.5,10.1.0.6',
                'intranet_nat_gateways': 'auto',
                'nat_gateway_routes': 'intranet/tunnel'
            }
        })

        network_intranet_mock = MagicMock()
        network_dmz_mock = MagicMock()
        network_maintenance_mock = MagicMock()
        network_tunnel_mock = MagicMock()

        def _meta_network_mock(name, vpc, network_cidr=None, boto3_connection=None):
            if name == 'intranet':
                ret = network_intranet_mock
            elif name == 'dmz':
                ret = network_dmz_mock
            elif name == 'maintenance':
                ret = network_maintenance_mock
            elif name == 'tunnel':
                ret = network_tunnel_mock
            else:
                return None

            ret.name = name
            ret.vpc = vpc
            ret.subnet_ids = ["subnet-cafe", "subnet-beef"]
            ret.get_nat_gateway_metanetwork.return_value = None
            if network_cidr:
                ret.network_cidr = IPNetwork(network_cidr)
            else:
                ret.network_cidr = IPNetwork('10.0.0.0/26')

            return ret

        meta_network_mock.side_effect = _meta_network_mock
        # End of setting up test

        # Calling method under test
        self.disco_vpc_gateways.update_nat_gateways_and_routes()

        # Verifying correct behavior
        network_intranet_mock.upsert_nat_gateway_route.assert_called_once_with(network_tunnel_mock)
        network_tunnel_mock.add_nat_gateways.assert_called_once_with(allocation_ids=[
            self.disco_vpc_gateways.eip.find_eip_address('eip').allocation_id,
            self.disco_vpc_gateways.eip.find_eip_address('eip').allocation_id,
            self.disco_vpc_gateways.eip.find_eip_address('eip').allocation_id
        ])
