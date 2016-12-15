"""
This module contains logic that processes a VPC's peering connections
"""

import logging
from sets import ImmutableSet

from itertools import product

from boto.exception import EC2ResponseError
import boto3

# FIXME: Disabling complaint about relative-import. This seems to be the only
# way that works for unit tests.
# pylint: disable=W0403
import disco_vpc

from .disco_config import read_config
from .resource_helper import tag2dict, create_filters, throttled_call
from .exceptions import VPCPeeringSyntaxError, VPCConfigError
from .disco_constants import VPC_CONFIG_FILE

logger = logging.getLogger(__name__)

LIVE_PEERING_STATES = ["pending-acceptance", "provisioning", "active"]


class DiscoVPCPeerings(object):
    """
    This class takes care of processing of a VPC's peering connections
    """

    def __init__(self, boto3_ec2=None):
        if boto3_ec2:
            self.client = boto3_ec2
        else:
            self.client = boto3.client('ec2')

    def update_peering_connections(self, vpc, dry_run=False, delete_extra_connections=False):
        """ Update peering connections for a VPC """
        desired_peerings = self._get_peerings_from_config(vpc.get_vpc_id())
        existing_peerings = self._get_existing_peerings(vpc)

        logger.info("Desired VPC peering connections: %s", desired_peerings)
        logger.info("Existing VPC peering connections: %s", existing_peerings)

        if delete_extra_connections and existing_peerings > desired_peerings:
            raise RuntimeError("Some existing VPC peering connections are not "
                               "defined in the configuration: {0}. Deletion of VPC peerings is "
                               "not implemented yet."
                               .format(existing_peerings - desired_peerings))

        if not dry_run:
            self._create_peering_connections(desired_peerings - existing_peerings)
            self._create_peering_routes(desired_peerings)

    def _get_existing_peerings(self, vpc):
        """
        Get the set of PeeringConnections for the existing peerings for given DiscoVPC object
        """
        current_peerings = set()
        for peering in self.list_peerings(vpc.get_vpc_id()):

            peer_vpc = self._find_peer_vpc(self._get_peer_vpc_id(vpc.get_vpc_id(), peering))
            if not peer_vpc:
                logger.warning("Failed to find the peer VPC (%s) associated with peering (%s). "
                               "If the VPC no longer exists, please delete the peering manually.",
                               peer_vpc['VpcId'], peering['VpcPeeringConnectionId'])
                continue

            for route_table in self._get_peering_route_tables(peering['VpcPeeringConnectionId']):
                tags_dict = tag2dict(route_table['Tags'])

                subnet_env, subnet_network = tags_dict['Name'].split('_')[:2]
                if subnet_env == vpc.environment_name:
                    source_endpoint = PeeringEndpoint(
                        vpc.environment_name,
                        vpc.environment_type,
                        subnet_network,
                        vpc.vpc
                    )

                    # find the metanetwork of the peering connection by matching the peering routes with
                    # the CIDRs of the VPC metanetworks
                    route_cidrs = [
                        route['DestinationCidrBlock'] for route in route_table['Routes']
                        if route.get('VpcPeeringConnectionId') == peering['VpcPeeringConnectionId']
                    ]

                    peered_networks = [network for network in peer_vpc.networks.values()
                                       if str(network.network_cidr) in route_cidrs]

                    if peered_networks:
                        target_endpoint = PeeringEndpoint(
                            peer_vpc.environment_name,
                            peer_vpc.environment_type,
                            peered_networks[0].name,
                            peer_vpc.vpc
                        )

                        current_peerings.add(PeeringConnection(source_endpoint, target_endpoint))

        return current_peerings

    def _get_peering_route_tables(self, peering_conn_id):
        """ Get all of the route tables associated with a given peering connection """
        return throttled_call(
            self.client.describe_route_tables,
            Filters=create_filters({
                'route.vpc-peering-connection-id': [peering_conn_id]
            })
        )['RouteTables']

    def _get_peer_vpc_id(self, vpc_id, peering):
        if peering['AccepterVpcInfo']['VpcId'] != vpc_id:
            return peering['AccepterVpcInfo']['VpcId']
        else:
            return peering['RequesterVpcInfo']['VpcId']

    def _find_peer_vpc(self, peer_vpc_id):
        try:
            peer_vpc = throttled_call(self.client.describe_vpcs, VpcIds=[peer_vpc_id])['Vpcs'][0]
        except Exception:
            return None

        try:
            vpc_tags_dict = tag2dict(peer_vpc['Tags'])

            return disco_vpc.DiscoVPC(vpc_tags_dict['Name'], vpc_tags_dict['type'], peer_vpc)
        except UnboundLocalError:
            raise RuntimeError("VPC {0} is missing tags: 'Name', 'type'.".format(peer_vpc_id))

    def _create_peering_connections(self, peerings):
        """ Create peerings in AWS for the given PeeringConnection objects"""

        peering_conns = [self._create_peering_conn(peering) for peering in peerings]

        peering_conn_ids = [peering_conn['VpcPeeringConnectionId'] for peering_conn in peering_conns]

        # wait for the peering connection to be ready
        waiter = self.client.get_waiter('vpc_peering_connection_exists')
        waiter.wait(
            VpcPeeringConnectionIds=peering_conn_ids,
            Filters=[{'Name': 'status-code', 'Values': LIVE_PEERING_STATES}]
        )

        for peering_conn in peering_conns:
            throttled_call(
                self.client.accept_vpc_peering_connection,
                VpcPeeringConnectionId=peering_conn['VpcPeeringConnectionId']
            )

    def _create_peering_conn(self, peering):
        """ Create a AWS peering connection for the given peering config object """
        return throttled_call(
            self.client.create_vpc_peering_connection,
            VpcId=peering.source_endpoint.vpc['VpcId'],
            PeerVpcId=peering.target_endpoint.vpc['VpcId']
        )['VpcPeeringConnection']

    def _create_peering_routes(self, peerings):
        """ create/update routes via peering connections between VPCs """
        connection_map = {}
        for peering_connection in self.list_peerings():
            source_target_key = '%s-%s' % (peering_connection['AccepterVpcInfo']['VpcId'],
                                           peering_connection['RequesterVpcInfo']['VpcId'])
            connection_map[source_target_key] = peering_connection['VpcPeeringConnectionId']

            target_source_key = '%s-%s' % (peering_connection['RequesterVpcInfo']['VpcId'],
                                           peering_connection['AccepterVpcInfo']['VpcId'])
            connection_map[target_source_key] = peering_connection['VpcPeeringConnectionId']

        for peering in peerings:
            source_vpc = disco_vpc.DiscoVPC(peering.source_endpoint.name,
                                            peering.source_endpoint.type,
                                            peering.source_endpoint.vpc)

            target_vpc = disco_vpc.DiscoVPC(peering.target_endpoint.name,
                                            peering.target_endpoint.type,
                                            peering.target_endpoint.vpc)

            source_network = source_vpc.networks[peering.source_endpoint.metanetwork]
            target_network = target_vpc.networks[peering.target_endpoint.metanetwork]

            peering_conn_key = '%s-%s' % (peering.source_endpoint.vpc['VpcId'],
                                          peering.target_endpoint.vpc['VpcId'])

            if peering_conn_key in connection_map:
                vpc_peering_conn_id = connection_map[peering_conn_key]
            else:
                raise RuntimeError('Peering connection %s not found. Cannot create routes' % peering_conn_key)

            source_network.create_peering_route(
                vpc_peering_conn_id,
                str(target_network.network_cidr)
            )

            target_network.create_peering_route(
                vpc_peering_conn_id,
                str(source_network.network_cidr)
            )

    def _get_peerings_from_config(self, vpc_id=None):
        """
        Parses configuration from disco_vpc.ini's peerings sections.
        If vpc_id is specified, only configuration relevant to vpc_id is included.
        """
        peering_configs = set()
        for peering in self._get_peering_lines():
            # resolve the peering line into a list of PeeringConnection objects
            # a single peering line might resolve to multiple peerings if there are wildcards
            resolved_peerings = self._resolve_peering_connection_line(peering)
            for resolved_peering in resolved_peerings:
                if vpc_id and not resolved_peering.contains_vpc_id(vpc_id):
                    logger.debug("Skipping peering %s because it doesn't include %s", peering, vpc_id)
                else:
                    peering_configs.add(resolved_peering)

        return peering_configs

    def _get_peering_lines(self):
        logger.debug("Parsing peerings configuration specified in %s", VPC_CONFIG_FILE)
        config = read_config(VPC_CONFIG_FILE)

        if 'peerings' not in config.sections():
            logger.info("No VPC peering configuration defined.")
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

    def delete_peerings(self, vpc_id=None):
        """Delete peerings. If vpc_id is specified, delete all peerings of the VPCs only"""
        for peering in self.list_peerings(vpc_id):
            try:
                logger.info('deleting peering connection %s', peering['VpcPeeringConnectionId'])
                throttled_call(
                    self.client.delete_vpc_peering_connection,
                    VpcPeeringConnectionId=peering['VpcPeeringConnectionId']
                )
            except EC2ResponseError:
                raise RuntimeError(
                    'Failed to delete VPC Peering connection {}'.format(peering['VpcPeeringConnectionId'])
                )

    def list_peerings(self, vpc_id=None, include_failed=False):
        """
        Return list of live vpc peering connection id.
        If vpc_id is given, return only that vpcs peerings
        Peerings that cannot be manipulated are ignored.
        """
        if vpc_id:
            peerings = throttled_call(
                self.client.describe_vpc_peering_connections,
                Filters=create_filters({'requester-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections']

            peerings += throttled_call(
                self.client.describe_vpc_peering_connections,
                Filters=create_filters({'accepter-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections']
        else:
            peerings = throttled_call(self.client.describe_vpc_peering_connections)['VpcPeeringConnections']

        peering_states = LIVE_PEERING_STATES + (["failed"] if include_failed else [])
        return [
            peering
            for peering in peerings
            if peering['Status']['Code'] in peering_states
        ]

    def _resolve_peering_connection_line(self, line):
        """
        Resolve a peering connection line into a set of PeeringConnections. Expand any wildcards

        Args:
            line (str): A peering line like `vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork`
                        `vpc_name` may be the name of a VPC or a `*` wildcard to peer with any VPC of vpc_type
        """

        # convert the config line into a PeeringConnection but it may contain wildcards
        unresolved_peering = PeeringConnection.from_peering_line(line)

        # get all VPCs created through Asiaq. Ones that have type and Name tags
        existing_vpcs = [vpc for vpc in throttled_call(self.client.describe_vpcs).get('Vpcs', [])
                         if all(tag in tag2dict(vpc.get('Tags', [])) for tag in ['type', 'Name'])]

        def resolve_endpoint(endpoint):
            """
            Convert a PeeringEndpoint that may contain wildcards into a list of PeeringEndpoints
            with wildcards resolved
            """
            endpoints = []
            for vpc in existing_vpcs:
                tags = tag2dict(vpc['Tags'])
                vpc_name = tags['Name']
                vpc_type = tags['type']

                if endpoint.match_vpc(vpc_name, vpc_type):
                    endpoints.append(PeeringEndpoint(vpc_name, vpc_type, endpoint.metanetwork, vpc))

            return endpoints

        # find the VPCs that match the peering config. Replace wildcards with real VPC names
        source_endpoints = resolve_endpoint(unresolved_peering.source_endpoint)
        target_endpoints = resolve_endpoint(unresolved_peering.target_endpoint)

        # generate new connection lines by peering the cross product of every source and target endpoint
        return {PeeringConnection(source, target)
                for source, target in product(source_endpoints, target_endpoints)
                # Don't peer a VPC with itself
                if not source == target}


class PeeringEndpoint(object):
    """
    Represents one side of a PeeringConnection
    """
    def __init__(self, env_name, env_type, metanetwork, vpc=None):
        self.name = env_name
        self.type = env_type
        self.metanetwork = metanetwork
        self.vpc = vpc

    @staticmethod
    def from_endpoint_str(endpoint):
        """ Get a PeeringEndpoint from one of the sides of a peering config """
        parts = endpoint.split('/')

        vpc_name = parts[0].split(':')[0].strip()

        # get type from `name[:type]/metanetwork`, defaulting to name if type is omitted
        vpc_type = parts[0].split(':')[-1].strip()

        # get metanetwork from `name[:type]/metanetwork`
        metanetwork = parts[1].strip()

        if vpc_type == '*':
            raise VPCConfigError(
                'Wildcards are not allowed for VPC type in "%s". '
                'Please specify a VPC type when using a wild card for the VPC name' % endpoint
            )

        return PeeringEndpoint(vpc_name, vpc_type, metanetwork)

    def match_vpc(self, vpc_name, vpc_type):
        """ Return True if the given vpc type and name match the VPC of this endpoint """
        return self.name in ('*', vpc_name) and self.type == vpc_type

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        return hash(str(self))

    def __str__(self):
        return '%s:%s/%s' % (
            self.name,
            self.type,
            self.metanetwork
        )


class PeeringConnection(object):
    """
    Represents a connection between two different VPCs
    """
    def __init__(self, source_endpoint, target_endpoint):
        """
        Args:
            source_endpoint (PeeringEndpoint): source side of connection
            target_endpoint (PeeringEndpoint): target side of connection
        """
        self.source_endpoint = source_endpoint
        self.target_endpoint = target_endpoint

    def contains_vpc_name(self, vpc_name):
        """ Return true if the given vpc_name is the name of a VPC on one of the sides of the connection"""
        return self.source_endpoint.name == vpc_name or self.target_endpoint.name == vpc_name

    def contains_vpc_id(self, vpc_id):
        """ Return true if the given vpc_id is the id of a VPC on one of the sides of the connection"""
        return self.source_endpoint.vpc['VpcId'] == vpc_id or self.target_endpoint.vpc['VpcId'] == vpc_id

    @staticmethod
    def from_peering_line(line):
        """ Parse a peering connection config line into a PeeringConnection object """
        endpoints = line.split(' ')
        if not len(endpoints) == 2:
            raise VPCConfigError('Invalid peering config "%s". Peering config must be of the format '
                                 'vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork' % line)

        source_peering = PeeringEndpoint.from_endpoint_str(endpoints[0])
        target_peering = PeeringEndpoint.from_endpoint_str(endpoints[1])

        return PeeringConnection(source_peering, target_peering)

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __hash__(self):
        # use a immutable set because regular sets aren't hashable
        return hash(ImmutableSet([self.source_endpoint, self.target_endpoint]))

    def __str__(self):
        return str(self.source_endpoint) + ' ' + str(self.target_endpoint)

    def __repr__(self):
        return str(self)
