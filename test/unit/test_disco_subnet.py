"""Tests of disco_subnet"""
from unittest import TestCase
import copy

from mock import MagicMock, call

from disco_aws_automation.disco_subnet import (
    DiscoSubnet,
    DYNO_NAT_TAG_KEY
)
from test.helpers.patch_disco_aws import TEST_ENV_NAME


MOCK_SUBNET_NAME = 'availability_zone_1'
MOCK_CIDR = '10.101.0.0/16'
MOCK_VPC_NAME = 'mock_vpc_name'
MOCK_VPC_ID = 'mock_vpc_id'
MOCK_ROUTE_TABLE_ID = 'centralized_route_table_id'
MOCK_ALLOCATION_ID = 'mock_allocation_id'
MOCK_PUBLIC_IP = '102.102.102.102'
MOCK_NAT_GATEWAY_ID = 'nat_gateway_id'
MOCK_SUBNET_ID = 'subnet_id'
MOCK_ROUTE_TABLE_ASSOC_ID = 'route_table_association_id'
MOCK_REPLACE_CIDR = '111.111.111.111/24'
MOCK_SUBNET = {'SubnetId': MOCK_SUBNET_ID,
               'State': 'available',
               'VpcId': MOCK_VPC_ID,
               'CidrBlock': MOCK_CIDR,
               'Tags': []}
MOCK_ROUTE_TABLE = {'RouteTableId': MOCK_ROUTE_TABLE_ID,
                    'Routes': [{'DestinationCidrBlock': MOCK_REPLACE_CIDR,
                                'GatewayId': 'mock_gateway_id1'},
                               {'DestinationCidrBlock': '22.22.22.22/24',
                                'GatewayId': 'mock_gateway_id2'}],
                    'Associations': [{'RouteTableAssociationId': MOCK_ROUTE_TABLE_ASSOC_ID,
                                      'RouteTableId': MOCK_ROUTE_TABLE_ID,
                                      'SubnetId': MOCK_SUBNET_ID,
                                      'Main': False}]}
MOCK_NEW_ROUTE_TABLE = {'RouteTableId': 'new_route_table_id'}
MOCK_NAT_GATEWAY = {'VpcId': MOCK_VPC_ID,
                    'SubnetId': MOCK_SUBNET_ID,
                    'NatGatewayId': MOCK_NAT_GATEWAY_ID,
                    'NatGatewayAddresses': [{'AllocationId': MOCK_ALLOCATION_ID,
                                             'PublicIp': MOCK_PUBLIC_IP}]}
MOCK_ROUTE = {'RouteId': 'route_id'}
MOCK_TAG = [{'Value': "{0}_{1}_{2}".format(TEST_ENV_NAME,
                                           MOCK_VPC_NAME,
                                           MOCK_SUBNET_NAME),
             'Key': 'Name'},
            {'Value': 'mock_vpc_name', 'Key': 'meta_network'},
            {'Value': 'availability_zone_1', 'Key': 'subnet'}]


def _get_metanetwork_mock():
    ret = MagicMock()
    ret.name = MOCK_VPC_NAME
    ret.vpc = MagicMock()
    ret.vpc.environment_name = TEST_ENV_NAME
    ret.vpc.vpc = {'VpcId': MOCK_VPC_ID}
    return ret


def _get_ec2_conn_mock(test_disco_subnet):
    ret = MagicMock()

    def _mock_describe_route_tables(*_, **__):
        if test_disco_subnet.route_table:
            return {'RouteTables': [copy.deepcopy(test_disco_subnet.route_table)]}
        else:
            return {'RouteTables': []}

    def _mock_describe_nat_gateways(*_, **__):
        if test_disco_subnet.nat_gateway:
            return {'NatGateways': [test_disco_subnet.nat_gateway]}
        else:
            return {'NatGateways': []}

    def _mock_create_nat_gateway(*_, **__):
        test_disco_subnet.nat_gateway = copy.deepcopy(MOCK_NAT_GATEWAY)
        return {'NatGateway': MOCK_NAT_GATEWAY}

    def _mock_delete_nat_gateway(*_, **__):
        test_disco_subnet.nat_gateway = None
        return {'NatGatewayId': MOCK_NAT_GATEWAY['NatGatewayId']}

    def _mock_describe_subnets(*_, **__):
        if test_disco_subnet.existing_subnet:
            return {'Subnets': [test_disco_subnet.existing_subnet]}
        else:
            return {'Subnets': []}

    def _mock_create_subnet(*_, **__):
        if not test_disco_subnet.existing_subnet:
            test_disco_subnet.existing_subnet = MOCK_SUBNET

        return {'Subnet': test_disco_subnet.existing_subnet}

    def _mock_create_route(*_, **params):
        test_disco_subnet.route_table['Routes'].append(
            {'DestinationCidrBlock': params.get('DestinationCidrBlock'),
             'NatGatewayId': params.get('NatGatewayId'),
             'GatewayId': params.get('GatewayId'),
             'InstanceId': params.get('InstanceId'),
             'NetworkInterfaceId': params.get('NetworkInterfaceId'),
             'VpcPeeringConnectionId': params.get('VpcPeeringConnectionId')}
        )
        return {'Return': params}

    def _mock_replace_route(*_, **params):
        for route in test_disco_subnet.route_table['Routes']:
            if route['DestinationCidrBlock'] == params.get('DestinationCidrBlock'):
                route['NatGatewayId'] = params.get('NatGatewayId')
                route['GatewayId'] = params.get('GatewayId')
                route['InstanceId'] = params.get('InstanceId')
                route['NetworkInterfaceId'] = params.get('NetworkInterfaceId')
                route['VpcPeeringConnectionId'] = params.get('VpcPeeringConnectionId')

    def _mock_delete_route(*_, **params):
        test_disco_subnet.route_table['Routes'] =\
            [route for route in test_disco_subnet.route_table['Routes']
             if route['DestinationCidrBlock'] != params.get('DestinationCidrBlock')]

    ret.create_tags.return_value = None
    ret.create_route_table.return_value = {'RouteTable': MOCK_NEW_ROUTE_TABLE}
    ret.create_route.side_effect = _mock_create_route
    ret.replace_route.side_effect = _mock_replace_route
    ret.delete_route.side_effect = _mock_delete_route
    ret.associate_route_table.return_value = {'AssociationId': 'association_id'}
    ret.describe_route_tables.side_effect = _mock_describe_route_tables
    ret.describe_nat_gateways.side_effect = _mock_describe_nat_gateways
    ret.create_nat_gateway.side_effect = _mock_create_nat_gateway
    ret.delete_nat_gateway.side_effect = _mock_delete_nat_gateway
    ret.describe_subnets.side_effect = _mock_describe_subnets
    ret.create_subnet.side_effect = _mock_create_subnet

    return ret


def _get_disco_eip_mock():
    ret = MagicMock()

    eip_mock = MagicMock()
    eip_mock.allocation_id = MOCK_ALLOCATION_ID

    ret.allocate.return_value = eip_mock

    return ret


class DiscoSubnetTests(TestCase):
    """Test DiscoSubnet"""

    def setUp(self):
        self.route_table = copy.deepcopy(MOCK_ROUTE_TABLE)
        self.nat_gateway = None
        self.existing_subnet = copy.deepcopy(MOCK_SUBNET)

        self.mock_metanetwork = _get_metanetwork_mock()
        self.mock_ec2_conn = _get_ec2_conn_mock(self)
        self.mock_disco_eip = _get_disco_eip_mock()

        self.subnet = DiscoSubnet(MOCK_SUBNET_NAME, self.mock_metanetwork,
                                  MOCK_CIDR, MOCK_ROUTE_TABLE_ID, self.mock_ec2_conn,
                                  self.mock_disco_eip)

    def test_init_subnet_with_cntrlzd_rt_tbl(self):
        """ Verify that subnet is initialized properly when there is an existing centralized route table """
        self.assertEqual(self.subnet.name, MOCK_SUBNET_NAME)
        self.assertEqual(self.subnet.metanetwork, self.mock_metanetwork)
        self.assertEqual(self.subnet.cidr, MOCK_CIDR)
        self.assertEqual(self.subnet.route_table, MOCK_ROUTE_TABLE)
        self.assertEqual(self.subnet.subnet_dict, MOCK_SUBNET)

        self.mock_ec2_conn.describe_route_tables.assert_called_once_with(
            RouteTableIds=[MOCK_ROUTE_TABLE_ID])
        self.mock_ec2_conn.describe_subnets.assert_called_once_with(
            Filters=[{'Values': [MOCK_VPC_NAME], 'Name': 'tag:meta_network'},
                     {'Values': [MOCK_VPC_ID], 'Name': 'vpc-id'},
                     {'Values': [MOCK_SUBNET_NAME], 'Name': 'availabilityZone'}])

        self.mock_ec2_conn.create_tags.assert_called_once_with(Resources=[MOCK_SUBNET_ID],
                                                               Tags=MOCK_TAG)

    def test_create_brand_new_subnet(self):
        """ Verify that a brand new subnet is properly created """
        self.route_table = None
        self.nat_gateway = None
        self.existing_subnet = None

        self.mock_metanetwork = _get_metanetwork_mock()
        self.mock_ec2_conn = _get_ec2_conn_mock(self)

        self.subnet = DiscoSubnet(MOCK_SUBNET_NAME, self.mock_metanetwork,
                                  MOCK_CIDR, None, self.mock_ec2_conn)

        self.assertEqual(self.subnet.subnet_dict, MOCK_SUBNET)
        self.mock_ec2_conn.create_subnet.assert_called_once_with(
            AvailabilityZone=MOCK_SUBNET_NAME,
            CidrBlock=MOCK_CIDR,
            VpcId=MOCK_VPC_ID)
        self.mock_ec2_conn.create_route_table.assert_called_once_with(VpcId=MOCK_VPC_ID)
        self.mock_ec2_conn.associate_route_table.assert_called_once_with(
            RouteTableId=MOCK_NEW_ROUTE_TABLE['RouteTableId'],
            SubnetId=MOCK_SUBNET_ID)

    def test_init_subnet_with_individual_rt_tbl(self):
        """ Verify that an existing subnet with individual route table is properly initialized """
        self.route_table = copy.deepcopy(MOCK_ROUTE_TABLE)
        self.nat_gateway = None
        self.existing_subnet = copy.deepcopy(MOCK_SUBNET)

        self.mock_metanetwork = _get_metanetwork_mock()
        self.mock_ec2_conn = _get_ec2_conn_mock(self)

        self.subnet = DiscoSubnet(MOCK_SUBNET_NAME, self.mock_metanetwork,
                                  MOCK_CIDR, None, self.mock_ec2_conn)

        self.assertEqual(self.subnet.subnet_dict, MOCK_SUBNET)
        self.mock_ec2_conn.describe_subnets.assert_called_once_with(
            Filters=[{'Values': [MOCK_VPC_NAME], 'Name': 'tag:meta_network'},
                     {'Values': [MOCK_VPC_ID], 'Name': 'vpc-id'},
                     {'Values': [MOCK_SUBNET_NAME], 'Name': 'availabilityZone'}])
        self.mock_ec2_conn.create_subnet.assert_not_called()

        self.assertEqual(self.subnet.route_table, MOCK_ROUTE_TABLE)
        self.mock_ec2_conn.describe_route_tables.assert_called_once_with(
            Filters=[{'Values': [MOCK_VPC_NAME], 'Name': 'tag:meta_network'},
                     {'Values': [MOCK_VPC_ID], 'Name': 'vpc-id'},
                     {'Values': [MOCK_SUBNET_NAME], 'Name': 'tag:subnet'}])
        self.mock_ec2_conn.create_route_table.assert_not_called()

    def test_find_existing_nat_gateway(self):
        """ Verify that an existing NAT gateway is found and associated with the subnet """
        # Setting up existing NAT gateway
        self.nat_gateway = copy.deepcopy(MOCK_NAT_GATEWAY)

        self.subnet.create_nat_gateway(MOCK_ALLOCATION_ID)

        self.assertEqual(self.subnet.nat_eip_allocation_id, MOCK_ALLOCATION_ID)
        self.assertEqual(self.subnet.nat_gateway, MOCK_NAT_GATEWAY)
        self.mock_ec2_conn.describe_nat_gateways.assert_called_once_with(
            Filters=[{'Values': ['available', 'pending'], 'Name': 'state'},
                     {'Values': [MOCK_SUBNET['SubnetId']], 'Name': 'subnet-id'},
                     {'Values': [MOCK_VPC_ID], 'Name': 'vpc-id'}])
        self.mock_ec2_conn.create_nat_gateway.assert_not_called()

    def test_create_nat_gateway(self):
        """ Verify creation of a new NAT gateway for the subnet """
        self.subnet.create_nat_gateway(eip_allocation_id=MOCK_ALLOCATION_ID)

        self.assertEqual(self.subnet.nat_eip_allocation_id, MOCK_ALLOCATION_ID)
        self.assertEqual(self.subnet.nat_gateway, MOCK_NAT_GATEWAY)

        self.mock_ec2_conn.create_nat_gateway.assert_called_once_with(AllocationId=MOCK_ALLOCATION_ID,
                                                                      SubnetId=MOCK_SUBNET['SubnetId'])

    def test_create_dyno_nat_gateway(self):
        """ Verify creation of a new NAT gateway for the subnet """
        self.subnet.create_nat_gateway()

        self.mock_disco_eip.allocate.assert_called_once_with()
        self.assertEqual(self.subnet.nat_eip_allocation_id, MOCK_ALLOCATION_ID)
        self.assertEqual(self.subnet.nat_gateway, MOCK_NAT_GATEWAY)
        self.mock_ec2_conn.create_nat_gateway.assert_called_once_with(AllocationId=MOCK_ALLOCATION_ID,
                                                                      SubnetId=MOCK_SUBNET['SubnetId'])
        self.mock_ec2_conn.create_tags.assert_has_calls(
            [call(Resources=[MOCK_SUBNET_ID],
                  Tags=[{'Key': DYNO_NAT_TAG_KEY, 'Value': ''}])])

    def test_delete_nat_gateway(self):
        """ Verify that a NAT gateway can be properly deleted """
        self.subnet.create_nat_gateway(MOCK_ALLOCATION_ID)

        self.subnet.delete_nat_gateway()

        self.assertEqual(self.subnet.nat_eip_allocation_id, None)
        self.assertEqual(self.subnet.nat_gateway, None)

    def test_delete_dyno_nat_gateway(self):
        """ Verify that a NAT gateway created with a dynamic EIP can be properly deleted """
        self.subnet.create_nat_gateway()
        self.existing_subnet['Tags'].append({'Key': DYNO_NAT_TAG_KEY, 'Value': ''})

        self.subnet.delete_nat_gateway()

        self.assertEqual(self.subnet.nat_eip_allocation_id, None)
        self.assertEqual(self.subnet.nat_gateway, None)
        self.mock_disco_eip.release.assert_called_once_with(MOCK_PUBLIC_IP)
        self.mock_ec2_conn.delete_tags.assert_has_calls(
            [call(Resources=[MOCK_SUBNET_ID], Tags=[{'Key': DYNO_NAT_TAG_KEY}])])

    def test_recreate_route_table(self):
        """ Verify a new route table is created to replace an existing one """
        self.subnet.recreate_route_table()

        calls = []
        for route in MOCK_ROUTE_TABLE['Routes']:
            params = {'RouteTableId': MOCK_NEW_ROUTE_TABLE['RouteTableId'],
                      'DestinationCidrBlock': route['DestinationCidrBlock']}
            if route.get('GatewayId'):
                params['GatewayId'] = route.get('GatewayId')
            if route.get('InstanceId'):
                params['InstanceId'] = route.get('InstanceId')
            if route.get('NetworkInterfaceId'):
                params['NetworkInterfaceId'] = route.get('NetworkInterfaceId')
            if route.get('VpcPeeringConnectionId'):
                params['VpcPeeringConnectionId'] = route.get('VpcPeeringConnectionId')
            if route.get('NatGatewayId'):
                params['NatGatewayId'] = route.get('NatGatewayId')
            calls.append(call(**params))
        self.mock_ec2_conn.create_route.assert_has_calls(calls)
        self.mock_ec2_conn.disassociate_route_table.assert_called_once_with(
            AssociationId=MOCK_ROUTE_TABLE_ASSOC_ID)
        self.assertEqual(self.subnet.route_table, MOCK_NEW_ROUTE_TABLE)

        self.mock_ec2_conn.create_tags.assert_called_with(Resources=[MOCK_NEW_ROUTE_TABLE['RouteTableId']],
                                                          Tags=MOCK_TAG)

    def test_create_new_peering_route(self):
        """ Verify that a peering route is properly created """
        new_peering_conn_id = 'new_peering_conn_id'
        new_cidr1 = '33.33.33.33/24'
        self.subnet.create_peering_routes(new_peering_conn_id, new_cidr1)

        new_cidr2 = '33.33.33.44/24'
        self.subnet.create_peering_routes(new_peering_conn_id, new_cidr2)

        expected_calls = [call(DestinationCidrBlock=new_cidr1,
                               RouteTableId=MOCK_ROUTE_TABLE_ID,
                               VpcPeeringConnectionId=new_peering_conn_id),
                          call(DestinationCidrBlock=new_cidr2,
                               RouteTableId=MOCK_ROUTE_TABLE_ID,
                               VpcPeeringConnectionId=new_peering_conn_id)]
        self.mock_ec2_conn.create_route.assert_has_calls(expected_calls)

        # Make sure route table is updated
        added_routes = [route for route in self.subnet.route_table['Routes']
                        if route['DestinationCidrBlock'] in [new_cidr1, new_cidr2] and
                        route['VpcPeeringConnectionId'] == new_peering_conn_id]
        self.assertEqual(len(added_routes), 2)

    def test_replace_peering_route(self):
        """ Verify that an existing peering route is properly replaced by a new one """
        new_peering_conn_id = 'new_peering_conn_id'

        self.subnet.create_peering_routes(new_peering_conn_id, MOCK_REPLACE_CIDR)

        self.mock_ec2_conn.replace_route.assert_called_once_with(DestinationCidrBlock=MOCK_REPLACE_CIDR,
                                                                 RouteTableId=MOCK_ROUTE_TABLE_ID,
                                                                 VpcPeeringConnectionId=new_peering_conn_id)

        # Make sure route table is updated
        update_route = [route for route in self.subnet.route_table['Routes']
                        if route['DestinationCidrBlock'] == MOCK_REPLACE_CIDR and
                        route['VpcPeeringConnectionId'] == new_peering_conn_id]
        self.assertTrue(update_route)

    def test_add_route_to_gateway(self):
        """ Verify that a new route can be created """
        destination_cidr_block = '44.44.44.44/24'
        gateway_id = 'temp_gateway_id'

        self.subnet.add_route_to_gateway(destination_cidr_block, gateway_id)

        self.mock_ec2_conn.create_route.assert_called_once_with(DestinationCidrBlock=destination_cidr_block,
                                                                GatewayId=gateway_id,
                                                                RouteTableId=MOCK_ROUTE_TABLE_ID)

        # Make sure route table is updated
        route_to_nat = [route for route in self.subnet.route_table['Routes']
                        if route['DestinationCidrBlock'] == destination_cidr_block and
                        route['GatewayId'] == gateway_id]
        self.assertTrue(route_to_nat)

    def test_upsert_route_to_nat_gateway(self):
        """ Verify that a new route to a NAT gateway can be created properly """
        self.subnet.create_nat_gateway(eip_allocation_id=MOCK_ALLOCATION_ID)

        # Calling method under test
        self.subnet.upsert_route_to_nat_gateway('0.0.0.0/0', MOCK_NAT_GATEWAY_ID)

        # Verification
        nat_gateway_route = [route for route in self.route_table['Routes']
                             if route.get('DestinationCidrBlock') == '0.0.0.0/0']
        self.assertTrue(len(nat_gateway_route) == 1)
        self.assertEqual(nat_gateway_route[0]['NatGatewayId'], MOCK_NAT_GATEWAY_ID)

    def test_change_to_new_nat_gateway(self):
        """ Verify that updating NAT gateway with a new EIP can be done properly """
        self.subnet.create_nat_gateway(eip_allocation_id=MOCK_ALLOCATION_ID)
        self.subnet.upsert_route_to_nat_gateway('0.0.0.0/0', MOCK_NAT_GATEWAY_ID)

        new_mock_nat_gateway_id = 'new_mock_nat_gateway_id'

        # Calling method under test
        self.subnet.create_nat_gateway()
        self.subnet.upsert_route_to_nat_gateway('0.0.0.0/0', new_mock_nat_gateway_id)

        # Verification
        self.mock_ec2_conn.delete_nat_gateway.assert_called_once_with(NatGatewayId=MOCK_NAT_GATEWAY_ID)
        expected_create_calls = [call(AllocationId=MOCK_ALLOCATION_ID, SubnetId=MOCK_SUBNET_ID),
                                 call(AllocationId=MOCK_ALLOCATION_ID, SubnetId=MOCK_SUBNET_ID)]
        self.mock_ec2_conn.create_nat_gateway.assert_has_calls(expected_create_calls)

        nat_gateway_route = [route for route in self.route_table['Routes']
                             if route.get('DestinationCidrBlock') == '0.0.0.0/0']
        self.assertTrue(len(nat_gateway_route) == 1)
        self.assertEqual(nat_gateway_route[0]['NatGatewayId'], new_mock_nat_gateway_id)
