"""
Network abstraction
"""

import logging
from random import choice

from netaddr import IPNetwork, IPAddress
from boto.ec2.networkinterface import (
    NetworkInterfaceSpecification,
    NetworkInterfaceCollection
)
from boto.exception import EC2ResponseError
from boto.vpc import VPCConnection
import boto3

from disco_aws_automation.network_helper import calc_subnet_offset
from .disco_subnet import DiscoSubnet
from .resource_helper import (
    keep_trying,
    find_or_create,
    throttled_call
)
from .disco_constants import NETWORKS
from .exceptions import (
    IPRangeError,
    EIPConfigError,
    RouteCreationError
)

logger = logging.getLogger(__name__)


class DiscoMetaNetwork(object):
    """
    Representation of a disco meta-network. Contains a subnet for each availability zone,
    along with a route table which is applied all the subnets.
    """
    def __init__(self, name, vpc, network_cidr=None, boto3_connection=None):
        self.vpc = vpc
        self.name = name
        if network_cidr:
            self._network_cidr = IPNetwork(network_cidr)
        else:
            self._network_cidr = None
        self._centralized_route_table_loaded = False
        self._centralized_route_table = None  # lazily initialized
        self._security_group = None  # lazily initialized
        self._connection = VPCConnection()
        self._disco_subnets = None  # lazily initialized
        self._boto3_connection = boto3_connection  # Lazily initialized if parameter is None

    @property
    def network_cidr(self):
        """Get the network_cidr for the meta network"""
        if not self._network_cidr:
            # if we don't have a network_cidr yet (if it wasn't passed in the constructor)
            # then calculate it from the subnets
            subnets = self._instantiate_subnets(try_creating_aws_subnets=False)

            # calculate how big the meta network must have been if we divided it into the existing subnets
            subnet_cidr_offset = calc_subnet_offset(len(subnets.values()))

            # pick one of the subnets to do our math from
            subnet_network = IPNetwork(subnets.values()[0].subnet_dict['CidrBlock'])

            # the meta network cidr is the cidr of one of the subnets but with a smaller prefix
            subnet_network.prefixlen = subnet_network.prefixlen - subnet_cidr_offset
            self._network_cidr = subnet_network.cidr

        return self._network_cidr

    @property
    def boto3_ec2(self):
        """
        Lazily creates boto3 EC2 connection
        """
        if not self._boto3_connection:
            self._boto3_connection = boto3.client('ec2')
        return self._boto3_connection

    def _resource_name(self, suffix=None):
        suffix = "_{0}".format(suffix) if suffix else ""
        return "{0}_{1}{2}".format(self.vpc.environment_name, self.name, suffix)

    def create(self):
        """
        Metanetwork is initialized lazily. This forces creation of all
        components.
        """
        self._centralized_route_table = self.centralized_route_table
        self._security_group = self.security_group
        self._disco_subnets = self.disco_subnets

    def vpc_filter(self):
        """ Returns VPC filter """
        vpc_filter = self.vpc.vpc_filters()[0]
        return {vpc_filter.get('Name'): vpc_filter.get('Values')[0]}

    @property
    def _resource_filter(self):
        resource_filter = self.vpc_filter()
        resource_filter["tag:meta_network"] = self.name
        return resource_filter

    def _tag_resource(self, resource, suffix=None):
        keep_trying(300, resource.add_tag, "Name", self._resource_name(suffix))
        keep_trying(300, resource.add_tag, "meta_network", self.name)

    @property
    def centralized_route_table(self):
        '''Returns the centralized route table for our metanetwork,
        which could be None'''
        if not self._centralized_route_table_loaded:
            self._centralized_route_table = self._find_centralized_route_table()
            self._centralized_route_table_loaded = True
        return self._centralized_route_table

    def _find_centralized_route_table(self):
        route_tables = throttled_call(
            self._connection.get_all_route_tables,
            filters=self._resource_filter
        )
        if len(route_tables) != 1:
            # If the number of route tables is more than one, it means there is
            # one route table per disco_subnet, therefore don't return anything.
            return None

        return route_tables[0]

    @property
    def security_group(self):
        '''Finds or creates the security group for our metanetwork'''
        if not self._security_group:
            self._security_group = find_or_create(
                self._find_security_group, self._create_security_group
            )
        return self._security_group

    def _find_security_group(self):
        try:
            return throttled_call(
                self._connection.get_all_security_groups,
                filters=self._resource_filter
            )[0]
        except IndexError:
            return None

    @property
    def sg_description(self):
        """Returns a description of the metanetwork's purpose"""
        return NETWORKS[self.name]

    def _create_security_group(self):
        security_group = throttled_call(
            self._connection.create_security_group,
            self._resource_name(),
            self.sg_description,
            self.vpc.get_vpc_id()
        )
        self._tag_resource(security_group)
        logger.debug("%s security_group: %s", self.name, security_group)
        return security_group

    @property
    def disco_subnets(self):
        '''Creates the subnets for our metanetwork'''
        if not self._disco_subnets:
            self._disco_subnets = self._instantiate_subnets()
        return self._disco_subnets

    @property
    def subnet_ip_networks(self):
        """
        Return IPNetwork of all subnet CIDRs
        """
        return [
            IPNetwork(subnet.subnet_dict['CidrBlock'])
            for subnet in self.disco_subnets.values()
        ]

    @property
    def subnet_ids(self):
        """
        Return subnet ids
        """
        return [
            subnet.subnet_dict['SubnetId']
            for subnet in self.disco_subnets.values()
        ]

    def add_nat_gateways(self, allocation_ids=None):
        """
        Creates a NAT gateway in each of the metanetwork's subnet, either using the
        EIP allocation ids provided, or dynamic EIPs if EIP allocation_ids are not passed in.
        :param allocation_ids: Allocation ids of the Elastic IPs that will be
                               associated with the NAT gateways.
        """
        if allocation_ids:
            if len(self.disco_subnets.values()) != len(allocation_ids):
                raise EIPConfigError("The number of subnets does not match with the "
                                     "number of NAT gateway EIPs provided for {0}: "
                                     "{1} != {2}"
                                     .format(self._resource_name(),
                                             len(self.disco_subnets.values()),
                                             len(allocation_ids)))

            self._create_route_table_per_subnet()

            for disco_subnet, allocation_id in zip(self.disco_subnets.values(), allocation_ids):
                disco_subnet.create_nat_gateway(eip_allocation_id=allocation_id)

        else:
            self._create_route_table_per_subnet()

            for disco_subnet in self.disco_subnets.values():
                disco_subnet.create_nat_gateway()

    def _create_route_table_per_subnet(self):
        if self.centralized_route_table:
            for disco_subnet in self.disco_subnets.values():
                disco_subnet.recreate_route_table()

            throttled_call(self._connection.delete_route_table, self.centralized_route_table.id)
            self._centralized_route_table = None

    def delete_nat_gateways(self):
        """ Deletes all subnets' NAT gateways if any """
        for disco_subnet in self.disco_subnets.values():
            disco_subnet.delete_nat_gateway()

    def _instantiate_subnets(self, try_creating_aws_subnets=True):
        # FIXME needs to talk about and simplify this
        logger.debug("instantiating subnets")
        zones = throttled_call(self._connection.get_all_zones)
        logger.debug("zones: %s", zones)
        # We'll need to split each subnet into smaller ones, one per zone
        # offset is how much we need to add to cidr divisor to create at least
        # that len(zone) subnets
        zone_cidr_offset = calc_subnet_offset(len(zones))
        logger.debug("zone_offset: %s", zone_cidr_offset)

        if try_creating_aws_subnets:
            zone_cidrs = self.network_cidr.subnet(
                int(self.network_cidr.prefixlen + zone_cidr_offset)
            )
        else:
            zone_cidrs = ['' for _ in zones]

        subnets = {}
        for zone, cidr in zip(zones, zone_cidrs):
            logger.debug("%s %s", zone, cidr)
            disco_subnet = DiscoSubnet(str(zone.name), self, str(cidr),
                                       self.centralized_route_table.id
                                       if self.centralized_route_table else None)
            subnets[zone.name] = disco_subnet
            logger.debug("%s disco_subnet: %s", self.name, disco_subnet)

        return subnets

    def subnet_by_ip(self, ip_address):
        """ Return the subnet to which the ip address belongs to """
        ip_address = IPAddress(ip_address)
        for disco_subnet in self.disco_subnets.values():
            cidr = IPNetwork(disco_subnet.subnet_dict['CidrBlock'])
            if ip_address >= cidr[0] and ip_address <= cidr[-1]:
                return disco_subnet.subnet_dict
        raise IPRangeError("IP {0} is not in Metanetwork ({1}) range.".format(ip_address, self.name))

    def create_interfaces_specification(self, subnet_ids=None, public_ip=False):
        """
        Create a network interface specification for an instance -- to be used
        with run_instance()
        """
        random_subnet_id = choice(subnet_ids if subnet_ids else
                                  [disco_subnet.subnet_dict['SubnetId']
                                   for disco_subnet in self.disco_subnets.values()])
        interface = NetworkInterfaceSpecification(
            subnet_id=random_subnet_id,
            groups=[self.security_group.id],
            associate_public_ip_address=public_ip)
        interfaces = NetworkInterfaceCollection(interface)
        return interfaces

    def get_interface(self, private_ip):
        """
        Allocate a 'floating' network inteface with static ip --
        if it does not already exist.
        """
        interface_filter = self.vpc_filter()
        interface_filter["private-ip-address"] = private_ip
        interfaces = throttled_call(
            self._connection.get_all_network_interfaces,
            filters=interface_filter
        )
        if interfaces:
            return interfaces[0]

        logger.debug("Creating floating ENI %s", private_ip)
        aws_subnet = self.subnet_by_ip(private_ip)
        return throttled_call(
            self._connection.create_network_interface,
            subnet_id=aws_subnet['SubnetId'],
            private_ip_address=private_ip,
            description="floating interface",
            groups=[self.security_group.id]
        )

    @staticmethod
    def _convert_sg_rule_tuple_to_dict(sg_rule_tuple):
        sg_rule = {
            "group_id": sg_rule_tuple[0],
            "ip_protocol": sg_rule_tuple[1]
        }
        if sg_rule_tuple[4]:
            sg_rule["src_security_group_group_id"] = sg_rule_tuple[4]
        elif sg_rule_tuple[5]:
            sg_rule["cidr_ip"] = sg_rule_tuple[5]

        sg_rule["from_port"] = sg_rule_tuple[2]
        sg_rule["to_port"] = sg_rule_tuple[3]

        return sg_rule

    def create_sg_rule_tuple(self, protocol, ports, sg_source_id=None, cidr_source=None):
        """ Creates a tuple represeting a security group rule with the security groupd ID
        of the current meta network added """
        return self.security_group.id, protocol, ports[0], ports[1], sg_source_id, cidr_source

    def update_sg_rules(self, desired_sg_rules, dry_run=False):
        """
        Update the security rules of the meta network so that they conform to
        the new rules being passed in. Each rule is a tuple that contains 6 values:
        desire_sg_rules[0]: security groupd ID
        desire_sg_rules[1]: protocol, e.g. tcp, icmp
        desire_sg_rules[2]: from port
        desire_sg_rules[3]: end port
        desire_sg_rules[4]: source security group ID
        desire_sg_rules[5]: source CIDR
        """
        logger.info("Updating security rules for meta network %s", self.name)
        current_sg_rules = [
            self.create_sg_rule_tuple(
                rule.ip_protocol,
                [int(rule.from_port) if rule.from_port else 0,
                 int(rule.to_port) if rule.to_port else 65535],
                grant.group_id, grant.cidr_ip)
            for rule in self.security_group.rules
            for grant in rule.grants]

        current_sg_rules = set(current_sg_rules)
        desired_sg_rules = set(desired_sg_rules) if desired_sg_rules else set()

        sg_rules_to_add = list(desired_sg_rules - current_sg_rules)
        sg_rules_to_delete = list(current_sg_rules - desired_sg_rules)

        logger.info("Adding new security group rules %s", sg_rules_to_add)
        logger.info("Revoking security group rules %s", sg_rules_to_delete)

        if not dry_run:
            self._add_sg_rules(sg_rules_to_add)
            self._revoke_sg_rules(sg_rules_to_delete)

    def _revoke_sg_rules(self, rule_tuples):
        """ Revoke the list of security group rules from the current meta network """
        for rule in rule_tuples:
            rule = DiscoMetaNetwork._convert_sg_rule_tuple_to_dict(rule)
            if not throttled_call(self._connection.revoke_security_group, **rule):
                logger.warning("Failed to revoke security group %s", rule)

    def _add_sg_rules(self, rule_tuples):
        """ Add a list of security rules to the current meta network """
        for rule in rule_tuples:
            rule = DiscoMetaNetwork._convert_sg_rule_tuple_to_dict(rule)
            if not throttled_call(self._connection.authorize_security_group, **rule):
                logger.warning("Failed to authorize security group %s", rule)

    def ip_by_offset(self, offset):
        """
        Pass in +10 and get 10th ip of subnet range
        Pass in -2 and get 2nd to last ip of subnet

        Returns IpAddress object, usually you'll want
        to cast this to str.
        """

        try:
            offset = int(offset)
        except ValueError:
            raise IPRangeError(
                "Cannot find IP in metanetwork {0} by offset {1}."
                .format(self.name, offset))

        subnets = sorted(self.subnet_ip_networks)
        base_address = subnets[0].first if offset >= 0 else subnets[-1].last
        desired_address = IPAddress(base_address + offset)
        # Lazy check to ensure IP address is in metanetwork range
        self.subnet_by_ip(desired_address)

        return desired_address

    def add_gateway_routes(self, route_tuples):
        """"
        Add a list of gateway routes to all the subnets' route tables. Each route
        is a tuple that contains 2 values:
        new_route_tuples[0]: destination CIDR block
        new_route_tuples[1]: gateway ID
        """
        for route_tuple in route_tuples:
            self._add_gateway_route(route_tuple[0], route_tuple[1])

    def _delete_gateway_routes(self, dest_cidr_blocks):
        """"
        Delete the routes to destination CIDR blocks from all the subnets' route tables.
        """
        if self.centralized_route_table:
            for dest_cidr_block in dest_cidr_blocks:
                throttled_call(
                    self._connection.delete_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=dest_cidr_block
                )
        else:
            for dest_cidr_block in dest_cidr_blocks:
                for disco_subnet in self.disco_subnets.values():
                    disco_subnet.delete_route(dest_cidr_block)

    def update_gateways_and_routes(self, desired_route_tuples, dry_run=False):
        """
        Update gateways and routes to them in the meta network so that they conform to
        the new routes being passed in. Each new route is a tuple that contains 2 values:
        desired_route_tuples[0]: destination CIDR block
        desired_route_tuples[1]: gateway ID
        """
        desired_route_tuples = set(desired_route_tuples) if desired_route_tuples else set()

        # Getting the routes currently in the route table(s)
        current_route_tuples = set()
        if self.centralized_route_table:
            for route in self.centralized_route_table.routes:
                if route.destination_cidr_block and \
                        route.gateway_id and route.gateway_id != 'local':
                    current_route_tuples.add((route.destination_cidr_block, route.gateway_id))
        else:
            # Only need to get from one subnet since they are the same
            for route in self.disco_subnets.values()[0].route_table['Routes']:
                if route.get('DestinationCidrBlock') and \
                        route.get('GatewayId') and route.get('GatewayId') != 'local':
                    current_route_tuples.add(
                        (route['DestinationCidrBlock'], route['GatewayId']))

        current_cidrs = set([route_tuple[0] for route_tuple in current_route_tuples])
        desired_cidrs = set([route_tuple[0] for route_tuple in desired_route_tuples])
        common_cidrs = current_cidrs & desired_cidrs

        routes_to_replace = set([(common_cidr, route_tuple[1])
                                 for common_cidr in common_cidrs
                                 for route_tuple in desired_route_tuples
                                 if common_cidr == route_tuple[0]])
        # Remove the ones that are the same as in the current routes
        routes_to_replace -= current_route_tuples

        routes_to_be_replaced = set([(common_cidr, route_tuple[1])
                                     for common_cidr in common_cidrs
                                     for route_tuple in current_route_tuples
                                     if common_cidr == route_tuple[0]])
        # Remove the ones that are the same as in the desired routes
        routes_to_be_replaced -= desired_route_tuples

        routes_to_delete = current_route_tuples - desired_route_tuples - routes_to_be_replaced
        routes_to_add = desired_route_tuples - current_route_tuples - routes_to_replace

        logger.info("Routes to delete: %s", routes_to_delete)
        logger.info("Routes to replace existing ones: %s", routes_to_replace)
        logger.info("Existing routes to be replaced: %s", routes_to_be_replaced)
        logger.info("Routes to add: %s", routes_to_add)

        if not dry_run:
            self._delete_gateway_routes([route[0] for route in routes_to_delete])
            self._replace_gateway_routes(routes_to_replace)
            self.add_gateway_routes(routes_to_add)

    def _add_gateway_route(self, destination_cidr_block, gateway_id):
        """ Add a gateway route to the centralized route table or to all the
        subnets' route tables"""

        if self.centralized_route_table:
            try:
                return throttled_call(
                    self._connection.create_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=destination_cidr_block,
                    gateway_id=gateway_id
                )
            except EC2ResponseError:
                logger.exception("Failed to create route due to conflict. Deleting old route and re-trying.")
                throttled_call(
                    self._connection.delete_route,
                    self.centralized_route_table.id,
                    destination_cidr_block
                )
                new_route = throttled_call(
                    self._connection.create_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=destination_cidr_block,
                    gateway_id=gateway_id
                )
                logger.error("Route re-created")
                return new_route
        else:
            # No centralized route table here, so add a route to each disco_subnet
            for disco_subnet in self.disco_subnets.values():
                if not disco_subnet.add_route_to_gateway(destination_cidr_block, gateway_id):
                    raise RouteCreationError("Failed to create a route for metanetwork-subnet {0}-{1}:"
                                             "{2} -> {3}".format(self.name,
                                                                 disco_subnet.name,
                                                                 destination_cidr_block,
                                                                 gateway_id))

    def _replace_gateway_routes(self, route_tuples):
        for route_tuple in route_tuples:
            if self.centralized_route_table:
                throttled_call(
                    self._connection.replace_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=route_tuple[0],
                    gateway_id=route_tuple[1]
                )
            else:
                # No centralized route table here, so replace the route in each disco_subnet
                for disco_subnet in self.disco_subnets.values():
                    disco_subnet.replace_route_to_gateway(route_tuple[0], route_tuple[1])

    def upsert_nat_gateway_route(self, dest_metanetwork):
        """ Add a default route in each of the subnet's route table to the corresponding NAT gateway
        of the same AZ in the destination metanetwork if it's not already there. """
        self._create_route_table_per_subnet()

        for zone in self.disco_subnets.keys():
            self.disco_subnets[zone].upsert_route_to_nat_gateway(
                '0.0.0.0/0',
                dest_metanetwork.disco_subnets[zone].nat_gateway['NatGatewayId']
            )

    def delete_nat_gateway_route(self):
        """ Deletes the default route to NAT gateway """
        for disco_subnet in self.disco_subnets.values():
            disco_subnet.delete_route('0.0.0.0/0')

    def get_nat_gateway_metanetwork(self):
        """ If this meta network's default route is going to a NAT gateway, returns the name of
        the meta network in which the NAT resides. Otherwise, returns None. """
        for route in self.disco_subnets.values()[0].route_table['Routes']:
            if route.get('NatGatewayId') and route['DestinationCidrBlock'] == '0.0.0.0/0':
                try:
                    nat_gateway = throttled_call(self.boto3_ec2.describe_nat_gateways,
                                                 NatGatewayIds=[route['NatGatewayId']])['NatGateways'][0]
                except IndexError:
                    raise RuntimeError("Phantom NatGatewayId {0} found in meta network {1}."
                                       .format(route['NatGatewayId'], self.name))

                subnet = throttled_call(self.boto3_ec2.describe_subnets,
                                        SubnetIds=[nat_gateway['SubnetId']])['Subnets'][0]

                for tag in subnet['Tags']:
                    if tag['Key'] == 'meta_network':
                        return tag['Value']

                raise RuntimeError("The meta_network tag is missing in subnet {0}."
                                   .format(subnet['SubnetId']))
        return None

    def create_peering_route(self, peering_conn_id, cidr):
        """ create/update a route between the peering connection and all the subnets.
        If a centralized route table is used, add the route there. If not, add the route
        to all the subnets. """
        if self.centralized_route_table:
            peering_routes_for_cidr = [
                _ for _ in self.centralized_route_table.routes
                if _.destination_cidr_block == cidr
            ]
            if not peering_routes_for_cidr:
                logger.info(
                    'Create routes for (route_table: %s, dest_cidr: %s, connection: %s)',
                    self.centralized_route_table.id, cidr, peering_conn_id)
                throttled_call(
                    self._connection.create_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=cidr,
                    vpc_peering_connection_id=peering_conn_id
                )
            else:
                logger.info(
                    'Update routes for (route_table: %s, dest_cidr: %s, connection: %s)',
                    self.centralized_route_table.id, cidr, peering_conn_id)
                throttled_call(
                    self._connection.replace_route,
                    route_table_id=self.centralized_route_table.id,
                    destination_cidr_block=cidr,
                    vpc_peering_connection_id=peering_conn_id
                )
        else:
            # No centralized route table here, so add a route to each subnet
            for disco_subnet in self.disco_subnets.values():
                disco_subnet.create_peering_routes(peering_conn_id, cidr)
