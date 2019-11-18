"""
Amazon VPC (Virtual Private Cloud) orchestration code.  We use VPC's to provide isolation between
environments, and between an environment and the internet.  In particular non-VPC instances
(EC2-Classic) have internet routable addresses which is not what we want.
"""

import logging

import socket
import time
from ConfigParser import ConfigParser

from datetime import datetime
from boto.exception import EC2ResponseError
import boto3
from botocore.exceptions import ClientError

from netaddr import IPNetwork, IPAddress
from netaddr.core import AddrFormatError

from disco_aws_automation.network_helper import calc_subnet_offset, get_random_free_subnet
from .disco_config import normalize_path

from .disco_alarm import DiscoAlarm
from .disco_alarm_config import DiscoAlarmsConfig
from .disco_group import DiscoGroup
from .disco_config import read_config
from .disco_constants import CREDENTIAL_BUCKET_TEMPLATE, CREDENTIAL_BUCKET_TEMPLATE_NEW, NETWORKS,\
    VPC_CONFIG_FILE
from .disco_elasticache import DiscoElastiCache
from .disco_elb import DiscoELB
from .disco_log_metrics import DiscoLogMetrics
from .disco_metanetwork import DiscoMetaNetwork
from .disco_rds import DiscoRDS
from .disco_sns import DiscoSNS
from .disco_vpc_endpoints import DiscoVPCEndpoints
from .disco_vpc_gateways import DiscoVPCGateways
from .disco_vpc_peerings import DiscoVPCPeerings
from .disco_vpc_sg_rules import DiscoVPCSecurityGroupRules
from .resource_helper import (tag2dict, create_filters, keep_trying, throttled_call, dict_to_boto3_tags)
from .exceptions import (IPRangeError, VPCConfigError, VPCEnvironmentError)

logger = logging.getLogger(__name__)


# FIXME: pylint thinks the file has too many instance arguments
# pylint: disable=R0902
class DiscoVPC(object):
    """
    This class contains all our VPC orchestration code
    """
    # Pylint complains this function has too many arguments
    # pylint: disable=R0913
    def __init__(self, environment_name, environment_type, vpc=None,
                 config_file=None, boto3_ec2=None, defer_creation=False,
                 aws_config=None, skip_enis_pre_allocate=False, vpc_tags=None,
                 environment_class=None):
        self.config_file = config_file or VPC_CONFIG_FILE

        self.environment_name = environment_name
        self.environment_type = environment_type
        self.environment_class = environment_class

        # Lazily initialized
        self._config = None
        self._region = None
        self._networks = None
        self._alarms_config = None
        self._disco_vpc_endpoints = None
        self._aws_config = aws_config
        self._skip_enis_pre_allocate = skip_enis_pre_allocate
        self._vpc_tags = vpc_tags

        if boto3_ec2:
            self.boto3_ec2 = boto3_ec2
        else:
            self.boto3_ec2 = boto3.client('ec2')

        self.rds = DiscoRDS(vpc=self)
        self.elb = DiscoELB(vpc=self)
        self.disco_vpc_sg_rules = DiscoVPCSecurityGroupRules(vpc=self, boto3_ec2=self.boto3_ec2)
        self.disco_vpc_gateways = DiscoVPCGateways(vpc=self, boto3_ec2=self.boto3_ec2)
        self.disco_vpc_peerings = DiscoVPCPeerings(boto3_ec2=self.boto3_ec2)
        self.elasticache = DiscoElastiCache(vpc=self)
        self.log_metrics = DiscoLogMetrics(environment=environment_name)

        if "_" in environment_name:  # Underscores break our alarm name parsing.
            raise VPCConfigError(
                "VPC name {0} must not contain an underscore".format(environment_name))

        if vpc:
            self.vpc = vpc
        elif not defer_creation:
            self.create()

    @property
    def aws_config(self):
        "Auto-populate and return an AsiaqConfig for the standard configuration file."
        if not self._aws_config:
            self._aws_config = read_config(environment=self.environment_name)
        return self._aws_config

    @property
    def config(self):
        """lazy load config"""
        if not self._config:
            try:
                config = ConfigParser()
                config_file = normalize_path(self.config_file)
                logger.info("Reading VPC config %s", config_file)
                config.read(config_file)
                self._config = config
            except Exception:
                return None
        return self._config

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

        return default

    def get_vpc_id(self):
        ''' Returns the vpc id '''
        return self.vpc['VpcId'] if self.vpc else None

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
            response = throttled_call(self.boto3_ec2.describe_availability_zones)
            self._region = response['AvailabilityZones'][0]['RegionName']
        return self._region

    @property
    def alarms_config(self):
        """The configuration for metrics and alarms"""
        if not self._alarms_config:
            self._alarms_config = DiscoAlarmsConfig(self.environment_name)
        return self._alarms_config

    def get_credential_buckets(self, project_name):
        """Returns list of buckets to locate credentials in"""
        if self.region == 'us-west-2':
            return [CREDENTIAL_BUCKET_TEMPLATE.format(region=self.region,
                                                      project=project_name,
                                                      postfix=postfix)
                    for postfix in self.get_config("credential_buckets", "").split()]
        return [CREDENTIAL_BUCKET_TEMPLATE_NEW.format(region=self.region,
                                                      project=project_name,
                                                      postfix=postfix)
                for postfix in self.get_config("credential_buckets", "").split()]

    @classmethod
    def fetch_environment(cls, vpc_id=None, environment_name=None):
        """
        Returns an instance of this class for the specified VPC, or None if it does not exist
        """
        client = boto3.client('ec2')
        if vpc_id:
            vpcs = throttled_call(
                client.describe_vpcs,
                Filters=create_filters({'vpc-id': [vpc_id]})
            )['Vpcs']
        elif environment_name:
            vpcs = throttled_call(
                client.describe_vpcs,
                Filters=create_filters({'tag:Name': [environment_name]})
            )['Vpcs']
        else:
            raise VPCEnvironmentError("Expect vpc_id or environment_name")

        if not vpcs:
            return None

        tags = tag2dict(vpcs[0]['Tags'] if 'Tags' in vpcs[0] else None)
        return cls(tags.get("Name", '-'), tags.get("type", '-'), vpcs[0],
                   environment_class=tags.get("environment_class", 'development'))

    @property
    def disco_vpc_endpoints(self):
        """
        Manage VPC endpoints
        """
        if not self._disco_vpc_endpoints:
            self._disco_vpc_endpoints = DiscoVPCEndpoints(
                vpc_id=self.get_vpc_id(),
                boto3_ec2_client=self.boto3_ec2,
            )
        return self._disco_vpc_endpoints

    @property
    def networks(self):
        """A dictionary containing each metanetwork name with its DiscoMetaNetwork class"""
        if self._networks:
            return self._networks
        self._networks = {
            network: DiscoMetaNetwork(network, self)
            for network in NETWORKS
            if self.get_config("{0}_cidr".format(network))  # don't create networks we haven't defined
        }
        return self._networks

    def _create_new_meta_networks(self):
        """Read the VPC config and create the DiscoMetaNetwork objects that should exist in a new VPC"""

        # don't create networks we haven't defined
        # a map of network names to the configured cidr value or "auto"
        networks = {network: self.get_config("{0}_cidr".format(network))
                    for network in NETWORKS
                    if self.get_config("{0}_cidr".format(network))}

        if len(networks) < 1:
            raise VPCConfigError('No Metanetworks configured for VPC %s' % self.environment_name)

        # calculate the extra cidr bits needed to represent the networks
        # for example breaking a /20 VPC into 4 meta networks will create /22 sized networks
        cidr_offset = calc_subnet_offset(len(networks))
        vpc_size = IPNetwork(self.vpc['CidrBlock']).prefixlen
        meta_network_size = vpc_size + cidr_offset

        # /32 is the smallest possible network
        if meta_network_size > 32:
            raise VPCConfigError('Unable to create %s metanetworks in /%s size VPC'
                                 % (len(networks), vpc_size))

        # keep a list of the cidrs used by the meta networks in case we need to pick a random one
        used_cidrs = [cidr for cidr in networks.values() if cidr != 'auto']

        metanetworks = {}
        for network_name, cidr in networks.iteritems():
            # pick a random ip range if there isn't one configured for the network in the config
            if cidr == 'auto':
                cidr = get_random_free_subnet(self.vpc['CidrBlock'], meta_network_size, used_cidrs)

                if not cidr:
                    raise VPCConfigError("Can't create metanetwork {}. No subnets available".
                                         format(network_name))

            metanetworks[network_name] = DiscoMetaNetwork(network_name, self, cidr)
            metanetworks[network_name].create()
            used_cidrs.append(cidr)

        return metanetworks

    def _reserve_hostclass_ip_addresses(self):
        """
        Reserves static ip addresses used by hostclasses by pre-creating the ENIs,
        so that these IPs won't be occupied by AWS services, such as RDS, ElastiCache, etc.
        """
        for hostclass in self.aws_config.get_hostclasses_from_section_names():
            ip_address = self.aws_config.get_asiaq_option(
                "ip_address", section=hostclass, required=False)
            if ip_address:
                meta_network = self.networks[
                    self.aws_config.get_asiaq_option("meta_network", section=hostclass)]

                if ip_address.startswith("-") or ip_address.startswith("+"):
                    try:
                        ip_address = str(meta_network.ip_by_offset(ip_address))
                    except IPRangeError as exc:
                        logger.warn("Failed to reserve IP address (%s) for hostclass (%s) due "
                                    "to IPRangeError: %s", ip_address, hostclass, exc.message)
                        continue

                meta_network.get_interface(ip_address)

    def find_instance_route_table(self, instance):
        """ Return route tables corresponding to instance """
        rt_filters = self.vpc_filters()
        rt_filters.extend(create_filters({'route.instance-id': [instance.id]}))
        return throttled_call(self.boto3_ec2.describe_route_tables, Filters=rt_filters)['RouteTables']

    def delete_instance_routes(self, instance):
        """ Delete all routes associated with instance """
        route_tables = self.find_instance_route_table(instance)
        for route_table in route_tables:
            for route in route_table.routes:
                if route.instance_id == instance.id:
                    throttled_call(self.boto3_ec2.delete_route,
                                   RouteTableId=route_table.id,
                                   DestinationCidrBlock=route.destination_cidr_block)

    def _get_ntp_server_config(self):
        ntp_servers = []
        ntp_server_config = self.get_config("ntp_server")
        if ntp_server_config:
            for ntp_server in ntp_server_config.split():
                if self._is_valid_ip(ntp_server):
                    ntp_servers.append(ntp_server)
                else:
                    ntp_server_ip = socket.gethostbyname(ntp_server)
                    ntp_servers.append(ntp_server_ip)
        else:
            ntp_server_metanetwork = self.get_config("ntp_server_metanetwork")
            ntp_server_offset = self.get_config("ntp_server_offset")
            if ntp_server_metanetwork and ntp_server_offset:
                ntp_servers.append(str(
                    self.networks[ntp_server_metanetwork].ip_by_offset(ntp_server_offset)))

        return ntp_servers if ntp_servers else None

    def _is_valid_ip(self, ip_str):
        try:
            IPAddress(ip_str)
            return True
        except AddrFormatError:
            return False

    def _update_dhcp_options(self, dry_run=False):
        desired_dhcp_options = self._get_dhcp_configs()
        logger.info("Desired DHCP options: %s", desired_dhcp_options)

        try:
            existing_dhcp_options = throttled_call(
                self.boto3_ec2.describe_dhcp_options,
                DhcpOptionsIds=[self.vpc['DhcpOptionsId']],
                Filters=[{'Name': 'tag:Name', 'Values': [self.environment_name]}]
            )['DhcpOptions'][0]['DhcpConfigurations']

        except (IndexError, ClientError):
            existing_dhcp_options = []

        for option in existing_dhcp_options:
            option['Values'] = [value['Value'] for value in option['Values']]

        logger.info("Existing DHCP options: %s", existing_dhcp_options)

        if not dry_run and not self._same_dhcp_options(desired_dhcp_options, existing_dhcp_options):
            created_dhcp_options = self._create_dhcp_options(desired_dhcp_options)

            if existing_dhcp_options:
                logger.info('Deleting existing DHCP options: %s', self.vpc['DhcpOptionsId'])
                throttled_call(self.boto3_ec2.delete_dhcp_options,
                               DhcpOptionsId=self.vpc['DhcpOptionsId'])

            self.vpc['DhcpOptionsId'] = created_dhcp_options['DhcpOptionsId']

    def _same_dhcp_options(self, desired_options, existing_options):
        desired_optns_dict = dict([(option_dict['Key'], option_dict['Values'])
                                   for option_dict in desired_options])

        existing_optns_dict = dict([(option_dict['Key'], option_dict['Values'])
                                    for option_dict in existing_options])

        return desired_optns_dict == existing_optns_dict

    def _get_dhcp_configs(self):
        internal_dns = self.get_config("internal_dns")
        external_dns = self.get_config("external_dns")
        domain_name = self.get_config("domain_name")

        # internal_dns server should be default, and for this reason it comes last.
        dhcp_configs = []
        dhcp_configs.append({"Key": "domain-name", "Values": [domain_name]})
        dhcp_configs.append({"Key": "domain-name-servers", "Values": [internal_dns, external_dns]})

        ntp_servers = self._get_ntp_server_config()
        if ntp_servers:
            dhcp_configs.append({"Key": "ntp-servers", "Values": ntp_servers})

        return dhcp_configs

    def _create_dhcp_options(self, desired_dhcp_options):

        created_dhcp_options = throttled_call(
            self.boto3_ec2.create_dhcp_options,
            DhcpConfigurations=desired_dhcp_options
        )['DhcpOptions']

        keep_trying(
            60,
            self.boto3_ec2.create_tags,
            Resources=[created_dhcp_options['DhcpOptionsId']],
            Tags=[{'Key': 'Name', 'Value': self.environment_name}]
        )

        created_dhcp_options = throttled_call(
            self.boto3_ec2.describe_dhcp_options,
            DhcpOptionsIds=[created_dhcp_options['DhcpOptionsId']]
        )['DhcpOptions']

        if not created_dhcp_options:
            raise RuntimeError("Failed to find DHCP options after creation.")

        throttled_call(self.boto3_ec2.associate_dhcp_options,
                       DhcpOptionsId=created_dhcp_options[0]['DhcpOptionsId'],
                       VpcId=self.vpc['VpcId'])

        return created_dhcp_options[0]

    def create(self):
        """Create a new disco style environment VPC"""

        # Create the VPC
        self._create_vpc()

        # Enable DNS
        throttled_call(self.boto3_ec2.modify_vpc_attribute,
                       VpcId=self.vpc['VpcId'], EnableDnsSupport={'Value': True})
        throttled_call(self.boto3_ec2.modify_vpc_attribute,
                       VpcId=self.vpc['VpcId'], EnableDnsHostnames={'Value': True})

        self._networks = self._create_new_meta_networks()
        if not self._skip_enis_pre_allocate:
            self._reserve_hostclass_ip_addresses()

        self._update_dhcp_options()

        self.disco_vpc_sg_rules.update_meta_network_sg_rules()
        self.disco_vpc_gateways.update_gateways_and_routes()
        self.disco_vpc_gateways.update_nat_gateways_and_routes()
        self.disco_vpc_endpoints.update()
        self.configure_notifications()
        self.disco_vpc_peerings.update_peering_connections(self)
        self.rds.update_all_clusters_in_vpc(parallel=True)

    def _get_vpc_cidr(self):
        """
        Get the vpc cidr from the config or get a random free subnet using the ip_space and the vpc_cidr_size
        :return: the allocated vpc CIDR
        """

        vpc_cidr = self.get_config("vpc_cidr")
        # if a vpc_cidr is not configured then allocate one dynamically
        if not vpc_cidr:
            ip_space = self.get_config("ip_space")
            vpc_size = self.get_config("vpc_cidr_size")

            if not ip_space and vpc_size:
                raise VPCConfigError('Cannot create VPC %s. ip_space or vpc_cidr_size missing'
                                     % self.environment_name)

            # get the cidr for all other VPCs so we can avoid overlapping with other VPCs
            occupied_network_cidrs = [vpc['cidr_block'] for vpc in self.list_vpcs()]

            vpc_cidr = get_random_free_subnet(ip_space, int(vpc_size), occupied_network_cidrs)

            if vpc_cidr is None:
                raise VPCConfigError('Cannot create VPC %s. No subnets available' % self.environment_name)

        return vpc_cidr

    def _create_vpc(self):
        """
        Create a new VPC and add the default and custom tags
        :param vpc_cidr:
        :return:
        """

        # Get the vpc CIDR
        vpc_cidr = self._get_vpc_cidr()

        # Create the new VPC
        self.vpc = throttled_call(self.boto3_ec2.create_vpc, CidrBlock=str(vpc_cidr))['Vpc']
        throttled_call(self.boto3_ec2.get_waiter('vpc_exists').wait, VpcIds=[self.vpc['VpcId']])
        throttled_call(self.boto3_ec2.get_waiter('vpc_available').wait, VpcIds=[self.vpc['VpcId']])

        # Add tags to VPC
        self._add_vpc_tags()

        logger.debug("vpc: %s", self.vpc)

    def get_vpc_tags(self):
        """Get tags for this VPC"""
        tags = {
            'Name': self.environment_name,
            'type': self.environment_type,
            'create_date': datetime.utcnow().isoformat(),
            'application': self.get_config('application', '')
        }

        if self._vpc_tags:
            # Add the extra tags to the list of default tags
            tags.update(self._vpc_tags)

        return tags

    def _add_vpc_tags(self):
        """
        Add tags to VPC. This function will add the default tags and the tags specified on the create
        vpc command
        """
        # get the resource representing the new VPC
        ec2 = boto3.resource('ec2')
        vpc = ec2.Vpc(self.vpc['VpcId'])

        tags = self.get_vpc_tags()

        tags = throttled_call(vpc.create_tags, Tags=dict_to_boto3_tags(tags))
        logger.debug("vpc tags: %s", tags)

    def configure_notifications(self, dry_run=False):
        """
        Configure SNS topics for CloudWatch alarms.
        Note that topics are not deleted with the VPC, since that would require re-subscribing the members.
        """
        notifications = self.alarms_config.get_notifications()
        logger.info("Desired alarms config: %s", notifications)
        if not dry_run:
            DiscoSNS().update_sns_with_notifications(notifications, self.environment_name)

    def assign_eip(self, instance, eip_address, allow_reassociation=False):
        """
        Assign EIP to an instance
        """
        eip = throttled_call(self.boto3_ec2.describe_addresses, PublicIps=[eip_address])['Addresses'][0]
        try:
            throttled_call(self.boto3_ec2.associate_address,
                           InstanceId=instance.id,
                           AllocationId=eip['AllocationId'],
                           AllowReassociation=allow_reassociation)
        except EC2ResponseError:
            logger.exception("Skipping failed EIP association. Perhaps reassociation of EIP is not allowed?")

    def vpc_filters(self):
        """Filters used to get only the current VPC when filtering an AWS reply by 'vpc-id'"""
        return create_filters({'vpc-id': [self.vpc['VpcId']]})

    def update(self, dry_run=False):
        """ Update the existing VPC """
        # Ignoring changes in CIDR for now at least

        logger.info("Updating DHCP options")
        self._update_dhcp_options(dry_run)
        logger.info("Updating security group rules...")
        self.disco_vpc_sg_rules.update_meta_network_sg_rules(dry_run)
        logger.info("Updating gateway routes...")
        self.disco_vpc_gateways.update_gateways_and_routes(dry_run)
        logger.info("Updating NAT gateways and routes...")
        self.disco_vpc_gateways.update_nat_gateways_and_routes(dry_run)
        logger.info("Updating VPC S3 endpoints...")
        self.disco_vpc_endpoints.update(dry_run=dry_run)
        logger.info("Updating VPC peering connections...")
        self.disco_vpc_peerings.update_peering_connections(self, dry_run, delete_extra_connections=True)
        logger.info("Updating alarm notifications...")
        self.configure_notifications(dry_run)

    def destroy(self):
        """ Delete all VPC resources in the right order and then delete the vpc itself """
        DiscoAlarm(self.environment_name).delete_environment_alarms(self.environment_name)
        self.log_metrics.delete_all_metrics()
        self.log_metrics.delete_all_log_groups()
        self._destroy_instances()
        self.elb.destroy_all_elbs()
        self._destroy_rds()
        self.elasticache.delete_all_cache_clusters(wait=True)
        self.elasticache.delete_all_subnet_groups()
        self.disco_vpc_gateways.destroy_nat_gateways()
        self.disco_vpc_gateways.destroy_igw_and_detach_vgws()
        self._destroy_interfaces()
        self.disco_vpc_sg_rules.destroy()
        self.disco_vpc_peerings.delete_peerings(self.get_vpc_id())
        self._destroy_subnets()
        self.disco_vpc_endpoints.delete()
        self._destroy_routes()
        self._destroy_vpc()

    def get_all_subnets(self):
        """ Returns a list of all the subnets in the current VPC """
        return throttled_call(self.boto3_ec2.describe_subnets, Filters=self.vpc_filters())['Subnets']

    def _destroy_instances(self):
        """ Find all instances in vpc and terminate them """
        discogroup = DiscoGroup(environment_name=self.environment_name)
        discogroup.delete_groups(force=True)
        reservations = throttled_call(self.boto3_ec2.describe_instances,
                                      Filters=self.vpc_filters())['Reservations']
        instances = [i['InstanceId']
                     for r in reservations
                     for i in r['Instances']]

        if not instances:
            logger.debug("No running instances")
            return
        logger.debug("terminating %s instance(s) %s", len(instances), instances)

        throttled_call(self.boto3_ec2.terminate_instances, InstanceIds=instances)

        throttled_call(self.boto3_ec2.get_waiter('instance_terminated').wait, InstanceIds=instances,
                       Filters=create_filters({'instance-state-name': ['terminated']}))
        discogroup.clean_configs()

        logger.debug("waiting for instance shutdown scripts")
        time.sleep(60)  # see http://copperegg.com/hooking-into-the-aws-shutdown-flow/

    def _destroy_rds(self, wait=True):
        """ Delete all RDS instances/clusters. Final snapshots are automatically taken. """
        self.rds.delete_all_db_instances(wait=wait)

    def _destroy_interfaces(self):
        """ Deleting interfaces explicitly lets go of subnets faster """

        def _destroy():
            interfaces = throttled_call(self.boto3_ec2.describe_network_interfaces,
                                        Filters=self.vpc_filters())["NetworkInterfaces"]
            for interface in interfaces:
                if 'Attachment' in interface:
                    throttled_call(
                        self.boto3_ec2.detach_network_interface,
                        AttachmentId=interface['Attachment']['AttachmentId'],
                        Force=True
                    )
                throttled_call(
                    self.boto3_ec2.delete_network_interface,
                    NetworkInterfaceId=interface['NetworkInterfaceId']
                )

        # Keep trying because delete could fail for reasons based on interface's state
        keep_trying(600, _destroy)

    def _destroy_subnets(self):
        """ Find all subnets belonging to a vpc and destroy them"""
        subnets = throttled_call(self.boto3_ec2.describe_subnets, Filters=self.vpc_filters())['Subnets']
        for subnet in subnets:
            throttled_call(self.boto3_ec2.delete_subnet, SubnetId=subnet['SubnetId'])

    def _destroy_routes(self):
        """ Find all route_tables belonging to vpc and destroy them"""
        routes = throttled_call(self.boto3_ec2.describe_route_tables,
                                Filters=self.vpc_filters())['RouteTables']
        for route_table in routes:
            if route_table["Associations"] and route_table["Associations"][0]["Main"]:
                logger.info("Skipping the default main route table %s", route_table['RouteTableId'])
                continue
            try:
                throttled_call(self.boto3_ec2.delete_route_table, RouteTableId=route_table['RouteTableId'])
            except EC2ResponseError:
                logger.error("Error deleting route_table %s:.", route_table['RouteTableId'])
                raise

    def _destroy_vpc(self):
        """Delete VPC and then delete the dhcp_options that were associated with it. """

        # save function and parameters so we can delete dhcp_options after vpc.
        dhcp_options_id = self.vpc['DhcpOptionsId']

        throttled_call(self.boto3_ec2.delete_vpc, VpcId=self.get_vpc_id())
        self.vpc = None

        dhcp_options = throttled_call(self.boto3_ec2.describe_dhcp_options,
                                      DhcpOptionsIds=[dhcp_options_id])['DhcpOptions']
        # If DHCP options didn't get created correctly during VPC creation, what we have here
        # could be the default DHCP options, which cannot be deleted. We need to check the tag
        # to make sure we are deleting the one that belongs to the VPC.
        if dhcp_options and dhcp_options[0].get('Tags'):
            tags = tag2dict(dhcp_options[0]['Tags'])
            if tags.get('Name') == self.environment_name:
                throttled_call(self.boto3_ec2.delete_dhcp_options, DhcpOptionsId=dhcp_options_id)

    @staticmethod
    def list_vpcs():
        """Returns list of boto.vpc.vpc.VPC classes, one for each existing VPC"""
        client = boto3.client('ec2')
        vpcs = throttled_call(client.describe_vpcs)
        return [{'id': vpc['VpcId'],
                 'tags': tag2dict(vpc['Tags'] if 'Tags' in vpc else None),
                 'cidr_block': vpc['CidrBlock']}
                for vpc in vpcs['Vpcs']]
