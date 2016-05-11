"""
Network abstraction
"""

import logging
from math import log, ceil
from random import choice

from netaddr import IPNetwork, IPAddress
from boto.ec2.networkinterface import (
    NetworkInterfaceSpecification,
    NetworkInterfaceCollection
)
from boto.exception import EC2ResponseError
from boto.vpc import VPCConnection

from .resource_helper import keep_trying
from .disco_constants import NETWORKS
from .exceptions import IPRangeError


def find_or_create(find, create):
    """Given a find and a create function, create a resource iff it doesn't exist"""
    result = find()
    if result:
        return result
    else:
        return create()


class DiscoMetaNetwork(object):
    """
    Representation of a disco meta-network. Contains a subnet for each availability zone,
    along with a route table which is applied all the subnets.
    """
    def __init__(self, name, vpc):
        self.vpc = vpc
        self.name = name
        self._route_table = None  # lazily initialized
        self._security_group = None  # lazily initialized
        self._subnets = None  # lazily initialized
        self._connection = VPCConnection()

    def _resource_name(self, suffix=None):
        suffix = "_{0}".format(suffix) if suffix else ""
        return "{0}_{1}{2}".format(self.vpc.environment_name, self.name, suffix)

    def create(self):
        """
        Metanetwork is initialized lazily. This forces creation of all
        components.
        """
        self._route_table = self.route_table
        self._security_group = self.security_group
        self._subnets = self.subnets

    def vpc_filter(self):
        vf = self.vpc.vpc_filter()
        return {vf.get('Name'): vf.get('Values')[0]}

    @property
    def _resource_filter(self):
        resource_filter = self.vpc_filter()
        resource_filter["tag:meta_network"] = self.name
        return resource_filter

    def _tag_resource(self, resource, suffix=None):
        keep_trying(300, resource.add_tag, "Name", self._resource_name(suffix))
        keep_trying(300, resource.add_tag, "meta_network", self.name)

    @property
    def route_table(self):
        '''Finds or creates the route table for our metanetwork'''
        if not self._route_table:
            self._route_table = find_or_create(
                self._find_route_table, self._create_route_table
            )
        return self._route_table

    def _find_route_table(self):
        try:
            return self._connection.get_all_route_tables(
                filters=self._resource_filter
            )[0]
        except IndexError:
            return None

    def _create_route_table(self):
        route_table = self._connection.create_route_table(self.vpc.get_vpc_id())
        self._tag_resource(route_table)
        logging.debug("%s route table: %s", self.name, self._route_table)
        return route_table

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
            return self._connection.get_all_security_groups(
                filters=self._resource_filter
            )[0]
        except IndexError:
            return None

    @property
    def sg_description(self):
        """Returns a description of the metanetwork's purpose"""
        return NETWORKS[self.name]

    def _create_security_group(self):
        security_group = self._connection.create_security_group(
            self._resource_name(),
            self.sg_description,
            self.vpc.get_vpc_id()
        )
        self._tag_resource(security_group)
        logging.debug("%s security_group: %s", self.name, security_group)
        return security_group

    @property
    def subnets(self):
        '''Finds or creates the subnets for our metanetwork'''
        if not self._subnets:
            self._subnets = find_or_create(
                self._find_subnets, self._create_subnets
            )
        return self._subnets

    def _find_subnets(self):
        return self._connection.get_all_subnets(
            filters=self._resource_filter
        )

    def _create_subnets(self):
        logging.debug("creating subnets")
        zones = self._connection.get_all_zones()
        logging.debug("zones: %s", zones)
        # We'll need to split each subnet into smaller ones, one per zone
        # offset is how much we need to add to cidr divisor to create at least
        # that len(zone) subnets
        zone_cidr_offset = ceil(log(len(zones), 2))
        logging.debug("zone_offset: %s", zone_cidr_offset)

        network_cidr = IPNetwork(self.vpc.get_config("{0}_cidr".format(self.name)))
        zone_cidrs = network_cidr.subnet(
            int(network_cidr.prefixlen + zone_cidr_offset)
        )

        subnets = []
        for zone, cidr in zip(zones, zone_cidrs):
            logging.debug("%s %s", zone, cidr)
            subnet = self._connection.create_subnet(self.vpc.get_vpc_id(), cidr, zone.name)
            self._connection.associate_route_table(self.route_table.id, subnet.id)
            self._tag_resource(subnet, zone.name)
            subnets.append(subnet)
            logging.debug("%s subnet: %s", self.name, subnet)

        return subnets

    def subnet_by_ip(self, ip_address):
        """ Return the subnet to which the ip address belongs to """
        ip_address = IPAddress(ip_address)
        for subnet in self.subnets:
            cidr = IPNetwork(subnet.cidr_block)
            if ip_address >= cidr[0] and ip_address <= cidr[-1]:
                return subnet
        raise IPRangeError("IP {0} is not in Metanetwork ({1}) range.".format(ip_address, self.name))

    def create_interfaces_specification(self, subnets=None, public_ip=False):
        """
        Create a network interface specification for an instance -- to be used
        with run_instance()
        """
        random_subnet = choice(subnets if subnets else self.subnets)
        interface = NetworkInterfaceSpecification(
            subnet_id=random_subnet.id,
            groups=[self.security_group.id],
            associate_public_ip_address=public_ip)
        interfaces = NetworkInterfaceCollection(interface)
        return interfaces

    def get_interface(self, private_ip):
        """
        Allocate a 'floating' network inteface with static ip --
        if it does not already exist.
        """
        instance_filter = self.vpc_filter()
        instance_filter["private-ip-address"] = private_ip
        interfaces = self._connection.get_all_network_interfaces(
            filters=instance_filter
        )
        if interfaces:
            return interfaces[0]

        subnet = self.subnet_by_ip(private_ip)
        return self._connection.create_network_interface(
            subnet_id=subnet.id,
            private_ip_address=private_ip,
            description="floating interface",
            groups=[self.security_group.id],
        )

    def add_route(self, destination_cidr_block, *args, **kwargs):
        """ Try adding a route, if fails delete matching CIDR route and try again """
        try:
            return self._connection.create_route(
                self.route_table.id,
                destination_cidr_block,
                *args,
                **kwargs
            )
        except EC2ResponseError:
            logging.exception("Failed to create route due to conflict. Deleting old route and re-trying.")
            self._connection.delete_route(self.route_table.id, destination_cidr_block)
            new_route = self._connection.create_route(
                self.route_table_id,
                destination_cidr_block,
                *args,
                **kwargs
            )
            logging.error("Route re-created")
            return new_route

    def add_sg_rule(self, protocol, ports, sg_source=None, cidr_source=None):
        """ Add a security rule to the network """
        sg_args = {
            "group_id": self.security_group.id,
            "ip_protocol": protocol
        }
        if sg_source:
            sg_args["src_security_group_group_id"] = sg_source
        if cidr_source:
            sg_args["cidr_ip"] = cidr_source

        sg_args["from_port"] = ports[0]
        sg_args["to_port"] = ports[1]
        logging.debug("Adding sg_rule: %s", sg_args)
        self._connection.authorize_security_group(**sg_args)
