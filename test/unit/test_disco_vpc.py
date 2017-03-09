"""Tests of disco_vpc"""

import unittest

from mock import MagicMock, patch, PropertyMock, call

from disco_aws_automation import DiscoVPC
from test.helpers.patch_disco_aws import get_mock_config, get_default_config_dict


class DiscoVPCTests(unittest.TestCase):
    """Test DiscoVPC"""

    # pylint: disable=unused-argument
    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    def test_create_meta_networks(self, meta_network_mock, config_mock, endpoints_mock):
        """Test creating meta networks with dynamic ip ranges"""
        vpc_mock = {'CidrBlock': '10.0.0.0/28',
                    'VpcId': 'mock_vpc_id'}

        config_mock.return_value = get_mock_config({
            'envtype:auto-vpc-type': {
                'vpc_cidr': '10.0.0.0/28',
                'intranet_cidr': 'auto',
                'tunnel_cidr': 'auto',
                'dmz_cidr': 'auto',
                'maintenance_cidr': 'auto'
            }
        })

        def _create_meta_network_mock(network_name, vpc, cidr):
            ret = MagicMock()
            ret.name = network_name
            ret.vpc = vpc
            ret.network_cidr = cidr

            return ret

        meta_network_mock.side_effect = _create_meta_network_mock

        auto_vpc = DiscoVPC('auto-vpc', 'auto-vpc-type', vpc_mock)

        meta_networks = auto_vpc._create_new_meta_networks()
        self.assertItemsEqual(['intranet', 'tunnel', 'dmz', 'maintenance'], meta_networks.keys())

        expected_ip_ranges = ['10.0.0.0/30', '10.0.0.4/30', '10.0.0.8/30', '10.0.0.12/30']
        actual_ip_ranges = [str(meta_network.network_cidr) for meta_network in meta_networks.values()]

        self.assertItemsEqual(actual_ip_ranges, expected_ip_ranges)

    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    def test_create_meta_networks_static_dynamic(self, meta_network_mock, config_mock, endpoints_mock):
        """Test creating meta networks with a mix of static and dynamic ip ranges"""
        vpc_mock = {'CidrBlock': '10.0.0.0/28',
                    'VpcId': 'mock_vpc_id',
                    'DhcpOptionsId': 'mock_dhcp_options_id'}

        config_mock.return_value = get_mock_config({
            'envtype:auto-vpc-type': {
                'vpc_cidr': '10.0.0.0/28',
                'intranet_cidr': 'auto',
                'tunnel_cidr': 'auto',
                'dmz_cidr': '10.0.0.4/31',
                'maintenance_cidr': 'auto'
            }
        })

        def _create_meta_network_mock(network_name, vpc, cidr):
            ret = MagicMock()
            ret.name = network_name
            ret.vpc = vpc
            ret.network_cidr = cidr

            return ret

        meta_network_mock.side_effect = _create_meta_network_mock

        auto_vpc = DiscoVPC('auto-vpc', 'auto-vpc-type', vpc_mock)

        meta_networks = auto_vpc._create_new_meta_networks()
        self.assertItemsEqual(['intranet', 'tunnel', 'dmz', 'maintenance'], meta_networks.keys())

        expected_ip_ranges = ['10.0.0.0/30', '10.0.0.4/31', '10.0.0.8/30', '10.0.0.12/30']
        actual_ip_ranges = [str(meta_network.network_cidr) for meta_network in meta_networks.values()]

        self.assertItemsEqual(actual_ip_ranges, expected_ip_ranges)

    # pylint: disable=unused-argument
    @patch('disco_aws_automation.disco_vpc.DiscoRDS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoSNS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCGateways')
    @patch('time.sleep')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    def test_create_auto_vpc(self, meta_network_mock, boto3_resource_mock,
                             boto3_client_mock, config_mock,
                             sleep_mock, gateways_mock, sns_mock, endpoints_mock, rds_mock):
        """Test creating a VPC with a dynamic ip range"""
        # FIXME This needs to mock way too many things. DiscoVPC needs to be refactored

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

        client_mock = MagicMock()
        client_mock.create_vpc.side_effect = _create_vpc_mock
        client_mock.get_all_zones.return_value = [MagicMock()]
        client_mock.describe_dhcp_options.return_value = {'DhcpOptions': [MagicMock()]}
        boto3_client_mock.return_value = client_mock

        auto_vpc = DiscoVPC('auto-vpc', 'auto-vpc-type')

        possible_vpcs = ['10.0.0.0/26', '10.0.0.64/26', '10.0.0.128/26', '10.0.0.192/26']
        self.assertIn(str(auto_vpc.vpc['CidrBlock']), possible_vpcs)

    @patch('disco_aws_automation.disco_vpc.DiscoRDS')
    @patch('disco_aws_automation.disco_vpc.DiscoSNS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('boto3.client')
    @patch('boto3.resource')
    def test_create_vpc_with_custom_tags(self, boto3_resource_mock, boto3_client_mock, config_mock,
                                         endpoints_mock, sns_mock, rds_mock):
        """Test creating a VPC with a dynamic ip range and tags"""
        # FIXME This needs to mock way too many things. DiscoVPC needs to be refactored

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

        client_mock = MagicMock()
        client_mock.create_vpc.side_effect = _create_vpc_mock
        boto3_client_mock.return_value = client_mock

        resource_mock = MagicMock()
        resource_mock.Vpc.create_tags.return_value = []
        boto3_resource_mock.return_value = resource_mock

        my_tags_options = [{'Value': 'astronauts', 'Key': 'productline'},
                           {'Value': 'tag_value', 'Key': 'mytag'}]
        DiscoVPC._get_vpc_cidr = MagicMock()
        DiscoVPC._get_vpc_cidr.return_value = '10.0.0.0/26'
        with patch("disco_aws_automation.DiscoVPC._create_new_meta_networks",
                   return_value=MagicMock(return_value={})):
            with patch("disco_aws_automation.DiscoVPC._update_dhcp_options", return_value=None):
                # The expect list of tag dictionaries
                expected_vpc_tags = [{'Value': 'auto-vpc', 'Key': 'Name'},
                                     {'Value': 'auto-vpc-type', 'Key': 'type'},
                                     {'Value': 'ANY', 'Key': 'create_date'},
                                     {'Value': 'astronauts', 'Key': 'productline'},
                                     {'Value': 'tag_value', 'Key': 'mytag'}]

                DiscoVPC('auto-vpc', 'auto-vpc-type', vpc_tags=my_tags_options)
                # Get the create_tags argument
                call_args_tags = resource_mock.Vpc.return_value.create_tags.call_args[1]
                # Verify Option Name
                self.assertEquals(['Tags'], call_args_tags.keys())
                call_tags_dict = call_args_tags['Tags']
                # Verify the number of tag Dictionaries in the list
                self.assertEquals(5, len(call_tags_dict))
                # Verify each tag options
                for tag_option in call_tags_dict:
                    if tag_option['Key'] == 'create_date':
                        tag_option['Value'] = 'ANY'
                    self.assertIn(tag_option, expected_vpc_tags)

    # pylint: disable=unused-argument,too-many-arguments,too-many-locals
    @patch('socket.gethostbyname')
    @patch('disco_aws_automation.disco_vpc.DiscoRDS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoSNS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCGateways')
    @patch('time.sleep')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    def test_create_vpc_ntp_names(self, meta_network_mock, boto3_resource_mock,
                                  boto3_client_mock, config_mock,
                                  sleep_mock, gateways_mock, sns_mock, endpoints_mock, rds_mock,
                                  gethostbyname_mock):
        """Test creating VPC with NTP server names"""
        # FIXME This needs to mock way too many things. DiscoVPC needs to be refactored

        local_dict = {
            'dhcp_options_created': False,
            'ntp_servers_dict': {
                '0.mock.ntp.server': '100.10.10.10',
                '1.mock.ntp.server': '100.10.10.11',
                '2.mock.ntp.server': '100.10.10.12'
            },
            'new_mock_dhcp_options_id': 'new_mock_dhcp_options_id',
            'mock_vpc_id': 'mock_vpc_id'
        }

        config_mock.return_value = get_mock_config({
            'envtype:auto-vpc-type': {
                'ip_space': '10.0.0.0/24',
                'vpc_cidr_size': '26',
                'intranet_cidr': 'auto',
                'tunnel_cidr': 'auto',
                'dmz_cidr': 'auto',
                'maintenance_cidr': 'auto',
                'ntp_server': ' '.join(local_dict['ntp_servers_dict'].keys())
            }
        })

        # pylint: disable=C0103
        def _create_vpc_mock(CidrBlock):
            return {'Vpc': {'CidrBlock': CidrBlock,
                            'VpcId': local_dict['mock_vpc_id'],
                            'DhcpOptionsId': 'mock_dhcp_options_id'}}

        def _create_create_dhcp_mock(**args):
            local_dict['dhcp_options_created'] = True
            return {'DhcpOptions': {'DhcpOptionsId': local_dict['new_mock_dhcp_options_id']}}

        def _create_describe_dhcp_mock(**args):
            if local_dict['dhcp_options_created']:
                return {'DhcpOptions': [{'DhcpOptionsId': local_dict['new_mock_dhcp_options_id']}]}
            else:
                return {'DhcpOptions': []}

        def _create_gethostbyname_mock(hostname):
            return local_dict['ntp_servers_dict'][hostname]

        client_mock = MagicMock()
        client_mock.create_vpc.side_effect = _create_vpc_mock
        client_mock.get_all_zones.return_value = [MagicMock()]
        client_mock.create_dhcp_options.side_effect = _create_create_dhcp_mock
        client_mock.describe_dhcp_options.side_effect = _create_describe_dhcp_mock
        gethostbyname_mock.side_effect = _create_gethostbyname_mock
        boto3_client_mock.return_value = client_mock

        # Calling method under test
        DiscoVPC('auto-vpc', 'auto-vpc-type')

        # Verifying result
        actual_ntp_servers = [
            option['Values']
            for option in client_mock.create_dhcp_options.call_args[1]['DhcpConfigurations']
            if option['Key'] == 'ntp-servers'][0]
        self.assertEquals(set(actual_ntp_servers), set(local_dict['ntp_servers_dict'].values()))

        client_mock.associate_dhcp_options.assert_has_calls(
            [call(DhcpOptionsId=local_dict['new_mock_dhcp_options_id'],
                  VpcId=local_dict['mock_vpc_id'])])

    # pylint: disable=unused-argument
    @patch('disco_aws_automation.disco_vpc.DiscoRDS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCEndpoints')
    @patch('disco_aws_automation.disco_vpc.DiscoSNS')
    @patch('disco_aws_automation.disco_vpc.DiscoVPCGateways')
    @patch('time.sleep')
    @patch('disco_aws_automation.disco_vpc.DiscoVPC.config', new_callable=PropertyMock)
    @patch('boto3.client')
    @patch('boto3.resource')
    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork')
    def test_reserve_hostclass_ip_addresses(self, meta_network_mock, boto3_resource_mock,
                                            boto3_client_mock, config_mock,
                                            sleep_mock, gateways_mock, sns_mock, endpoints_mock,
                                            rds_mock):
        """Test hostclass IP addresses are being reserved during VPC creation"""

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

        client_mock = MagicMock()
        client_mock.create_vpc.side_effect = _create_vpc_mock
        client_mock.get_all_zones.return_value = [MagicMock()]
        client_mock.describe_dhcp_options.return_value = {'DhcpOptions': [MagicMock()]}
        boto3_client_mock.return_value = client_mock
        network_mock = MagicMock()
        meta_network_mock.return_value = network_mock

        DiscoVPC('auto-vpc', 'auto-vpc-type', aws_config=get_mock_config())

        expected_calls = []
        default_config = get_default_config_dict()
        for section in default_config:
            if section.startswith("mhc") and default_config[section].get("ip_address"):
                expected_calls.append(call(default_config[section].get("ip_address")))
        network_mock.get_interface.assert_has_calls(expected_calls)
