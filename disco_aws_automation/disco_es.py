"""
Manage AWS ElasticSearch
"""
import logging
import time
import boto3

from .disco_route53 import DiscoRoute53
from .resource_helper import throttled_call
from .disco_aws_util import is_truthy


class DiscoES(object):
    """
    A simple class to manage ElasticSearch
    """

    def __init__(self, config):
        self.conn = boto3.client('es')
        self.config = config
        self.route53 = DiscoRoute53()

    @property
    def _cluster_name(self):
        return "{0}-log-es".format(self.config.environment_name)

    def list(self):
        """List all elasticsearch domains in an account"""
        response = throttled_call(self.conn.list_domain_names)

        return sorted([domain['DomainName'] for domain in
                       response['DomainNames']])

    def _add_route53(self):
        while not self.get_endpoint(self._cluster_name):
            time.sleep(60)

        zone = self.config.get_aws_option('domain_name')
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
        """
        Construct an access policy for the new elasticsearch cluster. Needs to be dynamically created because
        it will use the environment's proxy hostclass to forward requests to the elasticsearch cluster and the
        IP of the proxy hostclass could be different at run time.
        """
        proxy_hostclass = self.config.get_aws_option('http_proxy_hostclass')
        proxy_ip = self.config.get_hostclass_option('eip', proxy_hostclass)

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

        return policy.format(region=self.config.region, account_id=self.config.account_id,
                             cluster_name=self._cluster_name, proxy_ip=proxy_ip)

    def create(self):
        '''
        Create elasticsearch cluster.
        '''
        logging.info('Creating elasticsearch domain %s', self._cluster_name)

        # Get the latest elasticsearch cluster config.
        es_config = self._get_es_config()
        # Create a new elasticsearch config using the latest config.
        throttled_call(self.conn.create_elasticsearch_domain, **es_config)
        self._add_route53()

    def update(self):
        '''
        Update elasticsearch cluster.
        '''
        logging.info('Updating elasticsearch domain %s', self._cluster_name)

        # Get the latest elasticsearch cluster config.
        es_config = self._get_es_config()
        # Update the elasticsearch cluster config to be the latest one.
        throttled_call(self.conn.update_elasticsearch_domain_config, **es_config)
        self._add_route53()

    def _get_es_config(self):
        """
        Create boto3 config for the elasticsearch cluster.
        """
        es_cluster_config = {
            'InstanceType': self.config.get_es_option('es_instance_type', 'm3.medium.elasticsearch'),
            'InstanceCount': int(self.config.get_es_option('es_instance_count', 1)),
            'DedicatedMasterEnabled': bool(self.config.get_es_option('es_dedicated_master', False)),
            'ZoneAwarenessEnabled': bool(self.config.get_es_option('es_zone_awareness', False))
        }

        if is_truthy(es_cluster_config['DedicatedMasterEnabled']):
            es_cluster_config['DedicatedMasterType'] = self.config.get_es_option('es_dedicated_master_type')
            es_cluster_config['DedicatedMasterCount'] = int(
                self.config.get_es_option('es_dedicated_master_count')
            )

        ebs_option = {
            'EBSEnabled': bool(self.config.get_es_option('es_ebs_enabled', False))
        }

        if is_truthy(ebs_option['EBSEnabled']):
            ebs_option['VolumeType'] = self.config.get_es_option('es_volume_type', 'standard')
            ebs_option['VolumeSize'] = int(self.config.get_es_option('es_volume_size', 10))

            if ebs_option['VolumeType'] == 'io1':
                ebs_option['Iops'] = int(self.config.get_es_option('es_iops', 1000))

        snapshot_options = {
            'AutomatedSnapshotStartHour': int(self.config.get_es_option('es_snapshot_start_hour', 5))
        }

        config = {
            'DomainName': self._cluster_name,
            'ElasticsearchClusterConfig': es_cluster_config,
            'EBSOptions': ebs_option,
            'AccessPolicies': self._access_policy(),
            'SnapshotOptions': snapshot_options
        }

        return config
