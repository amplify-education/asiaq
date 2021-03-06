"""Tests of disco_vpc_peerings"""
import unittest

import boto3
from mock import MagicMock, patch
from moto import mock_ec2

from disco_aws_automation import DiscoVPC
from disco_aws_automation.disco_vpc_peerings import DiscoVPCPeerings, PeeringConnection, PeeringEndpoint

from tests.helpers.patch_disco_aws import get_mock_config


class DiscoVPCPeeringsTests(unittest.TestCase):
    """Test DiscoVPCPeerings"""

    @patch("disco_aws_automation.disco_vpc.DiscoSNS", MagicMock())
    @patch("disco_aws_automation.disco_vpc.DiscoRDS", MagicMock())
    @patch("disco_aws_automation.disco_vpc.DiscoVPCEndpoints", MagicMock())
    def setUp(self):
        mock_ec2().start()

        self.disco_vpc1 = DiscoVPC('mock-vpc-1', 'sandbox')
        self.disco_vpc2 = DiscoVPC('mock-vpc-2', 'sandbox')
        self.disco_vpc3 = DiscoVPC('mock-vpc-3', 'sandbox')

        self.client = boto3.client('ec2')

        self.disco_vpc_peerings = DiscoVPCPeerings()

    @patch('disco_aws_automation.disco_vpc.DiscoMetaNetwork.create_peering_route')
    @patch('disco_aws_automation.disco_vpc_peerings.read_config')
    def test_update_peering_connections(self, config_mock, create_peering_route_mock):
        """ Verify new peering connections are created properly """

        config_mock.return_value = get_mock_config({
            'peerings': {
                'connection_1': 'mock-vpc-1:sandbox/intranet mock-vpc-2:sandbox/intranet'
            }
        })

        # End setting up test

        # Calling method under test
        self.disco_vpc_peerings.update_peering_connections(self.disco_vpc1)

        # Asserting correct behavior

        peeerings = self.client.describe_vpc_peering_connections().get('VpcPeeringConnections')

        self.assertEqual(1, len(peeerings))

        peering_id = peeerings[0]['VpcPeeringConnectionId']

        self.assertEqual(self.disco_vpc1.get_vpc_id(), peeerings[0]['RequesterVpcInfo']['VpcId'])
        self.assertEqual(self.disco_vpc2.get_vpc_id(), peeerings[0]['AccepterVpcInfo']['VpcId'])

        # create_peering_route should have been called twice, once for each VPC
        create_peering_route_mock.assert_called_with(peering_id, '10.101.0.0/20')
        self.assertEqual(2, create_peering_route_mock.call_count)

    def test_parse_peering_connection(self):
        """test parsing a peering connection line with wildcards"""
        actual = self.disco_vpc_peerings._resolve_peering_connection_line(
            'mock-vpc-1:sandbox/intranet mock-vpc-3:sandbox/intranet'
        )

        expected = [
            PeeringConnection.from_peering_line('mock-vpc-1:sandbox/intranet mock-vpc-3:sandbox/intranet'),
        ]

        self.assertItemsEqual(actual, expected)

    def test_parse_peering_connection_wildcards(self):
        """test parsing a peering connection line with wildcards"""
        actual = self.disco_vpc_peerings._resolve_peering_connection_line(
            '*:sandbox/intranet mock-vpc-3:sandbox/intranet'
        )

        expected = [
            PeeringConnection.from_peering_line('mock-vpc-1:sandbox/intranet mock-vpc-3:sandbox/intranet'),
            PeeringConnection.from_peering_line('mock-vpc-2:sandbox/intranet mock-vpc-3:sandbox/intranet')
        ]

        self.assertItemsEqual(actual, expected)

    def test_parse_peering_double_wildcards(self):
        """test parsing a peering connection line with wildcards on both sides"""
        actual = self.disco_vpc_peerings._resolve_peering_connection_line(
            '*:sandbox/intranet *:sandbox/intranet'
        )

        expected = [
            PeeringConnection.from_peering_line('mock-vpc-1:sandbox/intranet mock-vpc-2:sandbox/intranet'),
            PeeringConnection.from_peering_line('mock-vpc-1:sandbox/intranet mock-vpc-3:sandbox/intranet'),
            PeeringConnection.from_peering_line('mock-vpc-2:sandbox/intranet mock-vpc-3:sandbox/intranet')
        ]

        self.assertItemsEqual(actual, expected)


class DiscoVPCPeeringsUpdateTests(unittest.TestCase):
    """Test DiscoVPCPeerings during VPC update"""

    @classmethod
    def setUpClass(cls):
        cls.disco_vpc_peerings = DiscoVPCPeerings()
        cls.vpc_endpoint_1 = PeeringEndpoint('test-env1', 'test-type', 'intranet', {'VpcId': 'vpc-1234'})
        cls.vpc_endpoint_2 = PeeringEndpoint('test-env2', 'test-type', 'intranet', {'VpcId': 'vpc-5678'})
        cls.peering_connection_1 = PeeringConnection(cls.vpc_endpoint_1, cls.vpc_endpoint_2)
        cls.peering_connection_2 = PeeringConnection(cls.vpc_endpoint_2, cls.vpc_endpoint_1)

    def test_update_missing_peerings(self):
        """Test missing peering is udpated"""

        self.disco_vpc_peerings._get_peerings_from_config = MagicMock(
            return_value={self.peering_connection_1}
        )
        self.disco_vpc_peerings._get_existing_peerings = MagicMock(
            return_value=set()
        )
        self.disco_vpc_peerings._create_peering_connections = MagicMock()
        self.disco_vpc_peerings._create_peering_routes = MagicMock()

        self.disco_vpc_peerings.update_peering_connections(MagicMock())

        self.disco_vpc_peerings._create_peering_connections.assert_called_once_with(
            {self.peering_connection_1}
        )
        self.disco_vpc_peerings._create_peering_routes.assert_called_once_with(
            {self.peering_connection_1}
        )

    def test_not_update_existing_peerings_1(self):
        """Test existing peering is not udpated (configured peering source & target match with existing)"""

        self.disco_vpc_peerings._get_peerings_from_config = MagicMock(
            return_value={self.peering_connection_1}
        )
        self.disco_vpc_peerings._get_existing_peerings = MagicMock(
            return_value={self.peering_connection_1}
        )
        self.disco_vpc_peerings._create_peering_connections = MagicMock()
        self.disco_vpc_peerings._create_peering_routes = MagicMock()

        self.disco_vpc_peerings.update_peering_connections(MagicMock())

        self.disco_vpc_peerings._create_peering_connections.assert_not_called()
        self.disco_vpc_peerings._create_peering_routes.assert_not_called()

    def test_not_update_existing_peerings_2(self):
        """Test existing peering is not udpated (configured peering source & target opposite of existing)"""

        self.disco_vpc_peerings._get_peerings_from_config = MagicMock(
            return_value={self.peering_connection_1}
        )
        self.disco_vpc_peerings._get_existing_peerings = MagicMock(
            return_value={self.peering_connection_2}
        )
        self.disco_vpc_peerings._create_peering_connections = MagicMock()
        self.disco_vpc_peerings._create_peering_routes = MagicMock()

        self.disco_vpc_peerings.update_peering_connections(MagicMock())

        self.disco_vpc_peerings._create_peering_connections.assert_not_called()
        self.disco_vpc_peerings._create_peering_routes.assert_not_called()

    @patch.object(DiscoVPCPeerings, '_get_peering_route_tables')
    def test_delete_peering_routes(self, mock_route_tables):
        """Test peering routes are removed when peering is deleted"""

        self.disco_vpc_peerings.client = MagicMock()

        mock_peering_list = [{
            'VpcPeeringConnectionId': 'pcx-12345678'
        }]

        mock_route_tables.return_value = [
            {
                'RouteTableId': 'rtb-12345678',
                'Routes': [
                    {
                        'DestinationCidrBlock': '10.10.0.0/16',
                        'VpcPeeringConnectionId': 'pcx-12345678'
                    }
                ]
            }
        ]

        self.disco_vpc_peerings.list_peerings = MagicMock(return_value=mock_peering_list)

        self.disco_vpc_peerings.delete_peerings(vpc_id=MagicMock())

        self.disco_vpc_peerings.client.delete_route.assert_called_with(
            DestinationCidrBlock='10.10.0.0/16',
            RouteTableId='rtb-12345678'
        )
