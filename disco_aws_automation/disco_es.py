"""
Manage AWS ElasticSearch
"""
import logging
import time
import boto3

from ConfigParser import NoOptionError
from .disco_iam import DiscoIAM
from .disco_route53 import DiscoRoute53
from .resource_helper import throttled_call
from .disco_aws_util import is_truthy
from .disco_constants import DEFAULT_CONFIG_SECTION


class DiscoES(object):
    """
    A simple class to manage ElasticSearch
    """

    def __init__(self, config_aws, config_vpc, environment_name):
        self.conn = boto3.client('es')
        self._config_aws = config_aws
        self._config_vpc = config_vpc
        self.environment_name = environment_name.lower()
        self.route53 = DiscoRoute53()

    @property
    def _cluster_name(self):
        return "{0}-log-es".format(self.environment_name)

    def list(self):
        """List all elasticsearch domains in an account"""
        response = throttled_call(self.conn.list_domain_names)

        return sorted([domain['DomainName'] for domain in
                       response['DomainNames']])

    def _add_route53(self):
        while not self.get_endpoint(self._cluster_name):
            time.sleep(60)

        zone = self.get_aws_option('domain_name')
        name = '{}.{}'.format(self._cluster_name, zone)
        value = self.get_endpoint(self._cluster_name)

        return self.route53.create_record(zone, name, 'CNAME', value)

    def _remove_route53(self):
        value = self.get_endpoint(self._cluster_name)

        return self.route53.delete_records_by_value('CNAME', value)

    def delete(self):
        """
        Delete an elasticsearch domain
        """
        domains = self.list()

        if self._cluster_name not in domains:
            logging.info('Elasticsearch domain %s does not exist. Nothing to delete.', self._cluster_name)
            return

        logging.info('Deleting elasticsearch domain %s', self._cluster_name)
        self._remove_route53()
        throttled_call(self.conn.delete_elasticsearch_domain,
                       DomainName=self._cluster_name)

    def _describe_es_domain(self, cluster_name):
        """
        Returns domain configuration information about the specified
        Elasticsearch domain, including the domain ID, domain endpoint, and
        domain ARN.
        """
        return self.conn.describe_elasticsearch_domain(DomainName=cluster_name)

    def get_endpoint(self, cluster_name):
        '''
        Get elasticsearch service endpoint
        '''
        try:
            return self._describe_es_domain(cluster_name)['DomainStatus']['Endpoint']
        except:
            return False

    def _access_policy(self):
        disco_iam = DiscoIAM(
            environment=self.environment_name,
            boto2_connection=self.aws.connection
        )
        proxy_hostclass = self.get_aws_option('http_proxy_hostclass')
        proxy_ip = self.get_hostclass_option('eip', proxy_hostclass)

        policy = '''
                {{
                  "Version": "2012-10-17",
                  "Statement": [
                    {{
                      "Effect": "Allow",
                      "Principal": {{
                        "AWS": "*"
                      }},
                      "Action": "es:*",
                      "Resource": "arn:aws:es:{region}:{account_id}:domain/{cluster_name}/*",
                      "Condition": {{
                        "IpAddress": {{
                          "aws:SourceIp": [
                            "66.104.227.162",
                            "38.117.159.162",
                            "{proxy_ip}"
                          ]
                        }}
                      }}
                    }}
                  ]
                }}
                '''

        return policy.format(region=self.vpc.region, account_id=disco_iam.account_id(),
                             cluster_name=self._cluster_name, proxy_ip=proxy_ip)

    def create(self):
        '''
        Create elasticsearch cluster using _upsert method
        Configuration is read from disco_vpc.ini
        '''
        logging.info('Creating elasticsearch domain %s', self._cluster_name)
        self._upsert(self.conn.create_elasticsearch_domain)
        self._add_route53()

    def update(self):
        '''
        Update elasticsearch cluster using _upsert method
        Configuration is read from disco_vpc.ini
        '''
        logging.info('Updating elasticsearch domain %s', self._cluster_name)
        self._upsert(self.conn.update_elasticsearch_domain_config)
        self._add_route53()

    def _upsert(self, generator):
        es_cluster_config = {
            'InstanceType': self.get_vpc_option('es_instance_type', 'm3.medium.elasticsearch'),
            'InstanceCount': int(self.get_vpc_option('es_instance_count', 1)),
            'DedicatedMasterEnabled': bool(self.get_vpc_option('es_dedicated_master', False)),
            'ZoneAwarenessEnabled': bool(self.get_vpc_option('es_zone_awareness', False))
        }

        if is_truthy(es_cluster_config['DedicatedMasterEnabled']):
            es_cluster_config['DedicatedMasterType'] = self.get_vpc_option('es_dedicated_master_type')
            es_cluster_config['DedicatedMasterCount'] = int(
                self.get_vpc_option('es_dedicated_master_count')
            )

        ebs_option = {
            'EBSEnabled': bool(self.get_vpc_option('es_ebs_enabled', False))
        }

        if is_truthy(ebs_option['EBSEnabled']):
            ebs_option['VolumeType'] = self.get_vpc_option('es_volume_type', 'standard')
            ebs_option['VolumeSize'] = int(self.get_vpc_option('es_volume_size', 10))

            if ebs_option['VolumeType'] == 'io1':
                ebs_option['Iops'] = int(self.get_vpc_option('es_iops', 1000))

        snapshot_options = {
            'AutomatedSnapshotStartHour': int(self.get_vpc_option('es_snapshot_start_hour', 5))
        }

        es_kwargs = {
            'DomainName': self._cluster_name,
            'ElasticsearchClusterConfig': es_cluster_config,
            'EBSOptions': ebs_option,
            'AccessPolicies': self._access_policy(),
            'SnapshotOptions': snapshot_options
        }

        throttled_call(generator, **es_kwargs)

    def get_vpc_option(self, option, default=None):
        '''Returns appropriate configuration for the current environment'''
        env_section = "env:{0}".format(self.environment_name)
        envtype_section = "envtype:{0}".format(self.environment_name)
        envtype_sandbox_section = "envtype:{0}".format("sandbox")
        peering_section = "peerings"

        if self._config_vpc.has_option(env_section, option):
            value = self._config_vpc.get(env_section, option)
        elif self._config_vpc.has_option(envtype_section, option):
            value = self._config_vpc.get(envtype_section, option)
        elif self._config_vpc.has_option(envtype_sandbox_section, option):
            value = self._config_vpc.get(envtype_sandbox_section, option)
        elif self._config_vpc.has_option(peering_section, option):
            value = self._config_vpc.get(peering_section, option)

        return value or default

    def get_aws_option(self, option, section=DEFAULT_CONFIG_SECTION, default=None):
        """Get a value from the config"""
        env_option = "{0}@{1}".format(option, self.environment_name)
        default_option = "default_{0}".format(option)
        default_env_option = "default_{0}".format(env_option)

        if self._config_aws.has_option(section, env_option):
            value = self._config_aws.get(section, env_option)
        if self._config_aws.has_option(section, option):
            value = self._config_aws.get(section, option)
        elif self._config_aws.has_option(DEFAULT_CONFIG_SECTION, default_env_option):
            value = self._config_aws.get(DEFAULT_CONFIG_SECTION, default_env_option)
        elif self._config_aws.has_option(DEFAULT_CONFIG_SECTION, default_option):
            value = self._config_aws.get(DEFAULT_CONFIG_SECTION, default_option)

        return value or default

    def get_hostclass_option(self, option, hostclass, default=None):
        """Fetch a hostclass configuration option, if it does not exist get the default"""
        return self.get_aws_option(option, hostclass, default)
