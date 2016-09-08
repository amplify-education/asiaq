"""
Manage AWS ElasticSearch
"""
import logging
import time
import json
from ConfigParser import NoOptionError

import boto3

from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError
from . import read_config
from .disco_route53 import DiscoRoute53
from .resource_helper import throttled_call
from .disco_aws_util import is_truthy
from .disco_constants import (
    DEFAULT_CONFIG_SECTION,
    VPC_CONFIG_FILE,
    ES_CONFIG_FILE
)
from .disco_alarm import DiscoAlarm
from .disco_alarm_config import DiscoAlarmsConfig

logger = logging.getLogger(__name__)


class DiscoElasticsearch(object):
    """
    A simple class to manage ElasticSearch
    """

    def __init__(self, environment_name, config_aws=None, config_es=None,
                 config_vpc=None, route53=None, alarms=None):
        self.config_aws = config_aws or read_config()
        self.config_vpc = config_vpc or read_config(VPC_CONFIG_FILE)
        self.config_es = config_es or read_config(ES_CONFIG_FILE)
        self.route53 = route53 or DiscoRoute53()

        if environment_name:
            self.environment_name = environment_name.lower()
        else:
            self.environment_name = self.config_aws.get("disco_aws", "default_environment")

        self._conn = None  # Lazily initialized
        self._session = None  # Lazily initialized
        self._account_id = None  # Lazily initialized
        self._region = None  # Lazily initialized
        self._zone = None  # Lazily initialized
        self._alarms = alarms or None  # Lazily initialized

    @property
    def conn(self):
        """The boto3 elasticsearch connection object"""
        if not self._conn:
            self._conn = boto3.client('es')
        return self._conn

    @property
    def session(self):
        """Boto3 session"""
        if not self._session:
            self._session = boto3.session.Session()
        return self._session

    @property
    def account_id(self):
        """Account id of the current IAM user"""
        if not self._account_id:
            self._account_id = boto3.resource('iam').CurrentUser().arn.split(':')[4]
        return self._account_id

    @property
    def region(self):
        """Current region used by Boto"""
        if not self._region:
            # Doing this requires boto3>=1.2.4
            # Could use undocumented and unsupported workaround for earlier versions:
            # session._session.get_config_variable('region')
            self._region = self.session.region_name
        return self._region

    @property
    def zone(self):
        """The current Route 53 zone"""
        if not self._zone:
            self._zone = self.get_aws_option('domain_name')
        return self._zone

    @property
    def alarms(self):
        """Disco alarms object"""
        if not self._alarms:
            alarm_configs = DiscoAlarmsConfig(self.environment_name, elasticsearch=self)
            self._alarms = DiscoAlarm(environment=self.environment_name, alarm_configs=alarm_configs)
        return self._alarms

    def get_domain_name(self, elasticsearch_name):
        """
        Get the name of the ElasticSearch domain.
        Follows the format 'es-{elasticsearch_name}-{environment_name}'
        """
        return "es-{}-{}".format(elasticsearch_name, self.environment_name)

    def _list(self):
        """
        List all active ElasticSearch domains
        """
        response = throttled_call(self.conn.list_domain_names)
        return sorted([domain['DomainName'] for domain in response['DomainNames']])

    def list(self, include_endpoint=False):
        """
        Lists information about all active ElasticSearch domains, filtered by the current environment

        Returns a list of dictionaries, typically like this:

        [
            {
                "elasticsearch_domain_name": "es-logging-ci",
                "route_53_endpoint": "es-logging-ci.aws.wgen.net",
                "internal_name": "logging",
            }
        ]

        If endpoint is included, then the response will include the endpoint of the domain, provided it
        exists.

        [
            {
                "elasticsearch_domain_name": "es-logging-ci",
                "route_53_endpoint": "es-logging-ci.aws.wgen.net",
                "internal_name": "logging",
                "elasticsearch_endpoint": "search-es-logging-ci-xxxxxxxxxxxxxxxx.us-west-2.es.amazonaws.com",
            }
        ]
        """
        domain_infos = []
        for domain_name in self._list():
            # Somewhat annoying logic to handle the fact that elasticsearch names are allowed to have '-'
            # in them.
            domain_name_components = domain_name.split("-")
            prefix = domain_name_components[0]
            environment_name = domain_name_components[-1]
            elasticsearch_name = "-".join(domain_name_components[1:-1])
            if prefix != "es":
                logger.info(
                    "Could not parse ElasticSearch domain %s, expected format 'es-$name-$env'",
                    domain_name
                )
                continue
            if environment_name != self.environment_name:
                logger.debug(
                    "ElasticSearch domain %s is associated with a different environment, ignoring",
                    domain_name
                )
                continue

            domain_info = {}

            domain_info["elasticsearch_domain_name"] = domain_name
            domain_info["route_53_endpoint"] = "{}.{}".format(domain_name, self.zone)
            domain_info["internal_name"] = elasticsearch_name

            if include_endpoint:
                domain_info["elasticsearch_endpoint"] = self.get_endpoint(domain_name)

            domain_infos.append(domain_info)

        return domain_infos

    def _add_route53(self, domain_name):
        """Add a Route 53 record for the given domain name"""
        # Wait until AWS attaches an endpoint to the ElasticSearch domain
        while not self.get_endpoint(domain_name):
            logger.info('Waiting for ElasticSearch domain %s to finish being created', domain_name)
            time.sleep(60)

        name = '{}.{}'.format(domain_name, self.zone)
        value = self.get_endpoint(domain_name)

        return self.route53.create_record(self.zone, name, 'CNAME', value)

    def _remove_route53(self, domain_name):
        """Remove all Route 53 records for the given domain name"""
        value = self.get_endpoint(domain_name)

        return self.route53.delete_records_by_value('CNAME', value)

    def _describe_es_domain(self, domain_name):
        """
        Returns domain configuration information about the specified
        Elasticsearch domain, including the domain ID, domain endpoint, and
        domain ARN.
        """
        return self.conn.describe_elasticsearch_domain(DomainName=domain_name)

    def get_endpoint(self, domain_name):
        """
        Get Elasticsearch service endpoint
        """
        try:
            return self._describe_es_domain(domain_name)['DomainStatus']['Endpoint']
        except (BotoCoreError, Boto3Error, KeyError):
            return None

    def get_client_id(self, domain_name):
        """
        Get the client id of the ElasticSearch domain.
        """
        domain_description = self._describe_es_domain(domain_name)
        return domain_description['DomainStatus']['DomainId'].split('/')[0]

    def _access_policy(self, domain_name, allowed_source_ips):
        """
        Construct an access policy for the new Elasticsearch cluster. Needs to be dynamically created because
        it will use the environment's NAT Gateway to forward requests to the elasticsearch cluster and the
        IP addresses of the NAT Gateway would be read from disco_vpc.ini.
        """
        nat_eips = self._get_nat_eips()
        if nat_eips:
            allowed_source_ips += nat_eips.split(',')

        resource = "arn:aws:es:{region}:{account}:domain/{domain_name}/*".format(region=self.region,
                                                                                 account=self.account_id,
                                                                                 domain_name=domain_name)

        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "AWS": "*"
                    },
                    "Action": "es:*",
                    "Resource": resource,
                    "Condition": {
                        "IpAddress": {
                            "aws:SourceIp": allowed_source_ips
                        }
                    }
                }
            ]
        }
        return json.dumps(policy)

    def update(self, elasticsearch_name=None, es_config=None):
        """
        Update/Create an Elasticsearch Domain.

        If the Elasticsearch Domain does not exist, it is created. If it already exists, it is updated.

        Params:

        elasticsearch_name: The internal name of the elasticsearch domain to update. If none is provided,
        the configuration file is parsed for all configured elasticsearch domains and all are created and/or
        updated as needed.

        es_config: The Elasticsearch configuration object to use for updating the elasticsearch domain. If not
        provided, the configuration is looked up from the configuration file. Overrides the value of the
        elasticsearch_name parameter.
        """
        configured_elasticsearch_names = self._get_elasticsearch_names()

        if es_config:
            domain_name_components = es_config["DomainName"].split("-")
            desired_elasticsearch_names = ["-".join(domain_name_components[1:-1])]
        elif elasticsearch_name:
            # If the elasticsearch_name exists, only update that one
            desired_elasticsearch_names = [elasticsearch_name]
        else:
            # Otherwise, update all configured elasticsearch domains
            desired_elasticsearch_names = configured_elasticsearch_names

        # Get a list of all the elasticsearch_names that exist in the current environment
        all_elasticsearch_names = [domain_info["internal_name"] for domain_info in self.list()]

        for desired_elasticsearch_name in desired_elasticsearch_names:
            domain_name = self.get_domain_name(desired_elasticsearch_name)

            # If no configuration was provided and the desired elasticsearch name isn't configured, ignore it
            if not es_config and desired_elasticsearch_name not in configured_elasticsearch_names:
                logger.info("Cannot create or update unconfigured Elasticsearch domain %s", domain_name)
                continue

            # Get the latest elasticsearch config.
            desired_es_config = es_config or self._get_es_config(desired_elasticsearch_name)

            if desired_elasticsearch_name in all_elasticsearch_names:
                try:
                    del(desired_es_config["ElasticsearchVersion"])
                    logging.debug("Ignoring ElasticsearchVersion specification on update")
                except KeyError:
                    pass
                logger.info('Updating ElasticSearch domain %s', domain_name)
                throttled_call(self.conn.update_elasticsearch_domain_config, **desired_es_config)
            else:
                logger.info('Creating ElasticSearch domain %s', domain_name)
                throttled_call(self.conn.create_elasticsearch_domain, **desired_es_config)

            # Add the Route 53 entry
            self._add_route53(domain_name)

            # Update alarms
            self.alarms.create_alarms(elasticsearch_name)

    def delete(self, elasticsearch_name=None, delete_all=False):
        """
        Delete an ElasticSearch domain.

        Params:

        elasticsearch_name: The internal name of the elasticsearch domain to delete. If none if provided,
        the configuration file is parsed for all configured elasticsearch domains and all are deleted.

        delete_all: If true, all elasticsearch domains for the current environment are deleted, regardless of
        whether or not they exist in the configuration file. Useful for when destroying a VPC. Generally
        should not be used. Also causes the value of elasticsearch_name to be ignored.
        """
        all_elasticsearch_names = [domain_info["internal_name"] for domain_info in self.list()]
        if delete_all:
            desired_elasticsearch_names = all_elasticsearch_names
        else:
            if elasticsearch_name:
                desired_elasticsearch_names = [elasticsearch_name]
            else:
                desired_elasticsearch_names = self._get_elasticsearch_names()

        for elasticsearch_name in desired_elasticsearch_names:
            domain_name = self.get_domain_name(elasticsearch_name)
            if elasticsearch_name not in all_elasticsearch_names:
                logger.info('ElasticSearch domain %s does not exist. Nothing to delete.', domain_name)
                continue

            logger.info('Deleting ElasticSearch domain %s', domain_name)
            self._remove_route53(domain_name)
            throttled_call(self.conn.delete_elasticsearch_domain, DomainName=domain_name)

            # Destroy alarms
            self.alarms.delete_hostclass_environment_alarms(
                self.environment_name, elasticsearch_name
            )

    def _get_elasticsearch_names(self):
        """
        Returns a list of all ElasticSearch names defined for the current environment in the config files.
        """
        elasticsearch_names = []

        for section in self.config_es.sections():
            if section != "defaults":
                environment_name, elasticsearch_name = section.split(":")
                if environment_name == self.environment_name:
                    elasticsearch_names.append(elasticsearch_name)

        return elasticsearch_names

    def _get_es_config(self, elasticsearch_name):
        """
        Create boto3 config for the ElasticSearch cluster.
        """
        es_cluster_config = {
            'InstanceType': self.get_es_option('instance_type', elasticsearch_name),
            'InstanceCount': int(self.get_es_option('instance_count', elasticsearch_name)),
            'DedicatedMasterEnabled': is_truthy(self.get_es_option('dedicated_master',
                                                                   elasticsearch_name)),
            'ZoneAwarenessEnabled': is_truthy(self.get_es_option('zone_awareness',
                                                                 elasticsearch_name))
        }

        if es_cluster_config['DedicatedMasterEnabled']:
            es_cluster_config['DedicatedMasterType'] = self.get_es_option('dedicated_master_type',
                                                                          elasticsearch_name)
            es_cluster_config['DedicatedMasterCount'] = int(
                self.get_es_option('dedicated_master_count', elasticsearch_name)
            )

        ebs_option = {
            'EBSEnabled': is_truthy(self.get_es_option('ebs_enabled', elasticsearch_name))
        }

        if ebs_option['EBSEnabled']:
            ebs_option['VolumeType'] = self.get_es_option('volume_type', elasticsearch_name)
            ebs_option['VolumeSize'] = int(self.get_es_option('volume_size', elasticsearch_name))

            if ebs_option['VolumeType'] == 'io1':
                ebs_option['Iops'] = int(self.get_es_option('iops', elasticsearch_name))

        snapshot_options = {
            'AutomatedSnapshotStartHour': int(self.get_es_option('snapshot_start_hour',
                                                                 elasticsearch_name))
        }

        domain_name = self.get_domain_name(elasticsearch_name)

        # Treat 'allowed_source_ips' as a space separated list of IP addresses and make it into a list
        allowed_source_ips = self.get_es_option("allowed_source_ips", elasticsearch_name).split()

        config = {
            'ElasticsearchVersion': self.get_es_option('version', elasticsearch_name),
            'DomainName': domain_name,
            'ElasticsearchClusterConfig': es_cluster_config,
            'EBSOptions': ebs_option,
            'AccessPolicies': self._access_policy(domain_name, allowed_source_ips),
            'SnapshotOptions': snapshot_options
        }

        return config

    def get_es_option(self, option, elasticsearch_name):
        """Returns appropriate configuration for the current environment"""
        section = "{}:{}".format(self.environment_name, elasticsearch_name)

        if self.config_es.has_option(section, option):
            return self.config_es.get(section, option)
        elif self.config_es.has_option('defaults', option):
            # Get option from defaults section if it's not found in the cluster's section
            return self.config_es.get('defaults', option)

        raise RuntimeError("Could not find option, %s, in either the %s and the defaults sections "
                           "of the ElasticSearch config.",
                           option, section)

    def get_aws_option(self, option, section=DEFAULT_CONFIG_SECTION):
        """Get a value from the config"""
        env_option = "{0}@{1}".format(option, self.environment_name)
        default_option = "default_{0}".format(option)
        default_env_option = "default_{0}".format(env_option)

        if self.config_aws.has_option(section, env_option):
            return self.config_aws.get(section, env_option)
        if self.config_aws.has_option(section, option):
            return self.config_aws.get(section, option)
        elif self.config_aws.has_option(DEFAULT_CONFIG_SECTION, default_env_option):
            return self.config_aws.get(DEFAULT_CONFIG_SECTION, default_env_option)
        elif self.config_aws.has_option(DEFAULT_CONFIG_SECTION, default_option):
            return self.config_aws.get(DEFAULT_CONFIG_SECTION, default_option)

        raise NoOptionError(option, section)

    def get_aws_option_default(self, option, section=DEFAULT_CONFIG_SECTION, default=None):
        """Get a value from the config"""
        try:
            return self.get_aws_option(option, section)
        except NoOptionError:
            return default

    def get_hostclass_option(self, option, hostclass):
        """Fetch a hostclass configuration option, if it does not exist get the default"""
        return self.get_aws_option(option, hostclass)

    def get_hostclass_option_default(self, option, hostclass, default=None):
        """Fetch a hostclass configuration option, if it does not exist get the default"""
        return self.get_aws_option_default(option, hostclass, default)

    def _get_nat_eips(self):
        env_option = 'envtype:{}'.format(self.environment_name)
        if self.config_vpc.has_option(env_option, 'tunnel_nat_gateways'):
            return self.config_vpc.get(env_option, 'tunnel_nat_gateways')
        else:
            return None
