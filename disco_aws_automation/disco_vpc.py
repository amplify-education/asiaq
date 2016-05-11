"""
Amazon VPC (Virtual Private Cloud) orchestration code.  We use VPC's to provide isolation between
environments, and between an environment and the internet.  In particular non-VPC instances
(EC2-Classic) have internet routable addresses which is not what we want.
"""

import logging
import time
from ConfigParser import ConfigParser

from boto.exception import EC2ResponseError
import boto3

from . import read_config
from .resource_helper import keep_trying
from .disco_log_metrics import DiscoLogMetrics
from .disco_alarm import DiscoAlarm
from .disco_alarm_config import DiscoAlarmsConfig
from .disco_autoscale import DiscoAutoscale
from .disco_constants import CREDENTIAL_BUCKET_TEMPLATE, NETWORKS
from .disco_metanetwork import DiscoMetaNetwork
from .disco_elasticache import DiscoElastiCache
from .disco_sns import DiscoSNS
from .disco_rds import DiscoRDS
from .disco_elb import DiscoELB
from .exceptions import (
    MultipleVPCsForVPCNameError, TimeoutError, VPCConfigError, VPCEnvironmentError, VPCPeeringSyntaxError,
    VPCNameNotFound)

CONFIG_FILE = "disco_vpc.ini"
VGW_STATE_POLL_INTERVAL = 2  # seconds
VGW_ATTACH_TIME = 600  # seconds. From observation, it takes about 300s to attach vgw
LIVE_PEERING_STATES = ["pending-acceptance", "provisioning", "active"]


def tag2dict(tags):
    ''' Converts a list of dict to dict '''
    return {tag.get('Key'): tag.get('Value') for tag in tags or {}}


class DiscoVPC(object):
    """
    This class contains all our VPC orchestration code
    """

    def __init__(self, environment_name, environment_type, vpc=None, config_file=None):
        if config_file:
            self.config = ConfigParser()
            self.config.read(config_file)
        else:
            self.config = read_config(CONFIG_FILE)
        self.environment_name = environment_name
        self.environment_type = environment_type
        self._peerings = None  # lazily initialized
        self._region = None  # lazily initialized
        self._networks = None  # lazily initialized
        self._alarms_config = None  # lazily initialized
        self.rds = DiscoRDS(vpc=self)
        self.elb = DiscoELB(vpc=self)
        self.elasticache = DiscoElastiCache(vpc=self)
        self.log_metrics = DiscoLogMetrics(environment=environment_name)

        if "_" in environment_name:  # Underscores break our alarm name parsing.
            raise VPCConfigError(
                "VPC name {0} must not contain an underscore".format(environment_name))

        self.client = boto3.client('ec2')
        if vpc:
            self.vpc = vpc
        else:
            self._configure_environment()

    def get_config(self, option, default=None):
        '''Returns appropriate configuration for the current environment'''
        env_section = "env:{0}".format(self.environment_name)
        envtype_section = "envtype:{0}".format(self.environment_type)
        peering_section = "peerings"
        if self.config.has_option(env_section, option):
            return self.config.get(env_section, option)
        elif self.config.has_option(envtype_section, option):
            return self.config.get(envtype_section, option)
        elif self.config.has_option(peering_section, option):
            return self.config.get(peering_section, option)
        else:
            return default

    def get_vpc_id(self):
        ''' Returns the vpc id '''
        return self.vpc['Vpc']['VpcId'] if self.vpc['Vpc'] else None

    def ami_stage(self):
        '''Returns default AMI stage to deploy in a development environment'''
        return self.get_config("ami_stage")

    @staticmethod
    def get_credential_buckets_from_env_name(aws_config, environment_name):
        """Return the credentials S3 bucket names for this environment"""

        env_name = environment_name or aws_config.get("disco_aws", "default_environment")
        if not env_name:
            raise VPCEnvironmentError(
                "Can not determine credentials bucket name, need to know environment name"
            )

        project_name = aws_config.get("disco_aws", "project_name")
        if not env_name:
            raise VPCEnvironmentError(
                "Can not determine credentials bucket name, need to know project name"
            )

        vpc = DiscoVPC.fetch_environment(environment_name=env_name)
        if not vpc:
            raise VPCEnvironmentError(
                "Can not determine credentials from environment name unless vpc exists"
            )

        return vpc.get_credential_buckets(project_name)

    @property
    def region(self):
        """Region we're operating in"""
        if not self._region:
            # region = self.vpc.region.name <-- This doesn't work, so we use the HACK below
            self._region = self.client.describe_availability_zones()['AvailabilityZones'][0]['RegionName']
        return self._region

    @property
    def alarms_config(self):
        """The configuration for metrics and alarms"""
        if not self._alarms_config:
            self._alarms_config = DiscoAlarmsConfig(self.environment_name)
        return self._alarms_config

    def get_credential_buckets(self, project_name):
        """Returns list of buckets to locate credentials in"""
        return [CREDENTIAL_BUCKET_TEMPLATE.format(region=self.region, project=project_name, postfix=postfix)
                for postfix in self.get_config("credential_buckets", "").split()]

    @classmethod
    def fetch_environment(cls, vpc_id=None, environment_name=None):
        """
        Returns an instance of this class for the specified VPC, or None if it does not exist
        """
        client = boto3.client('ec2')
        if vpc_id:
            vpc = client.describe_vpcs(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        elif environment_name:
            vpc = client.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [environment_name]}])
        else:
            raise VPCEnvironmentError("Expect vpc_id or environment_name")
        if vpc['Vpcs']:
            tags = tag2dict(vpc['Vpcs'][0]['Tags'] if 'Tags' in vpc else None)
            vpc['Vpc'] = vpc['Vpcs'][0]
            return cls(tags.get("Name", '-'), tags.get("type", '-'), vpc)
        else:
            return None

    @property
    def networks(self):
        """A dictionary containing each metanetwork name with its DiscoMetaNetwork class"""
        if self._networks:
            return self._networks
        self._networks = {
            network: DiscoMetaNetwork(network, self)
            for network in NETWORKS.keys()
            if self.get_config("{0}_cidr".format(network))  # don't create networks we haven't defined
        }
        return self._networks

    def find_instance_route_table(self, instance):
        """ Return route tables corresponding to instance """
        rt_filter = []
        rt_filter.append(self.vpc_filter())
        rt_filter.append({"Name": "route.instance-id", "Values": [instance.id]})
        return self.client.describe_route_tables(Filters=rt_filter)['RouteTables']

    def get_route_table(self, metanetwork):
        """ Returns the route table for a meta network """
        return self.networks[metanetwork].route_table

    def delete_instance_routes(self, instance):
        """ Delete all routes associated with instance """
        route_tables = self.find_instance_route_table(instance)
        for route_table in route_tables:
            for route in route_table.routes:
                if route.instance_id == instance.id:
                    self.vpc.connection.delete_route(
                        route_table.id, route.destination_cidr_block)

    def _configure_dhcp(self):
        internal_dns = self.get_config("internal_dns")
        external_dns = self.get_config("external_dns")
        domain_name = self.get_config("domain_name")
        ntp_server = self.get_config("ntp_server")

        # internal_dns server should be default, and for this reason it comes last.
        DhcpConfigurations = []
        DhcpConfigurations.append({"Key": "domain-name", "Values": [domain_name]})
        DhcpConfigurations.append({"Key": "domain-name-servers", "Values": [internal_dns, external_dns]})
        DhcpConfigurations.append({"Key": "ntp-servers", "Values": [ntp_server]})

        response = self.client.create_dhcp_options(DhcpConfigurations=DhcpConfigurations)
        ec2 = boto3.resource('ec2')
        dhcp_options = ec2.DhcpOptions(response['DhcpOptions']['DhcpOptionsId'])
        dhcp_options.create_tags(Tags=[{'Key': 'Name', 'Value': self.environment_name}])
        return self.client.describe_dhcp_options(
                            DhcpOptionsIds=[response['DhcpOptions']['DhcpOptionsId']]
                                                )['DhcpOptions']

    @staticmethod
    def _extract_port_range(port_def):
        ports = port_def.split(":")
        return [int(ports[0]), int(ports[1] if len(ports) > 1 else ports[0])]

    def _open_customer_ports(self):
        # TODO port to _add_sg_rules
        customer_ports = self.get_config("customer_ports", "").split()
        customer_cidrs = self.get_config("customer_cidr", "").split()
        for port_def in customer_ports:
            port_range = DiscoVPC._extract_port_range(port_def)
            for customer_cidr in customer_cidrs:
                # Allow traffic from customer to dmz
                self.vpc.connection.authorize_security_group(
                    group_id=self.networks["dmz"].security_group.id,
                    ip_protocol="tcp",
                    from_port=port_range[0],
                    to_port=port_range[1],
                    cidr_ip=customer_cidr
                )
            # Allow within DMZ so that vpn host can talk to lbexternal
            self.vpc.connection.authorize_security_group(
                group_id=self.networks["dmz"].security_group.id,
                ip_protocol="tcp",
                from_port=port_range[0],
                to_port=port_range[1],
                src_security_group_group_id=self.networks["dmz"].security_group.id
            )
            # Allow traffic from dmz to intranet (for lbexternal)
            self.vpc.connection.authorize_security_group(
                group_id=self.networks["intranet"].security_group.id,
                ip_protocol="tcp",
                from_port=port_range[0],
                to_port=port_range[1],
                src_security_group_group_id=self.networks["dmz"].security_group.id
            )

    def _add_sg_rules(self, network):
        rules = self.get_config("{0}_sg_rules".format(network.name))
        if not rules:
            # No config, nothing to do
            return

        rules = rules.split(",")
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
                port_def = self._extract_port_range(port_def)
                if source.lower() == "all":
                    # Handle rule where source is all other networks
                    for source_network in self.networks.values():
                        network.add_sg_rule(
                            protocol,
                            port_def,
                            sg_source=source_network.security_group.id
                        )
                elif "/" in source:
                    # Handle CIDR based sources
                    network.add_sg_rule(protocol, port_def, cidr_source=source)
                else:
                    # Single network wide source
                    network.add_sg_rule(
                        protocol,
                        port_def,
                        sg_source=self.networks[source].security_group.id
                    )

    def _add_igw_routes(self, internet_gateway):
        logging.debug("Adding IGW routes")
        for network_name, network in self.networks.iteritems():
            igw_routes = self.get_config("{0}_igw_routes".format(network_name))
            if not igw_routes:
                continue
            igw_routes = igw_routes.split(" ")

            for igw_route in igw_routes:
                logging.debug("adding IGW route %s to %s", igw_route, network_name)
                network.add_route(igw_route, internet_gateway['InternetGateway']['InternetGatewayId'])

    def _add_vgw_routes(self, virtual_private_gateway):
        logging.debug("Adding VGW routes")
        for network_name, network in self.networks.iteritems():
            vgw_routes = self.get_config("{0}_vgw_routes".format(network_name))
            if not vgw_routes:
                continue
            vgw_routes = vgw_routes.split(" ")

            for vgw_route in vgw_routes:
                logging.debug("adding VGW route %s to %s", vgw_route, network_name)
                network.add_route(vgw_route, virtual_private_gateway.id)

    def _find_vgw(self):
        """Locate VPN Gateway that corresponds to this VPN"""
        vgw_filter = [{"Name": "tag-value", "Values": [self.environment_name]}]
        vgws = self.client.describe_vpn_gateways(Filters=vgw_filter)
        if not len(vgws['VpnGateways']):
            logging.debug("Cannot find the required VPN Gateway named %s.", self.environment_name)
            return None
        return vgws['VpnGateways'][0]

    def _check_vgw_states(self, state):
        """Checks if all VPN Gateways are in the desired state"""
        filters = {"Name": "tag:Name", "Values": [self.environment_name]}
        states = []
        vgws = self.client.describe_vpn_gateways(Filters=[filters])
        for vgw in vgws['VpnGateways']:
            for attachment in vgw['VpcAttachments']:
                if state == u'detached':
                    states.append(attachment['State'] == state)
                elif attachment['VpcId'] == self.get_vpc_id():
                    states.append(attachment['State'] == state)
        logging.debug("%s of %s VGW attachments are now in state '%s'",
                      states.count(True), len(states), state)
        return states and all(states)

    def _wait_for_vgw_states(self, state, timeout=VGW_ATTACH_TIME):
        """Wait for all VPN Gateways to reach a specified state"""
        time_passed = 0
        while True:
            try:
                if self._check_vgw_states(state):
                    return True
            except EC2ResponseError:
                pass  # These are most likely transient, we will timeout if they are not

            if time_passed >= timeout:
                raise TimeoutError(
                    "Timed out waiting for VPN Gateways to change state to {0} after {1}s."
                    .format(state, time_passed))

            time.sleep(VGW_STATE_POLL_INTERVAL)
            time_passed += VGW_STATE_POLL_INTERVAL

    def _attach_vgw(self):
        """If configured, attach VPN Gateway and create corresponding routes"""
        vgw = self._find_vgw()
        if vgw:
            logging.debug("Attaching VGW: %s.", vgw)
            if vgw['VpcAttachments'] and vgw['VpcAttachments'][0]['State'] != 'detached':
                logging.info("VGW %s already attached to %s. Will detach and reattach to %s.",
                             vgw['VpnGatewayId'], vgw['VpcAttachments'][0]['VpcId'], self.get_vpc_id())
                self._detach_vgws()
                logging.debug("Waiting 30s to avoid VGW 'non-existance' conditon post detach.")
                time.sleep(30)
            self.client.attach_vpn_gateway(VpnGatewayId=vgw['VpnGatewayId'], VpcId=self.get_vpc_id())
            logging.debug("Waiting for VGW to become attached.")
            self._wait_for_vgw_states(u'attached')
            logging.debug("VGW have been attached.")

            self._add_vgw_routes(vgw)
        else:
            logging.info("No VGW to attach.")

    def _detach_vgws(self):
        """Detach VPN Gateways, but don't delete them so they can be re-used"""
        vgw_filter = [
                      {"Name": "attachment.state", "Values": ['attached']},
                      {"Name": "tag:Name", "Values": [self.environment_name]}
                    ]
        detached = False
        for vgw in self.client.describe_vpn_gateways(Filters=vgw_filter)['VpnGateways']:
            logging.debug("Detaching VGW: %s.", vgw)
            if not self.client.detach_vpn_gateway(VpnGatewayId=vgw['VpnGatewayId'],
                                                  VpcId=vgw['VpcAttachments'][0]['VpcId']):
                logging.error("Failed to detach %s from %s", vgw['VpnGatewayId'],
                              vgw['VpcAttachments'][0]['VpcId'])
            else:
                detached = True

        if not detached:
            return

        try:
            logging.debug("Waiting for VGWs to become detached.")
            self._wait_for_vgw_states(u'detached')
        except TimeoutError:
            logging.exception("Failed to detach VPN Gateways (Timeout).")

    def _update_environment(self):
        """Update the disco style environment VPC"""
        vpc_cidr = self.get_config("vpc_cidr")
        vpcs = self.client.describe_vpcs(Filters=[{'Name': 'tag-value', 'Values': [self.environment_name]}])

        if vpcs is None:
            logging.error("Failed to find vpc : %s", self.environment_name)
            raise Exception("Failed to find vpc : {}".format(self.environment_name))

        if len(vpcs['Vpcs']) > 1:
            vpc_names = [tag['Value'] for vpc in vpcs['Vpcs'] if 'Tags' in vpc
                         for tag in vpc['Tags'] if tag['Key'] == 'Name']
            logging.error("More than one vpc was found : %s", vpc_names)
            raise Exception("More than one vpc was found : %s" % vpc_names)

        vpc = vpcs['Vpcs'][0]

        if vpc_cidr != vpc['CidrBlock']:
            logging.error("VPC cannot be updated, Cidr values are different, %s instead of"
                          "%s", vpc_cidr, vpc['CidrBlock'])

        vpc_id = vpc['VpcId']

        sgs = self.client.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])

        print sgs
        networks = self.networks

        print networks

    def _configure_environment(self):
        """Create a new disco style environment VPC"""
        vpc_cidr = self.get_config("vpc_cidr")

        # Create VPC
        self.vpc = self.client.create_vpc(CidrBlock=self.get_config("vpc_cidr"))
        waiter = self.client.get_waiter('vpc_available')
        waiter.wait(VpcIds=[self.vpc['Vpc']['VpcId']])
        ec2 = boto3.resource('ec2')
        vpc = ec2.Vpc(self.vpc['Vpc']['VpcId'])
        tags = vpc.create_tags(Tags=[{'Key': 'Name', 'Value': self.environment_name},
                                     {'Key': 'type', 'Value': self.environment_type}])
        logging.debug("vpc: %s", self.vpc)
        logging.debug("vpc tags: %s", tags)

        dhcp_options = self._configure_dhcp()[0]
        self.client.associate_dhcp_options(DhcpOptionsId=dhcp_options['DhcpOptionsId'],
                                           VpcId=self.vpc['Vpc']['VpcId'])

        # Enable DNS
        self.client.modify_vpc_attribute(VpcId=self.vpc['Vpc']['VpcId'],
                                         EnableDnsSupport={'Value': True})
        self.client.modify_vpc_attribute(VpcId=self.vpc['Vpc']['VpcId'],
                                         EnableDnsHostnames={'Value': True})

        # Create metanetworks (subnets, route_tables and security groups)
        for network in self.networks.itervalues():
            network.create()

        # Configure security group rules
        for network in self.networks.values():
            self._add_sg_rules(network)

        # Set up security group rules
        self._open_customer_ports()

        # Allow ICMP (ping, traceroute & etc) and DNS traffic for all subnets
        for network in self.networks.itervalues():
            self.client.authorize_security_group_ingress(
                GroupId=network.security_group.id,
                IpProtocol="icmp",
                FromPort=-1,
                ToPort=-1,
                CidrIp=vpc_cidr
            )
            self.client.authorize_security_group_ingress(
                GroupId=network.security_group.id,
                IpProtocol="udp",
                FromPort=53,
                ToPort=53,
                CidrIp=vpc_cidr
            )

        # Setup internet gateway
        internet_gateway = self.client.create_internet_gateway()
        self.client.attach_internet_gateway(
                InternetGatewayId=internet_gateway['InternetGateway']['InternetGatewayId'],
                VpcId=self.get_vpc_id())
        logging.debug("internet_gateway: %s", internet_gateway)
        self._add_igw_routes(internet_gateway)

        self._attach_vgw()
        self.configure_notifications()
        DiscoVPC.create_peering_connections(DiscoVPC.parse_peerings_config(self.get_vpc_id()))
        self.rds.update_all_clusters_in_vpc()

    def configure_notifications(self):
        """
        Configure SNS topics for CloudWatch alarms.
        Note that topics are not deleted with the VPC, since that would require re-subscribing the members.
        """
        notifications = self.alarms_config.get_notifications()
        DiscoSNS().update_sns_with_notifications(notifications, self.environment_name)

    def assign_eip(self, instance, eip_address, allow_reassociation=False):
        """
        Assign EIP to an instance
        """
        eip = self.vpc.connection.get_all_addresses(addresses=[eip_address])[0]
        try:
            self.vpc.connection.associate_address(
                instance_id=instance.id,
                allocation_id=eip.allocation_id,
                allow_reassociation=allow_reassociation)
        except EC2ResponseError:
            logging.exception("Skipping failed EIP association. Perhaps reassociation of EIP is not allowed?")

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

    def vpc_filter(self):
        """Filter used to get only the current VPC when filtering an AWS reply by 'vpc-id'"""
        return {"Name": "vpc-id", "Values": [self.vpc['Vpc']['VpcId']]}

    def update(self):
        ''' Update an existing VPC '''
        self._update_environment()

    def destroy(self):
        """ Delete all VPC resources in the right order and then delete the vpc itself """
        DiscoAlarm().delete_environment_alarms(self.environment_name)
        self.log_metrics.delete_all_metrics()
        self.log_metrics.delete_all_log_groups()
        self._destroy_instances()
        self.elb.destroy_all_elbs()
        self._destroy_rds()
        self.elasticache.delete_all_cache_clusters(wait=True)
        self.elasticache.delete_all_subnet_groups()
        self._destroy_interfaces()
        self._destroy_subnets()
        self._delete_security_group_rules()
        keep_trying(60, self._destroy_security_groups)
        self._destroy_igws()
        self._destroy_routes()
        self._detach_vgws()
        DiscoVPC.delete_peerings(self.get_vpc_id())
        return self._destroy_vpc()

    def _destroy_instances(self):
        """ Find all instances in vpc and terminate them """
        autoscale = DiscoAutoscale(environment_name=self.environment_name)
        autoscale.clean_groups(force=True)
        instances = [i['InstanceId']
                     for r in self.client.describe_instances(Filters=[self.vpc_filter()])['Reservations']
                     for i in r['Instances']]

        if not instances:
            logging.debug("No running instances")
            return
        logging.debug("terminating %s instance(s) %s", len(instances), instances)

        # for instance in instances:
        #    instance.terminate()
        # for instance in instances:
        #    wait_for_state(instance, u'terminated')

        self.client.terminate_instances(InstanceIds=instances)
        waiter = self.client.get_waiter('instance_terminated')
        waiter.wait(InstanceIds=instances,
                    Filters=[{'Name': 'instance-state-name', 'Values': ['terminated']}]
                    )
        autoscale.clean_configs()

        logging.debug("waiting for instance shutdown scripts")

    def _destroy_rds(self, wait=True):
        """ Delete all RDS instances/clusters. Final snapshots are automatically taken. """
        self.rds.delete_all_db_instances(wait=wait)

    def _destroy_interfaces(self):
        """ Deleting interfaces explicitly lets go of subnets faster """
        for interface in self.client.describe_network_interfaces(Filters=[self.vpc_filter()])["NetworkInterfaces"]:
            try:
                self.client.delete_network_interface(NetworkInterfaceId=interface['NetworkInterfaceId'])
            except EC2ResponseError:
                # Occasionally we get InvalidNetworkInterfaceID.NotFound, not sure why.
                logging.exception("Skipping error deleting network.")

    def _destroy_subnets(self):
        """ Find all subnets belonging to a vpc and destroy them"""
        for subnet in self.client.describe_subnets(Filters=[self.vpc_filter()])['Subnets']:
            self.client.delete_subnet(SubnetId=subnet['SubnetId'])

    def _delete_security_group_rules(self):
        """ Delete all security group rules."""
        security_groups = self.get_all_security_groups_for_vpc()
        for security_group in security_groups:
            for permission in security_group['IpPermissions']:
                try:
                    logging.debug(
                        "revoking %s %s %s %s %s", security_group, permission.get('IpProtocol'),
                        permission.get('FromPort', '-'), permission.get('ToPort', '-'))
                    self.client.revoke_security_group_ingress(
                        GroupId=security_group['GroupId'],
                        IpPermissions=[permission]
                    )
                except EC2ResponseError:
                    logging.exception("Skipping error deleting sg rule.")

    def _destroy_security_groups(self):
        """ Find all security groups belonging to vpc and destroy them."""
        for security_group in self.get_all_security_groups_for_vpc():
            if security_group['GroupName'] != u'default':
                logging.debug("deleting sg: %s", security_group)
                self.client.delete_security_group(GroupId=security_group['GroupId'])

    def get_all_security_groups_for_vpc(self):
        """ Find all security groups belonging to vpc and return them """
        return self.client.describe_security_groups(Filters=[self.vpc_filter()])['SecurityGroups']

    def _destroy_igws(self):
        """ Find all gateways belonging to vpc and destroy them"""
        vpc_attachment_filter = {"Name": "attachment.vpc-id", "Values": [self.get_vpc_id()]}
        # delete gateways
        for igw in self.client.describe_internet_gateways(Filters=[vpc_attachment_filter])['InternetGateways']:
            self.client.detach_internet_gateway(
                InternetGatewayId=igw['InternetGatewayId'],
                VpcId=self.get_vpc_id())
            self.client.delete_internet_gateway(InternetGatewayId=igw['InternetGatewayId'])

    def _destroy_routes(self):
        """ Find all route_tables belonging to vpc and destroy them"""
        for route_table in self.client.describe_route_tables(Filters=[self.vpc_filter()])['RouteTables']:
            if len(route_table['Tags']) < 1:
                logging.info("Skipping untagged (default) route table %s", route_table['RouteTableId'])
                continue
            try:
                self.client.delete_route_table(RouteTableId=route_table['RouteTableId'])
            except EC2ResponseError:
                logging.error("Error deleting route_table %s:.", route_table['RouteTableId'])
                raise

    def _destroy_vpc(self):
        """Delete VPC and then delete the dhcp_options that were associated with it. """

        # save function and parameters so we can delete dhcp_options after vpc. We do this becase botos
        # get_all_dhcp_options does not support filter. Because we cannot easily find the default dhcp
        # options, re-assigning default dhcp option is not trivial.
        # delete_dhcp_options = self.vpc.connection.delete_dhcp_options
        dhcp_options_id = self.vpc['Vpc']['DhcpOptionsId']

        self.client.delete_vpc(VpcId=self.get_vpc_id())
        # delete_status = keep_trying(30, self.vpc.delete)
        self.vpc = None

        self.client.delete_dhcp_options(DhcpOptionsId=dhcp_options_id)
        # if not delete_dhcp_options(dhcp_options_id):
        #    logging.warning("failed to delete dhcp options (%s)", dhcp_options_id)

        # return delete_status

    @staticmethod
    def find_vpc_id_by_name(vpc_name):
        """Find VPC by name"""
        client = boto3.client('ec2')
        vpcs = client.describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [vpc_name]}])['Vpcs']
        if len(vpcs) == 1:
            return vpcs[0]['VpcId']
        elif len(vpcs) == 0:
            raise VPCNameNotFound("No VPC is named as {}".format(vpc_name))
        else:
            raise MultipleVPCsForVPCNameError("More than 1 VPC is named as {}".format(vpc_name))

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
                vpc_conn.describe_vpcs(Filters=[{'Name': 'tag-value', 'Values': [vpc_name]}])['Vpcs'], 0)
            for vpc_name in vpc_type_map.keys()
        }

        missing_vpcs = [vpc_name for vpc_name, vpc_object in vpc_objects.items() if not vpc_object]
        if missing_vpcs:
            logging.debug(
                "Skipping peering %s because the following VPC(s) are not up: %s",
                line, ", ".join(map(str, missing_vpcs)))
            return {}

        vpc_map = {
            k: DiscoVPC(k, v, vpc_objects[k])
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
    def parse_peerings_config(vpc_id=None):
        """
        Parses configuration from disco_vpc.ini's peerings sections.
        If vpc_id is specified, only configuration relevant to vpc_id is included.
        """
        logging.debug("Parsing peerings configuration specified in %s", CONFIG_FILE)
        config = read_config(CONFIG_FILE)

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

        # vpc_conn = VPCConnection()
        client = boto3.client('ec2')
        peering_configs = {}
        for peering in peerings:
            peering_config = DiscoVPC.parse_peering_connection_line(peering, client)
            vpc_ids_in_peering = [vpc.vpc['VpcId'] for vpc in peering_config.get("vpc_map", {}).values()]

            if len(vpc_ids_in_peering) < 2:
                pass  # not all vpcs were up, nothing to do
            elif vpc_id and vpc_id not in vpc_ids_in_peering:
                logging.debug("Skipping peering %s because it doesn't include %s", peering, vpc_id)
            else:
                peering_configs[peering] = peering_config

        return peering_configs

    @staticmethod
    def create_peering_connections(peering_configs):
        """ create vpc peering configuration from the peering config dictionary"""
        client = boto3.client('ec2')
        for peering in peering_configs.keys():
            vpc_map = peering_configs[peering]['vpc_map']
            vpc_metanetwork_map = peering_configs[peering]['vpc_metanetwork_map']
            vpc_ids = [vpc.vpc.id for vpc in vpc_map.values()]
            existing_peerings = client.describe_vpc_peering_connections(
                Filters=[
                    {'Name': 'status-code', 'Values': ['active']},
                    {'Name': 'accepter-vpc-info.vpc-id', 'Values': [vpc_ids[0]]},
                    {'Name': 'requester-vpc-info.vpc-id', 'Values': [vpc_ids[1]]}
                ]
            ) + client.describe_vpc_peering_connections(
                filters=[
                    {'Name': 'status-code', 'Values': ['active']},
                    {'Name': 'accepter-vpc-info.vpc-id', 'Values': [vpc_ids[1]]},
                    {'Name': 'requester-vpc-info.vpc-id', 'Values': [vpc_ids[0]]}
                ]
            )
            # create peering when peering doesn't exist
            if not existing_peerings:
                peering_conn = client.create_vpc_peering_connection(*vpc_ids)
                client.accept_vpc_peering_connection(peering_conn.id)
                logging.info("create new peering connection %s for %s", peering_conn.id, peering)
            else:
                peering_conn = existing_peerings[0]
                logging.info("peering connection %s exists for %s", existing_peerings[0].id, peering)
            DiscoVPC.create_peering_routes(client, vpc_map, vpc_metanetwork_map, peering_conn)

    @staticmethod
    def create_peering_routes(client, vpc_map, vpc_metanetwork_map, peering_conn):
        """ create/update routes via peering connections between VPCs """
        cidr_map = {
            _: vpc_map[_].get_config("{0}_cidr".format(vpc_metanetwork_map[_]))
            for _ in vpc_map.keys()
        }
        route_table_map = {
            _: vpc_map[_].networks[vpc_metanetwork_map[_]].route_table
            for _ in vpc_map.keys()
        }
        for vpc_name, route_table in route_table_map.iteritems():
            remote_vpc_names = vpc_map.keys()
            remote_vpc_names.remove(vpc_name)
            peering_routes_for_peering = [
                _ for _ in route_table.routes
                if _.vpc_peering_connection_id == peering_conn
            ]
            if not peering_routes_for_peering:
                peering_routes_for_cidr = [
                    _ for _ in route_table.routes
                    if _.destination_cidr_block == cidr_map[remote_vpc_names[0]]
                ]
                if not peering_routes_for_cidr:
                    logging.info(
                        'create routes for (route_table: %s, dest_cidr: %s, connection: %s)',
                        route_table.id, cidr_map[remote_vpc_names[0]], peering_conn.id)
                    client.create_route(RouteTableId=route_table.id,
                                        DestinationCidrBlock=cidr_map[remote_vpc_names[0]],
                                        VpcPeeringConnectionId=peering_conn.id)
                else:
                    logging.info(
                        'update routes for (route_table: %s, dest_cidr: %s, connection: %s)',
                        route_table.id, cidr_map[remote_vpc_names[0]], peering_conn.id)
                    client.replace_route(RouteTableId=route_table.id,
                                         DestinationCidrBlock=cidr_map[remote_vpc_names[0]],
                                         VpcPeeringConnectionId=peering_conn.id)

    @staticmethod
    def list_vpcs():
        """Returns list of boto.vpc.vpc.VPC classes, one for each existing VPC"""
        client = boto3.client('ec2')
        vpcs = client.describe_vpcs()
        return [{'id': vpc['VpcId'], 'tags': tag2dict(vpc['Tags'] if 'Tags' in vpc else None)}
                for vpc in vpcs['Vpcs']]

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
                Filters=[{'Name': 'requester-vpc-info.vpc-id', 'Values': [vpc_id]}]
            )['VpcPeeringConnections'] + client.describe_vpc_peering_connections(
                Filters=[{'Name': 'accepter-vpc-info.vpc-id', 'Values': [vpc_id]}]
            )['VpcPeeringConnections']
        else:
            peerings = client.describe_vpc_peering_connections()['VpcPeeringConnections']

        peering_states = LIVE_PEERING_STATES + (["failed"] if include_failed else [])
        return [
            peering
            for peering in peerings
            if peering['Status']['Code'] in peering_states
        ]

    @staticmethod
    def delete_peerings(vpc_id=None):
        """Delete peerings. If vpc_id is specified, delete all peerings of the VPCs only"""
        client = boto3.client('ec2')
        for peering in DiscoVPC.list_peerings(vpc_id):
            try:
                logging.info('deleting peering connection %s', peering['VpcPeeringConnectionId'])
                client.delete_vpc_peering_connection(VpcPeeringConnectionId=peering['VpcPeeringConnectionId'])
            except EC2ResponseError:
                raise RuntimeError('Failed to delete VPC Peering connection \
                                    {}'.format(peering['VpcPeeringConnectionId']))
