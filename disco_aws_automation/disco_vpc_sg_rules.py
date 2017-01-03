"""
This module contains logic that processes security group rules for a VPC
"""

import logging

from boto.exception import EC2ResponseError

from .exceptions import VPCEnvironmentError
from .resource_helper import keep_trying, throttled_call

logger = logging.getLogger(__name__)


class DiscoVPCSecurityGroupRules(object):
    """
    This class takes care of processing of security group rules for a VPC
    """
    def __init__(self, vpc, boto3_ec2):
        self.disco_vpc = vpc
        self.boto3_ec2 = boto3_ec2

    def update_meta_network_sg_rules(self, dry_run=False):
        """
        Update the security group rules in each meta network based on what is defined
        the config file
        """
        for network in self.disco_vpc.networks.values():
            network.update_sg_rules(self._get_sg_rule_tuples(network), dry_run)

    def destroy(self):
        """ Deletes all the security group rules in a VPC """
        self._delete_security_group_rules()
        keep_trying(60, self._destroy_security_groups)

    def _get_sg_rule_tuples(self, network):
        rules = self.disco_vpc.get_config("{0}_sg_rules".format(network.name))
        if not rules:
            # No config, nothing to do
            return

        rules = rules.split(",")
        sg_rule_tuples = []
        for rule in rules:
            rule = rule.strip().split()
            if len(rule) < 3 or not all(rule):
                raise VPCEnvironmentError(
                    "Cannot make heads or tails of rule {0} for metanetwork {1}."
                    .format(" ".join(rule), network.name)
                )

            protocol = rule[0]
            source = rule[1]
            ports = rule[2:]

            for port_def in ports:
                port_def = DiscoVPCSecurityGroupRules._extract_port_range(port_def)
                if source.lower() == "all":
                    # Handle rule where source is all other networks
                    for source_network in self.disco_vpc.networks.values():
                        sg_rule_tuples.append(network.create_sg_rule_tuple(
                            protocol, port_def,
                            sg_source_id=source_network.security_group.id
                        ))
                elif "/" in source:
                    # Handle CIDR based sources
                    sg_rule_tuples.append(network.create_sg_rule_tuple(
                        protocol, port_def, cidr_source=source))
                else:
                    # Single network wide source
                    sg_rule_tuples.append(network.create_sg_rule_tuple(
                        protocol, port_def,
                        sg_source_id=self.disco_vpc.networks[source].security_group.id
                    ))

        # Add security rules for customer ports
        sg_rule_tuples += self._get_dmz_customer_ports_sg_rules(network) +\
            self._get_intranet_customer_ports_sg_rules(network)

        # Add security rules to allow ICMP (ping, traceroute & etc) and DNS
        # traffic for all subnets
        sg_rule_tuples += self._get_icmp_sg_rules(network)

        return sg_rule_tuples

    def _get_dmz_customer_ports_sg_rules(self, network):
        sg_rule_tuples = []
        if network.name == "dmz":
            customer_ports = self.disco_vpc.get_config("customer_ports", "").split()
            customer_cidrs = self.disco_vpc.get_config("customer_cidr", "").split()

            for port_def in customer_ports:
                port_range = DiscoVPCSecurityGroupRules._extract_port_range(port_def)
                for customer_cidr in customer_cidrs:
                    # Allow traffic from customer to dmz
                    sg_rule_tuples.append(network.create_sg_rule_tuple(
                        "tcp", port_range, cidr_source=customer_cidr))

                # Allow within DMZ so that vpn host can talk to lbexternal
                sg_rule_tuples.append(network.create_sg_rule_tuple(
                    "tcp", port_range,
                    sg_source_id=network.security_group.id
                ))

        return sg_rule_tuples

    def _get_intranet_customer_ports_sg_rules(self, network):
        sg_rule_tuples = []
        if network.name == "intranet":
            customer_ports = self.disco_vpc.get_config("customer_ports", "").split()
            for port_def in customer_ports:
                port_range = DiscoVPCSecurityGroupRules._extract_port_range(port_def)
                # Allow traffic from dmz to intranet (for lbexternal)
                sg_rule_tuples.append(network.create_sg_rule_tuple(
                    "tcp", port_range,
                    sg_source_id=self.disco_vpc.networks["dmz"].security_group.id
                ))

        return sg_rule_tuples

    def _get_icmp_sg_rules(self, network):
        return [network.create_sg_rule_tuple("icmp", [-1, -1],
                                             cidr_source=self.disco_vpc.vpc['CidrBlock']),
                network.create_sg_rule_tuple("udp", [53, 53],
                                             cidr_source=self.disco_vpc.vpc['CidrBlock'])]

    @staticmethod
    def _extract_port_range(port_def):
        ports = port_def.split(":")
        return [int(ports[0]), int(ports[1] if len(ports) > 1 else ports[0])]

    @staticmethod
    def _find_sg_by_id(groups, group_id):
        """
        Given a list of security groups, returns one with the matching ID

        raises KeyError if it is not found.
        """
        for group in groups:
            if group['GroupId'] == group_id:
                print "group:"
                print group
                return group
        raise KeyError("Security Group not found {0}".format(group_id))

    def _delete_security_group_rules(self):
        """ Delete all security group rules."""
        security_groups = self.get_all_security_groups_for_vpc()
        for security_group in security_groups:
            for permission in security_group['IpPermissions']:
                try:
                    logger.debug(
                        "revoking %s %s %s %s", security_group, permission.get('IpProtocol'),
                        permission.get('FromPort', '-'), permission.get('ToPort', '-'))
                    throttled_call(self.boto3_ec2.revoke_security_group_ingress,
                                   GroupId=security_group['GroupId'],
                                   IpPermissions=[permission])
                except EC2ResponseError:
                    logger.exception("Skipping error deleting sg rule.")

    def _destroy_security_groups(self):
        """ Find all security groups belonging to vpc and destroy them."""
        for security_group in self.get_all_security_groups_for_vpc():
            if security_group['GroupName'] != u'default':
                logger.debug("deleting sg: %s", security_group)
                throttled_call(self.boto3_ec2.delete_security_group, GroupId=security_group['GroupId'])

    def get_all_security_groups_for_vpc(self):
        """ Find all security groups belonging to vpc and return them """
        return throttled_call(self.boto3_ec2.describe_security_groups,
                              Filters=self.disco_vpc.vpc_filters())['SecurityGroups']
