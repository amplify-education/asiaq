"""
This module contains logic that processes a VPC's peering connections
"""

import logging

from boto.exception import EC2ResponseError
import boto3

from . import read_config
from .resource_helper import tag2dict, create_filters
from .exceptions import VPCPeeringSyntaxError
# FIXME: Disabling complaint about relative-import. This seems to be the only
# way that works for unit tests.
# pylint: disable=W0403
import disco_vpc
from .disco_constants import VPC_CONFIG_FILE

LIVE_PEERING_STATES = ["pending-acceptance", "provisioning", "active"]


class DiscoVPCPeerings(object):
    """
    This class takes care of processing of a VPC's peering connections
    """
    def __init__(self, vpc, boto3_ec2):
        self.disco_vpc = vpc
        self.boto3_ec2 = boto3_ec2

    def update_peering_connections(self, dry_run=False):
        """ Update peering connections for a VPC """
        desired_peerings = self.parse_peering_strs_config(self.disco_vpc.environment_name)
        existing_peerings = self._get_existing_peerings()

        logging.info("Desired VPC peering connections: %s", desired_peerings)
        logging.info("Existing VPC peering connections: %s", existing_peerings)

        if existing_peerings > desired_peerings:
            raise RuntimeError("Some existing VPC peering connections are not "
                               "defined in the configuration: {0}. Deletion of VPC peerings is "
                               "not implemented yet."
                               .format(existing_peerings - desired_peerings))

        peerings_config = self.parse_peerings_config(self.disco_vpc.get_vpc_id())
        logging.debug("Desired VPC peering config: %s", peerings_config)
        if not dry_run:
            DiscoVPCPeerings.create_peering_connections(peerings_config)

    def _get_existing_peerings(self):
        current_peerings = set()

        for peering in DiscoVPCPeerings.list_peerings(self.disco_vpc.get_vpc_id()):

            peer_vpc = self._find_peer_vpc(self._get_peer_vpc_id(peering))

            vpc_peering_route_tables = self.boto3_ec2.describe_route_tables(
                Filters=create_filters(
                    {'route.vpc-peering-connection-id': [peering['VpcPeeringConnectionId']]})
            )['RouteTables']

            for route_table in vpc_peering_route_tables:
                tags_dict = tag2dict(route_table['Tags'])

                subnet_name_parts = tags_dict['Name'].split('_')
                if subnet_name_parts[0] == self.disco_vpc.environment_name:
                    source_network = subnet_name_parts[0] + ':' + \
                        self.disco_vpc.environment_type + '/' + \
                        subnet_name_parts[1]

                    for route in route_table['Routes']:
                        if route.get('VpcPeeringConnectionId') == peering['VpcPeeringConnectionId']:
                            dest_network = peer_vpc.environment_name + ':' + \
                                peer_vpc.environment_type + '/' + \
                                [network.name for network in peer_vpc.networks.values()
                                 if str(network.network_cidr) == route['DestinationCidrBlock']][0]

                            current_peerings.add(source_network + ' ' + dest_network)

        return current_peerings

    def _get_peer_vpc_id(self, peering):
        if peering['AccepterVpcInfo']['VpcId'] != self.disco_vpc.get_vpc_id():
            return peering['AccepterVpcInfo']['VpcId']
        else:
            return peering['RequesterVpcInfo']['VpcId']

    def _find_peer_vpc(self, peer_vpc_id):
        try:
            peer_vpc = self.boto3_ec2.describe_vpcs(VpcIds=[peer_vpc_id])['Vpcs'][0]
        except IndexError:
            raise RuntimeError("Failed to retrieve VPC with vpc_id: {0}".format(peer_vpc_id))

        try:
            vpc_tags_dict = tag2dict(peer_vpc['Tags'])

            return disco_vpc.DiscoVPC(vpc_tags_dict['Name'], vpc_tags_dict['type'], peer_vpc)
        except UnboundLocalError:
            raise RuntimeError("VPC {0} is missing tags: 'Name', 'type'.".format(peer_vpc_id))

    @staticmethod
    def create_peering_connections(peering_configs):
        """ create vpc peering configuration from the peering config dictionary"""
        client = boto3.client('ec2')
        for peering in peering_configs.keys():
            vpc_map = peering_configs[peering]['vpc_map']
            vpc_metanetwork_map = peering_configs[peering]['vpc_metanetwork_map']
            vpc_ids = [vpc.vpc['VpcId'] for vpc in vpc_map.values()]
            existing_peerings = client.describe_vpc_peering_connections(
                Filters=create_filters({'status-code': ['active'],
                                        'accepter-vpc-info.vpc-id': [vpc_ids[0]],
                                        'requester-vpc-info.vpc-id': [vpc_ids[1]]})
            )['VpcPeeringConnections'] + client.describe_vpc_peering_connections(
                Filters=create_filters({'status-code': ['active'],
                                        'accepter-vpc-info.vpc-id': [vpc_ids[1]],
                                        'requester-vpc-info.vpc-id': [vpc_ids[0]]})
            )['VpcPeeringConnections']

            # create peering when peering doesn't exist
            if not existing_peerings:
                peering_conn = client.create_vpc_peering_connection(
                    VpcId=vpc_ids[0], PeerVpcId=vpc_ids[1])['VpcPeeringConnection']
                client.accept_vpc_peering_connection(
                    VpcPeeringConnectionId=peering_conn['VpcPeeringConnectionId'])
                logging.info("Created new peering connection %s for %s",
                             peering_conn['VpcPeeringConnectionId'], peering)
            else:
                peering_conn = existing_peerings[0]
                logging.info("Peering connection %s exists for %s",
                             existing_peerings[0]['VpcPeeringConnectionId'], peering)
            DiscoVPCPeerings.create_peering_routes(vpc_map, vpc_metanetwork_map, peering_conn)

    @staticmethod
    def create_peering_routes(vpc_map, vpc_metanetwork_map, peering_conn):
        """ create/update routes via peering connections between VPCs """
        cidr_map = {
            _: vpc_map[_].networks[vpc_metanetwork_map[_]].network_cidr
            for _ in vpc_map.keys()
        }
        network_map = {
            _: vpc_map[_].networks[vpc_metanetwork_map[_]]
            for _ in vpc_map.keys()
        }
        for vpc_name, network in network_map.iteritems():
            remote_vpc_names = vpc_map.keys()
            remote_vpc_names.remove(vpc_name)

            network.create_peering_route(peering_conn['VpcPeeringConnectionId'],
                                         str(cidr_map[remote_vpc_names[0]]))

    @staticmethod
    def parse_peerings_config(vpc_id=None):
        """
        Parses configuration from disco_vpc.ini's peerings sections.
        If vpc_id is specified, only configuration relevant to vpc_id is included.
        """
        peerings = DiscoVPCPeerings._get_peering_lines()

        client = boto3.client('ec2')
        peering_configs = {}
        for peering in peerings:
            peering_config = DiscoVPCPeerings.parse_peering_connection_line(peering, client)
            vpc_ids_in_peering = [vpc.vpc['VpcId'] for vpc in peering_config.get("vpc_map", {}).values()]

            if len(vpc_ids_in_peering) < 2:
                pass  # not all vpcs were up, nothing to do
            elif vpc_id and vpc_id not in vpc_ids_in_peering:
                logging.debug("Skipping peering %s because it doesn't include %s", peering, vpc_id)
            else:
                peering_configs[peering] = peering_config

        return peering_configs

    @staticmethod
    def _get_peering_lines():
        logging.debug("Parsing peerings configuration specified in %s", VPC_CONFIG_FILE)
        config = read_config(VPC_CONFIG_FILE)

        if 'peerings' not in config.sections():
            logging.info("No VPC peering configuration defined.")
            return {}

        peerings = [
            peering[1]
            for peering in config.items('peerings')
            if peering[0].startswith('connection_')
        ]

        for peering in peerings:
            endpoints = [_.strip() for _ in peering.split(' ')]
            if len(endpoints) != 2:
                raise VPCPeeringSyntaxError(
                    "Syntax error in vpc peering connection. "
                    "Expected 2 space-delimited endpoints but found: '{}'".format(peering))

        return peerings

    @staticmethod
    def parse_peering_strs_config(source_vpc_name):
        """
        Return a set of peering string from the VPC's config file
        """
        def _parse_endponit(endpoint):
            endpoint_parts = endpoint.split('/')
            vpc_parts = endpoint_parts[0].strip().split(':')
            vpc_name = vpc_parts[0].strip()
            vpc_type = vpc_parts[-1].strip()
            network_name = endpoint_parts[1].strip()

            return vpc_name, vpc_name + ':' + vpc_type + '/' + network_name

        peering_strs = set()
        peering_lines = DiscoVPCPeerings._get_peering_lines()
        for line in peering_lines:
            endpoints_map = {}
            for endpoint in line.split(' '):
                endpoint = _parse_endponit(endpoint)
                endpoints_map[endpoint[0]] = endpoint[1]

            if source_vpc_name in endpoints_map.keys():
                peer_vpc_name = [vpc_name for vpc_name in endpoints_map.keys()
                                 if vpc_name != source_vpc_name][0]

                peering_strs.add(endpoints_map[source_vpc_name] +
                                 ' ' + endpoints_map[peer_vpc_name])

        return peering_strs

    @staticmethod
    def parse_peering_connection_line(line, vpc_conn):
        """
        Parses vpc connections of the form `vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork`
        and returns the data in two dictionaries: vpc_name -> DiscoVPC instance and vpc_name -> metanetwork.
        vpc_type defaults to vpc_name if unspecified.
        """
        logging.debug('checking existence for peering %s', line)
        endpoints = line.split(' ')

        def get_vpc_name(endpoint):
            """return name from `name[:type]/metanetwork`"""
            return endpoint.split('/')[0].split(':')[0].strip()

        def get_vpc_type(endpoint):
            """return type from `name[:type]/metanetwork`, defaulting to name if type is omitted"""
            return endpoint.split('/')[0].split(':')[-1].strip()

        def get_metanetwork(endpoint):
            """return metanetwork from `name[:type]/metanetwork`"""
            return endpoint.split('/')[1].strip()

        def safe_get_from_list(_list, i):
            """returns the i-th element in a list, or None if it doesn't exist"""
            return _list[i] if _list and len(_list) > i else None

        vpc_type_map = {
            get_vpc_name(endpoint): get_vpc_type(endpoint)
            for endpoint in endpoints
        }

        vpc_objects = {
            vpc_name: safe_get_from_list(
                vpc_conn.describe_vpcs(Filters=create_filters({'tag-value': [vpc_name]}))['Vpcs'], 0)
            for vpc_name in vpc_type_map.keys()
        }

        missing_vpcs = [vpc_name for vpc_name, vpc_object in vpc_objects.items() if not vpc_object]
        if missing_vpcs:
            logging.debug(
                "Skipping peering %s because the following VPC(s) are not up: %s",
                line, ", ".join(map(str, missing_vpcs)))
            return {}

        vpc_map = {
            k: disco_vpc.DiscoVPC(k, v, vpc_objects[k])
            for k, v in vpc_type_map.iteritems()
        }

        for vpc in vpc_map.values():
            if not vpc.networks:
                raise RuntimeError("No metanetworks found for vpc {}. Are you sure it's of type {}?".format(
                    vpc.environment_name, vpc.environment_type))

        vpc_metanetwork_map = {
            get_vpc_name(endpoint): get_metanetwork(endpoint)
            for endpoint in endpoints
        }

        return {
            'vpc_metanetwork_map': vpc_metanetwork_map,
            'vpc_map': vpc_map
        }

    @staticmethod
    def delete_peerings(vpc_id=None):
        """Delete peerings. If vpc_id is specified, delete all peerings of the VPCs only"""
        client = boto3.client('ec2')
        for peering in DiscoVPCPeerings.list_peerings(vpc_id):
            try:
                logging.info('deleting peering connection %s', peering['VpcPeeringConnectionId'])
                client.delete_vpc_peering_connection(VpcPeeringConnectionId=peering['VpcPeeringConnectionId'])
            except EC2ResponseError:
                raise RuntimeError('Failed to delete VPC Peering connection \
                                    {}'.format(peering['VpcPeeringConnectionId']))

    @staticmethod
    def list_peerings(vpc_id=None, include_failed=False):
        """
        Return list of live vpc peering connection id.
        If vpc_id is given, return only that vpcs peerings
        Peerings that cannot be manipulated are ignored.
        """
        client = boto3.client('ec2')
        if vpc_id:
            peerings = client.describe_vpc_peering_connections(
                Filters=create_filters({'requester-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections'] + client.describe_vpc_peering_connections(
                Filters=create_filters({'accepter-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections']
        else:
            peerings = client.describe_vpc_peering_connections()['VpcPeeringConnections']

        peering_states = LIVE_PEERING_STATES + (["failed"] if include_failed else [])
        return [
            peering
            for peering in peerings
            if peering['Status']['Code'] in peering_states
        ]
