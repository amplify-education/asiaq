"""Tests of disco_metanetwork"""
from unittest import TestCase

from mock import MagicMock, call, patch

from disco_aws_automation.disco_metanetwork import DiscoMetaNetwork
from disco_aws_automation.exceptions import EIPConfigError

from test.helpers.patch_disco_aws import TEST_ENV_NAME


MOCK_ROUTE_FILTER = {"vpc-id": "mock_vpc_id",
                     "tag:meta_network": TEST_ENV_NAME}
MOCK_ZONE1 = MagicMock()
MOCK_ZONE1.name = "aws-zone1"
MOCK_ZONE2 = MagicMock()
MOCK_ZONE2.name = "aws-zone2"
MOCK_ZONE3 = MagicMock()
MOCK_ZONE3.name = "aws-zone3"
MOCK_ZONES = [MOCK_ZONE1, MOCK_ZONE2, MOCK_ZONE3]
MOCK_ROUTE_TABLE = MagicMock()
MOCK_ROUTE_TABLE.id = "route_table_id"


def _get_vpc_mock():
    ret = MagicMock()
    ret.vpc_filters.return_value = [{"Name": "vpc-id", "Values": [MOCK_ROUTE_FILTER["vpc-id"]]}]
    ret.get_vpc_id.return_value = MOCK_ROUTE_FILTER["vpc-id"]

    return ret


def _get_vpc_conn_mock():
    ret = MagicMock()
    ret.get_all_zones.return_value = MOCK_ZONES

    security_group = MagicMock()
    grant = MagicMock()
    grant.group_id = None
    grant.cidr_ip = '12.23.34.45/23'
    rule = MagicMock()
    rule.ip_protocol = 'udp'
    rule.from_port = 43
    rule.to_port = 21
    rule.grants = [grant]
    security_group.rules = [rule]
    security_group.id = 'sg_id'

    ret.get_all_security_groups.return_value = [security_group]
    ret.get_all_route_tables.return_value = [MOCK_ROUTE_TABLE]
    return ret


# DiscoSubnet.__init__ is being patched but not referenced.
# pylint: disable=W0613
class DiscoMetaNetworkTests(TestCase):
    """Test DiscoMetaNetwork"""

    def setUp(self):
        self.mock_vpc_conn = _get_vpc_conn_mock()
        with patch('disco_aws_automation.disco_metanetwork.VPCConnection',
                   return_value=self.mock_vpc_conn):
            self.mock_vpc = _get_vpc_mock()
            self.meta_network = DiscoMetaNetwork(TEST_ENV_NAME, self.mock_vpc, network_cidr='10.101.0.0/16')

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_create_meta_network(self, mock_subnet_init):
        """ Verify that a meta network is properly created """
        self.meta_network.create()

        self.mock_vpc_conn.\
            get_all_route_tables.assert_called_once_with(filters=MOCK_ROUTE_FILTER)
        self.mock_vpc_conn.\
            get_all_security_groups.assert_called_once_with(filters=MOCK_ROUTE_FILTER)

        self.assertEquals(self.meta_network.centralized_route_table,
                          MOCK_ROUTE_TABLE)
        self.assertEquals(self.meta_network.security_group,
                          self.mock_vpc_conn.get_all_security_groups.return_value[0])

        calls = [call(MOCK_ZONE1.name, self.meta_network, "10.101.0.0/18", MOCK_ROUTE_TABLE.id),
                 call(MOCK_ZONE2.name, self.meta_network, "10.101.64.0/18", MOCK_ROUTE_TABLE.id),
                 call(MOCK_ZONE3.name, self.meta_network, "10.101.128.0/18", MOCK_ROUTE_TABLE.id)]
        mock_subnet_init.assert_has_calls(calls)
        self.assertEquals(len(self.meta_network.disco_subnets.values()), len(MOCK_ZONES))

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.recreate_route_table', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.create_nat_gateway', return_value=None)
    def test_create_nat_gateways(self, mock_create_nat_gateway,
                                 mock_recreate_route_table, mock_subnet_init):
        """ Verify that NAT gateways are properly created for a meta network  """
        mock_allocation_ids = ["allocation_id1", "allocation_id2", "allocation_id3"]

        self.meta_network.create()
        self.meta_network.add_nat_gateways(allocation_ids=mock_allocation_ids)

        self.assertFalse(self.meta_network.centralized_route_table)
        self.mock_vpc_conn.delete_route_table.assert_called_once_with(MOCK_ROUTE_TABLE.id)

        recreate_route_table_calls = []
        for _ in range(len(MOCK_ZONES)):
            recreate_route_table_calls.append(call())
        mock_recreate_route_table.assert_has_calls(recreate_route_table_calls)

        nat_gateway_calls = []
        for allocation_id in mock_allocation_ids:
            nat_gateway_calls.append(call(eip_allocation_id=allocation_id))
        mock_create_nat_gateway.assert_has_calls(nat_gateway_calls)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.recreate_route_table', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.create_nat_gateway', return_value=None)
    def test_create_dyno_nat_gateways(self, mock_create_nat_gateway,
                                      mock_recreate_route_table, mock_subnet_init):
        """ Verify that NAT gateways are properly created for a meta network using dynamic EIPs"""

        self.meta_network.create()
        self.meta_network.add_nat_gateways()

        self.assertFalse(self.meta_network.centralized_route_table)
        self.mock_vpc_conn.delete_route_table.assert_called_once_with(MOCK_ROUTE_TABLE.id)

        recreate_route_table_calls = []
        nat_gateway_calls = []
        for _ in range(len(MOCK_ZONES)):
            recreate_route_table_calls.append(call())
            nat_gateway_calls.append(call())

        mock_recreate_route_table.assert_has_calls(recreate_route_table_calls)
        mock_create_nat_gateway.assert_has_calls(nat_gateway_calls)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_create_nat_gateways__fail(self, mock_subnet_init):
        """ Verify that insufficient allocation ids would raise exception during NAT gateway creation """
        mock_allocation_ids = ["allocation_id1"]

        self.meta_network.create()

        with self.assertRaises(EIPConfigError):
            self.meta_network.add_nat_gateways(mock_allocation_ids)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_add_route_cntrlzd_rt_tbl(self, mock_subnet_init):
        """ Verify a route is added when a centralized route table is used """
        self.meta_network.create()

        mock_gateway_id = 'mock_gateway_id'
        mock_cidr = '10.101.0.0/16'

        self.meta_network.add_gateway_routes([(mock_cidr, mock_gateway_id)])

        self.mock_vpc_conn.create_route.\
            assert_called_once_with(destination_cidr_block=mock_cidr,
                                    route_table_id=MOCK_ROUTE_TABLE.id,
                                    gateway_id=mock_gateway_id)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.add_route_to_gateway', return_value=True)
    def test_add_route__multi_rt_tbls(self, mock_add_route,
                                      mock_subnet_init):
        """ Verify a route is added to all subnets' route tables """
        self.mock_vpc_conn.get_all_route_tables.return_value = []
        self.meta_network.create()

        mock_gateway_id = 'mock_gateway_id'
        mock_cidr = '10.101.0.0/16'

        self.meta_network.add_gateway_routes([(mock_cidr, mock_gateway_id)])

        add_route_calls = []
        for _ in range(len(MOCK_ZONES)):
            add_route_calls.append(call(mock_cidr, mock_gateway_id))

        mock_add_route.assert_has_calls(add_route_calls)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_create_peering__single_rt_tbl(self, mock_subnet_init):
        """ Verify peering is created when a centralized route table is used """
        self.meta_network.create()

        mock_peering_conn_id = 'peering_conn_id'
        mock_cidr1 = '10.101.0.0/16'
        self.meta_network.create_peering_route(mock_peering_conn_id, mock_cidr1)

        mock_cidr2 = '10.102.0.0/16'
        self.meta_network.create_peering_route(mock_peering_conn_id, mock_cidr2)

        expected_calls = [call(destination_cidr_block=mock_cidr1,
                               route_table_id=MOCK_ROUTE_TABLE.id,
                               vpc_peering_connection_id=mock_peering_conn_id),
                          call(destination_cidr_block=mock_cidr2,
                               route_table_id=MOCK_ROUTE_TABLE.id,
                               vpc_peering_connection_id=mock_peering_conn_id)]
        self.mock_vpc_conn.create_route.assert_has_calls(expected_calls)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_create_peering__with_existing_route(self, mock_subnet_init):
        """ Verify peering replacement is properly done """
        mock_peering_conn_id = 'peering_conn_id'
        mock_cidr = '10.101.0.0/16'

        mock_route = MagicMock()
        mock_route.destination_cidr_block = mock_cidr
        mock_route_table = MagicMock()
        mock_route_table.id = "route_table_id"
        mock_route_table.routes = [mock_route]

        self.mock_vpc_conn.get_all_route_tables.return_value = [mock_route_table]
        self.meta_network.create()

        self.meta_network.create_peering_route(mock_peering_conn_id, mock_cidr)

        self.mock_vpc_conn.replace_route.\
            assert_called_once_with(destination_cidr_block=mock_cidr,
                                    route_table_id=MOCK_ROUTE_TABLE.id,
                                    vpc_peering_connection_id=mock_peering_conn_id)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.create_peering_routes', return_value=None)
    def test_create_peering__multi_rt_tbls(self, mock_create_peering_routes,
                                           mock_subnet_init):
        """ Verify peering is properly when the meta network has one route table per subnet """
        self.mock_vpc_conn.get_all_route_tables.return_value = []
        self.meta_network.create()

        mock_peering_conn_id = 'peering_conn_id'
        mock_cidr = '10.101.0.0/16'

        self.meta_network.create_peering_route(mock_peering_conn_id, mock_cidr)

        create_peering_routes_calls = []
        for _ in range(len(MOCK_ZONES)):
            create_peering_routes_calls.append(call(mock_peering_conn_id, mock_cidr))

        mock_create_peering_routes.assert_has_calls(create_peering_routes_calls)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_update_sg_rules(self, mock_subnet_init):
        """ Verify updating security group rules """
        self.meta_network.create()

        self.meta_network.update_sg_rules([('sg_id', 'tcp', 123, 234, 'source_sg_id', None)])

        self.mock_vpc_conn.authorize_security_group.assert_called_once_with(
            src_security_group_group_id='source_sg_id', to_port=234,
            from_port=123, group_id='sg_id', ip_protocol='tcp')
        self.mock_vpc_conn.revoke_security_group.assert_called_once_with(
            cidr_ip='12.23.34.45/23', from_port=43, group_id='sg_id',
            ip_protocol='udp', to_port=21)

    @patch('disco_aws_automation.disco_subnet.DiscoSubnet.__init__', return_value=None)
    def test_update_gateways_and_routes(self, mock_subnet_init):
        """ Verify gateways and routes are updated correctly """
        mock_cidr_1 = '23.34.32.12/32'
        mock_cidr_2 = '65.76.23.23/32'
        mock_cidr_3 = '6.6.4.23/32'
        mock_route_1 = MagicMock()
        mock_route_1.gateway_id = "old_gateway_1_id"
        mock_route_1.destination_cidr_block = mock_cidr_1
        mock_route_2 = MagicMock()
        mock_route_2.gateway_id = "old_gateway_2_id"
        mock_route_2.destination_cidr_block = mock_cidr_2
        mock_route_table = MagicMock()
        mock_route_table.id = "mock_route_table_id"
        mock_route_table.routes = [mock_route_1, mock_route_2]
        self.mock_vpc_conn.get_all_route_tables.return_value = [mock_route_table]

        self.meta_network.create()

        self.meta_network.update_gateways_and_routes([
            (mock_cidr_2, 'new_gateway_1_id'), (mock_cidr_3, 'new_gateway_2_id')])

        self.mock_vpc_conn.delete_route.assert_called_once_with(
            destination_cidr_block=mock_cidr_1, route_table_id='mock_route_table_id')
        self.mock_vpc_conn.replace_route.assert_called_once_with(
            destination_cidr_block=mock_cidr_2,
            gateway_id='new_gateway_1_id',
            route_table_id='mock_route_table_id')
        self.mock_vpc_conn.create_route.assert_called_once_with(
            destination_cidr_block=mock_cidr_3,
            gateway_id='new_gateway_2_id',
            route_table_id='mock_route_table_id'
        )
