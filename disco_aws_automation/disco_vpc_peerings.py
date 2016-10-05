"""
This module contains logic that processes a VPC's peering connections
"""

import logging
from itertools import product

from boto.exception import EC2ResponseError
import boto3

from . import read_config
from .resource_helper import tag2dict, create_filters, throttled_call
from .exceptions import VPCPeeringSyntaxError, VPCConfigError
# FIXME: Disabling complaint about relative-import. This seems to be the only
# way that works for unit tests.
# pylint: disable=W0403
import disco_vpc
from .disco_constants import VPC_CONFIG_FILE

logger = logging.getLogger(__name__)

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

        logger.info("Desired VPC peering connections: %s", desired_peerings)
        logger.info("Existing VPC peering connections: %s", existing_peerings)

        if existing_peerings > desired_peerings:
            raise RuntimeError("Some existing VPC peering connections are not "
                               "defined in the configuration: {0}. Deletion of VPC peerings is "
                               "not implemented yet."
                               .format(existing_peerings - desired_peerings))

        peerings_config = self.parse_peerings_config(self.disco_vpc.get_vpc_id())
        logger.debug("Desired VPC peering config: %s", peerings_config)
        if not dry_run:
            DiscoVPCPeerings.create_peering_connections(peerings_config)

    def _get_existing_peerings(self):
        current_peerings = set()

        for peering in DiscoVPCPeerings.list_peerings(self.disco_vpc.get_vpc_id()):

            peer_vpc_id = self._get_peer_vpc_id(peering)
            peer_vpc = self._find_peer_vpc(peer_vpc_id)
            if not peer_vpc:
                logger.warning("Failed to find the peer VPC (%s) associated with peering (%s). "
                               "If the VPC no longer exists, please delete the peering manually.",
                               peer_vpc_id, peering['VpcPeeringConnectionId'])
                continue

            peering_query = create_filters(
                {'route.vpc-peering-connection-id': [peering['VpcPeeringConnectionId']]}
            )

            vpc_peering_route_tables = throttled_call(self.boto3_ec2.describe_route_tables,
                                                      Filters=peering_query)['RouteTables']

            for route_table in vpc_peering_route_tables:
                tags_dict = tag2dict(route_table['Tags'])

                subnet_name_parts = tags_dict['Name'].split('_')
                if subnet_name_parts[0] == self.disco_vpc.environment_name:
                    source_network = subnet_name_parts[0] + ':' + \
                        self.disco_vpc.environment_type + '/' + \
                        subnet_name_parts[1]

                    route_cidrs = [route['DestinationCidrBlock']
                                   for route in route_table['Routes']
                                   if route.get('VpcPeeringConnectionId') ==
                                   peering['VpcPeeringConnectionId']]

                    for network in peer_vpc.networks.values():
                        if str(network.network_cidr) in route_cidrs:
                            dest_network = peer_vpc.environment_name + ':' + \
                                peer_vpc.environment_type + '/' + network.name

                            current_peerings.add(source_network + ' ' + dest_network)

        return current_peerings

    def _get_peer_vpc_id(self, peering):
        if peering['AccepterVpcInfo']['VpcId'] != self.disco_vpc.get_vpc_id():
            return peering['AccepterVpcInfo']['VpcId']
        else:
            return peering['RequesterVpcInfo']['VpcId']

    def _find_peer_vpc(self, peer_vpc_id):
        try:
            peer_vpc = throttled_call(self.boto3_ec2.describe_vpcs, VpcIds=[peer_vpc_id])['Vpcs'][0]
        except Exception:
            return None

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

            existing_peerings = throttled_call(
                client.describe_vpc_peering_connections,
                Filters=create_filters({'status-code': ['active'],
                                        'accepter-vpc-info.vpc-id': [vpc_ids[0]],
                                        'requester-vpc-info.vpc-id': [vpc_ids[1]]})
            )['VpcPeeringConnections']

            existing_peerings += throttled_call(
                client.describe_vpc_peering_connections,
                Filters=create_filters({'status-code': ['active'],
                                        'accepter-vpc-info.vpc-id': [vpc_ids[1]],
                                        'requester-vpc-info.vpc-id': [vpc_ids[0]]})
            )['VpcPeeringConnections']

            # create peering when peering doesn't exist
            if not existing_peerings:
                peering_conn = throttled_call(
                    client.create_vpc_peering_connection,
                    VpcId=vpc_ids[0], PeerVpcId=vpc_ids[1]
                )['VpcPeeringConnection']

                throttled_call(
                    client.accept_vpc_peering_connection,
                    VpcPeeringConnectionId=peering_conn['VpcPeeringConnectionId']
                )
                logger.info("Created new peering connection %s for %s",
                            peering_conn['VpcPeeringConnectionId'], peering)
            else:
                peering_conn = existing_peerings[0]
                logger.info("Peering connection %s exists for %s",
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

        # get all VPCs created through Asiaq. Ones that have type and Name tags
        existing_vpcs = throttled_call(
            client.describe_vpcs,
            Filters=[{
                'Name': 'tag-key',
                'Values': ['type', 'Name']
            }]
        ).get('Vpcs', [])

        for peering in peerings:
            peering_info = DiscoVPCPeerings.parse_peering_connection_line(peering, existing_vpcs)
            for peering_id, peering_config in peering_info.iteritems():
                vpc_ids_in_peering = [vpc.vpc['VpcId'] for vpc in peering_config.get("vpc_map", {}).values()]

                if len(vpc_ids_in_peering) < 2:
                    pass  # not all vpcs were up, nothing to do
                elif vpc_id and vpc_id not in vpc_ids_in_peering:
                    logger.debug("Skipping peering %s because it doesn't include %s", peering, vpc_id)
                else:
                    peering_configs[peering_id] = peering_config

        return peering_configs

    @staticmethod
    def _get_peering_lines():
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
    # Pylint thinks this function has too many local variables
    # pylint: disable=R0914
    def parse_peering_connection_line(line, existing_vpcs):
        """
        Parse vpc peering config lines and return data for creating the vpc peering connections

        Args:
            line (str): A vpc peering config line of the form
                            `vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork`

                        `vpc_name` may be the name of a VPC or a `*` wildcard to peer with any VPC of vpc_type
            existing_vpcs (List[VPC]): A list of Boto VPC dicts for all the existing AWS VPCs

        Returns:
            data necessary to create peering connections for the VPCs as a dictionary record for each
            peering connection containing two other dictionaries: vpc_name -> DiscoVPC instance
            and vpc_name -> metanetwork name
        """
        logger.debug('checking existence for peering %s', line)
        endpoints = line.split(' ')
        if not len(endpoints) == 2:
            raise VPCConfigError('Invalid peering config "%s". Peering config must be of the format '
                                 'vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork' % line)

        def get_endpoint_info(endpoint):
            """ Get a dict of peering info from a peering config section """
            return {
                # get name from `name[:type]/metanetwork
                'vpc_name': endpoint.split('/')[0].split(':')[0].strip(),
                # get type from `name[:type]/metanetwork`, defaulting to name if type is omitted
                'vpc_type': endpoint.split('/')[0].split(':')[-1].strip(),
                # get metanetwork from `name[:type]/metanetwork`
                'metanetwork': endpoint.split('/')[1].strip()
            }

        def get_matching_vpcs(vpc_name, vpc_type, existing_vpcs):
            """return a list of VPC names for VPCs that match the given vpc_name and vpc_type"""
            vpc_names = []
            for vpc in existing_vpcs:
                tags = tag2dict(vpc['Tags'])
                if vpc_name in ('*', tags['Name']) and vpc_type == tags['type']:
                    vpc_names.append(tags['Name'])
            return vpc_names

        source_info = get_endpoint_info(endpoints[0])
        target_info = get_endpoint_info(endpoints[1])

        if source_info['vpc_type'] == '*' or target_info['vpc_type'] == '*':
            raise VPCConfigError('Wildcards are not allowed for VPC type in "%s". '
                                 'Please specify a VPC type when using a wild card for the VPC name' % line)

        disco_vpc_objects = {}
        for vpc in existing_vpcs:
            tags = tag2dict(vpc['Tags'])
            disco_vpc_objects[tags['Name']] = disco_vpc.DiscoVPC(tags['Name'], tags['type'], vpc)

        # find the VPCs that match the peering config. Replace wildcards with real VPC names
        source_vpc_names = get_matching_vpcs(source_info['vpc_name'],
                                             source_info['vpc_type'],
                                             existing_vpcs)
        target_vpc_names = get_matching_vpcs(target_info['vpc_name'],
                                             target_info['vpc_type'],
                                             existing_vpcs)

        # the source or target side might not have matched any VPCs
        missing_vpcs = []
        if not source_vpc_names:
            missing_vpcs.append('%s:%s' % (source_info['vpc_name'], source_info['vpc_type']))
        if not target_vpc_names:
            missing_vpcs.append('%s:%s' % (target_info['vpc_name'], target_info['vpc_type']))

        if missing_vpcs:
            logger.debug(
                "Skipping peering %s because the following VPC(s) are not up: %s",
                line, ", ".join(missing_vpcs))
            return {}

        # peer every combination of source and target VPCs
        # don't peer a VPC with itself
        peerings = [peering for peering in product(source_vpc_names, target_vpc_names)
                    if peering[0] != peering[1]]

        peering_configs = {}
        for peering in peerings:
            source_vpc_name = peering[0]
            target_vpc_name = peering[1]
            # create a new peering connection line with all wildcards replaced
            peering_id = '%s:%s/%s %s:%s/%s' % (
                source_vpc_name,
                disco_vpc_objects[source_vpc_name].environment_type,
                source_info['metanetwork'],
                target_vpc_name,
                disco_vpc_objects[target_vpc_name].environment_type,
                target_info['metanetwork'],
            )

            vpc_map = {
                source_vpc_name: disco_vpc_objects[source_vpc_name],
                target_vpc_name: disco_vpc_objects[target_vpc_name]
            }

            vpc_metanetwork_map = {
                source_vpc_name: source_info['metanetwork'],
                target_vpc_name: target_info['metanetwork']
            }

            for vpc in vpc_map.values():
                if not vpc.networks:
                    raise RuntimeError("No metanetworks found for vpc %s. Are you sure it's of type %s?"
                                       % (vpc.environment_name, vpc.environment_type))

            peering_configs[peering_id] = {
                'vpc_map': vpc_map,
                'vpc_metanetwork_map': vpc_metanetwork_map
            }

        return peering_configs

    @staticmethod
    def delete_peerings(vpc_id=None):
        """Delete peerings. If vpc_id is specified, delete all peerings of the VPCs only"""
        client = boto3.client('ec2')
        for peering in DiscoVPCPeerings.list_peerings(vpc_id):
            try:
                logger.info('deleting peering connection %s', peering['VpcPeeringConnectionId'])
                throttled_call(
                    client.delete_vpc_peering_connection,
                    VpcPeeringConnectionId=peering['VpcPeeringConnectionId']
                )
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
            peerings = throttled_call(
                client.describe_vpc_peering_connections,
                Filters=create_filters({'requester-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections']

            peerings += throttled_call(
                client.describe_vpc_peering_connections,
                Filters=create_filters({'accepter-vpc-info.vpc-id': [vpc_id]})
            )['VpcPeeringConnections']
        else:
            peerings = throttled_call(client.describe_vpc_peering_connections)['VpcPeeringConnections']

        peering_states = LIVE_PEERING_STATES + (["failed"] if include_failed else [])
        return [
            peering
            for peering in peerings
            if peering['Status']['Code'] in peering_states
        ]
