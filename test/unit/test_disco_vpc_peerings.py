"""Tests of disco_vpc_peerings"""
import unittest

import boto3
from mock import MagicMock, patch
from moto import mock_ec2

from disco_aws_automation import DiscoVPC
from disco_aws_automation.disco_vpc_peerings import DiscoVPCPeerings, PeeringConnection

from test.helpers.patch_disco_aws import get_mock_config


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
