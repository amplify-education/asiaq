"""
Tests of disco_elasticache
"""
import random
import json

from unittest import TestCase
from mock import MagicMock
from disco_aws_automation import DiscoElasticsearch
from disco_aws_automation.disco_aws_util import is_truthy
from test.helpers.patch_disco_aws import get_mock_config

MOCK_AWS_CONFIG_DEFINITION = {
    "disco_aws": {
        "default_domain_name": "aws.example.com",
    }
}

MOCK_VPC_CONFIG_DEFINITION = {
    "envtype:foo": {
        "tunnel_nat_gateways": "1.1.1.1,2.2.2.2,3.3.3.3"
    }
}

MOCK_ES_CONFIG_DEFINITION = {
    "foo:logs": {
        "instance_type": "m3.medium.elasticsearch",
        "instance_count": "3",
        "dedicated_master": "yes",
        "zone_awareness": "true",
        "dedicated_master_type": "m3.medium.elasticsearch",
        "dedicated_master_count": "1",
        "ebs_enabled": "true",
        "volume_type": "io1",
        "volume_size": "10",
        "iops": "10000",
        "snapshot_start_hour": "5",
        "version": "1.5",
        "allowed_source_ips": "192.0.2.100 192.0.2.200"
    },
    "foo:other-logs": {
        "instance_type": "t2.medium.elasticsearch",
        "instance_count": "1",
        "ebs_enabled": "True",
        "volume_type": "standard",
        "volume_size": "10",
        "snapshot_start_hour": "8"
    },
    "bar:strange-logs": {
        "instance_type": "t2.medium.elasticsearch",
        "instance_count": "1",
        "ebs_enabled": "True",
        "volume_type": "standard",
        "volume_size": "10",
        "snapshot_start_hour": "5"
    },
    "defaults": {
        "instance_type": "m3.medium.elasticsearch",
        "instance_count": "1",
        "dedicated_master": "False",
        "zone_awareness": "False",
        "ebs_enabled": "False",
        "volume_type": "standard",
        "volume_size": "10",
        "iops": "1000",
        "snapshot_start_hour": "5",
        "version": "2.3",
        "allowed_source_ips": ""
    }
}


def _get_mock_route53():
    route53 = MagicMock()
    return route53


# Pylint thinks the test method names are too long. Test method names should be long and descriptive...
# pylint: disable=invalid-name
class DiscoElastiSearchTests(TestCase):
    """Test DiscoElasticSearch"""

    def setUp(self):
        self.mock_route_53 = _get_mock_route53()

        config_aws = get_mock_config(MOCK_AWS_CONFIG_DEFINITION)
        config_vpc = get_mock_config(MOCK_VPC_CONFIG_DEFINITION)
        config_es = get_mock_config(MOCK_ES_CONFIG_DEFINITION)
        self.account_id = ''.join(random.choice("0123456789") for _ in range(12))
        self.region = "us-west-2"
        self.environment_name = "foo"

        self.mock_alarms = MagicMock()

        self._es = DiscoElasticsearch(environment_name=self.environment_name, alarms=self.mock_alarms,
                                      config_aws=config_aws, config_es=config_es,
                                      config_vpc=config_vpc, route53=self.mock_route_53)

        self._es._account_id = self.account_id
        self._es._region = self.region

        self._es._conn = MagicMock()

        self.domain_configs = {}

        def _list_domain_names():
            domain_names = [{"DomainName": domain_name} for domain_name in self.domain_configs.keys()]

            return {"DomainNames": domain_names}

        # pylint doesn't like Boto3's argument names
        # pylint: disable=C0103
        def _delete_elasticsearch_domain(DomainName):
            self.domain_configs.pop(DomainName, None)

        # pylint doesn't like Boto3's argument names
        # pylint: disable=C0103
        def _describe_elasticsearch_domain(DomainName):
            return self.domain_configs[DomainName]

        def _create_elasticsearch_domain(**config):
            domain_name = config["DomainName"]
            if domain_name in self.domain_configs:
                endpoint = self.domain_configs[domain_name]["DomainStatus"]["Endpoint"]
                domain_id = self.domain_configs[domain_name]["DomainStatus"]["DomainId"]
            else:
                cluster_id = ''.join(random.choice("0123456789abcdef") for _ in range(60))
                endpoint = "search-{}-{}.{}.es.amazonaws.com".format(domain_name, cluster_id,
                                                                     self.region)
                client_id = ''.join(random.choice("0123456789") for _ in range(12))
                domain_id = "{}/{}".format(client_id, domain_name)

            config["Endpoint"] = endpoint
            config["DomainId"] = domain_id

            domain_config = {
                "DomainStatus": config
            }

            self.domain_configs[domain_name] = domain_config

        def _update_elasticsearch_domain_config(**config):
            if config["DomainName"] not in self.domain_configs:
                raise RuntimeError("Domain not found: {}".format(config["DomainName"]))
            _create_elasticsearch_domain(**config)

        self._es._conn.list_domain_names.side_effect = _list_domain_names
        self._es._conn.delete_elasticsearch_domain.side_effect = _delete_elasticsearch_domain
        self._es._conn.describe_elasticsearch_domain.side_effect = _describe_elasticsearch_domain
        self._es._conn.create_elasticsearch_domain.side_effect = _create_elasticsearch_domain
        self._es._conn.update_elasticsearch_domain_config.side_effect = _update_elasticsearch_domain_config

    def _get_endpoint(self, domain_name):
        return self.domain_configs[domain_name]["DomainStatus"]["Endpoint"]

    def _get_client_id(self, domain_name):
        return self.domain_configs[domain_name]["DomainStatus"]["DomainId"].split('/')[0]

    def test_domain_name_formatted(self):
        """Make sure that the domain name is formatted correctly"""
        elasticsearch_name = "logs"
        expected_domain_name = "es-{}-{}".format(elasticsearch_name, self.environment_name)
        self.assertEquals(expected_domain_name, self._es.get_domain_name(elasticsearch_name))

    def test_list_domains_with_no_domains(self):
        """If we list domains with no domains created, we should get no domains back"""
        self.assertEquals(self._es.list(), [])

    def test_list_domains_with_a_domain(self):
        """If we list domains with one domain created, we should get only that domain back"""
        self._es.update("logs")
        self.assertEquals(["logs"], [info["internal_name"] for info in self._es.list()])

    def test_list_domains_with_domain_from_different_environment(self):
        """If we list domains with a domain from a different environment, we shouldn't see that domain"""
        es_config = self._es._get_es_config("logs")
        self._es.update("logs", es_config)
        es_config["DomainName"] = "es-other-logs-bar"
        self._es.conn.create_elasticsearch_domain(**es_config)
        self.assertEquals(["logs"], [info["internal_name"] for info in self._es.list()])

    def test_list_domains_with_domain_with_a_bad_format(self):
        """If we list domains with a domain with a bad format, we shouldn't see that domain"""
        es_config = self._es._get_es_config("logs")
        self._es.update("logs", es_config)
        es_config["DomainName"] = "someother_format"
        self._es.conn.create_elasticsearch_domain(**es_config)
        es_config["DomainName"] = "someotherprefix-other-logs-foo"
        self._es.conn.create_elasticsearch_domain(**es_config)
        self.assertEquals(["logs"], [info["internal_name"] for info in self._es.list()])

    def test_list_domains_with_endpoints(self):
        """If we list domains with endpoints, we should get endpoints"""
        self._es.update("logs")
        self.assertIn("elasticsearch_endpoint", self._es.list(include_endpoint=True)[0])

    def test_get_endpoint_with_a_domain(self):
        """Verify that get_endpoint returns the correct endpoint for a domain"""
        elasticsearch_name = "logs"
        domain_name = self._es.get_domain_name(elasticsearch_name)
        self._es.update(elasticsearch_name)
        expected_endpoint = self._get_endpoint(domain_name)
        actual_endpoint = self._es.get_endpoint(domain_name)
        self.assertEquals(actual_endpoint, expected_endpoint)

    def test_get_client_id_with_a_domain(self):
        """Verify that get_client_id returns the correct client_id for a domain"""
        elasticsearch_name = "logs"
        domain_name = self._es.get_domain_name(elasticsearch_name)
        self._es.update(elasticsearch_name)
        expected_client_id = self._get_client_id(domain_name)
        actual_client_id = self._es.get_client_id(domain_name)
        self.assertEquals(actual_client_id, expected_client_id)

    def test_get_endpoint_with_bad_domain(self):
        """Verify that get_endpoint returns None if the requested domain_name doesn't exist"""
        self.assertEquals(self._es.get_endpoint("DoesntMatter"), None)

    def test_create_can_create_all(self):
        """Verify that when create is called with no arguments, it creates all configured domains"""
        expected_domain_names = ["es-logs-foo", "es-other-logs-foo"]
        self._es.update()
        self.assertEquals(self._es._list(), expected_domain_names)

    def test_create_domain_respects_config_files(self):
        """Verify that create respects the configuration file"""
        elasticsearch_name = "logs"
        config_section = "{}:{}".format(self.environment_name, elasticsearch_name)
        self._es.update(elasticsearch_name)
        domain_name = self._es.get_domain_name(elasticsearch_name)
        self.assertIn(domain_name, self._es._list())
        domain_config = self._es._describe_es_domain(domain_name)["DomainStatus"]
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["InstanceType"],
                          MOCK_ES_CONFIG_DEFINITION[config_section]["instance_type"])
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["InstanceCount"],
                          int(MOCK_ES_CONFIG_DEFINITION[config_section]["instance_count"]))
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["DedicatedMasterEnabled"],
                          is_truthy(MOCK_ES_CONFIG_DEFINITION[config_section]["dedicated_master"]))
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["ZoneAwarenessEnabled"],
                          is_truthy(MOCK_ES_CONFIG_DEFINITION[config_section]["zone_awareness"]))
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["DedicatedMasterType"],
                          MOCK_ES_CONFIG_DEFINITION[config_section]["dedicated_master_type"])
        self.assertEquals(domain_config["ElasticsearchClusterConfig"]["DedicatedMasterCount"],
                          int(MOCK_ES_CONFIG_DEFINITION[config_section]["dedicated_master_count"]))
        self.assertEquals(domain_config["EBSOptions"]["EBSEnabled"],
                          is_truthy(MOCK_ES_CONFIG_DEFINITION[config_section]["ebs_enabled"]))
        self.assertEquals(domain_config["EBSOptions"]["Iops"],
                          int(MOCK_ES_CONFIG_DEFINITION[config_section]["iops"]))
        self.assertEquals(domain_config["EBSOptions"]["VolumeSize"],
                          int(MOCK_ES_CONFIG_DEFINITION[config_section]["volume_size"]))
        self.assertEquals(domain_config["EBSOptions"]["VolumeType"],
                          MOCK_ES_CONFIG_DEFINITION[config_section]["volume_type"])
        self.assertEquals(domain_config["SnapshotOptions"]["AutomatedSnapshotStartHour"],
                          int(MOCK_ES_CONFIG_DEFINITION[config_section]["snapshot_start_hour"]))
        expected_source_ips = MOCK_ES_CONFIG_DEFINITION[config_section]["allowed_source_ips"].split()
        expected_nat_gateways = MOCK_VPC_CONFIG_DEFINITION['envtype:foo']["tunnel_nat_gateways"].split(',')
        expected_source_ips += expected_nat_gateways
        access_policy = json.loads(domain_config["AccessPolicies"])
        actual_source_ips = access_policy["Statement"][0]["Condition"]["IpAddress"]["aws:SourceIp"]
        self.assertEquals(set(actual_source_ips), set(expected_source_ips))

    def test_create_domain_twice_is_idempotent(self):
        """Verify that creating a domain twice is ignored and has no effect"""
        elasticsearch_name = "logs"
        self._es.update(elasticsearch_name)
        self.assertEquals(len(self._es.list()), 1)
        original_domain_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        self._es.update(elasticsearch_name)
        self.assertEquals(len(self._es.list()), 1)
        new_domain_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        del(original_domain_config['DomainStatus']['ElasticsearchVersion'])
        self.assertEquals(original_domain_config.viewitems(), new_domain_config.viewitems())

    def test_create_and_delete_a_domain(self):
        """Verify that a domain can be deleted after its been created"""
        elasticsearch_name = "logs"
        self._es.update(elasticsearch_name)
        self.assertEquals(len(self._es.list()), 1)
        self._es.delete(elasticsearch_name)
        self.assertEquals(len(self._es.list()), 0)

    def test_create_a_domain_creates_route53_record(self):
        """Verify that creating a domain makes the expected route53 record"""
        elasticsearch_name = "logs"
        self._es.update(elasticsearch_name)
        domain_name = self._es.get_domain_name(elasticsearch_name)
        endpoint = self._get_endpoint(domain_name)
        self.mock_route_53.create_record.assert_called_once_with(self._es.zone,
                                                                 '{}.{}'.format(domain_name, self._es.zone),
                                                                 'CNAME', endpoint)

    def test_create_a_domain_creates_alarms(self):
        """Verify that a domain can be deleted after its been created"""
        elasticsearch_name = "logs"
        self._es.update(elasticsearch_name)
        self.mock_alarms.create_alarms.assert_called_once_with(elasticsearch_name)

    def test_delete_domain_with_no_domain(self):
        """Verify that deleting a domain that does not exist throws no exception and has no effect"""
        elasticsearch_names = ["logs", "other-logs"]
        for elasticsearch_name in elasticsearch_names:
            self._es.update(elasticsearch_name)
        self.assertEquals(set(elasticsearch_names), set([info["internal_name"] for info in self._es.list()]))
        self._es.delete("a-domain-that-doesnt-exist")
        self.assertEquals(set(elasticsearch_names), set([info["internal_name"] for info in self._es.list()]))

    def test_delete_deletes_all_config_domains(self):
        """Verify that calling delete with no arguments deletes all configured domains"""
        self._es.update()
        self.assertEquals(len(self._es.list()), 2)
        self._es.delete()
        self.assertEquals(len(self._es.list()), 0)

    def test_delete_can_delete_all_domains(self):
        """Verify that calling delete with delete_all deletes all domains in the current environment"""
        es_config = self._es._get_es_config("logs")
        elasticsearch_names = ["logs", "other-logs", "another-one"]
        for elasticsearch_name in elasticsearch_names:
            es_config["DomainName"] = self._es.get_domain_name(elasticsearch_name)
            self._es.update(elasticsearch_name, es_config)
        self.assertEquals(set(elasticsearch_names), set([info["internal_name"] for info in self._es.list()]))
        self._es.delete()
        self.assertEquals(["another-one"], [info["internal_name"] for info in self._es.list()])
        self._es.delete(delete_all=True)
        self.assertEquals([], [info["internal_name"] for info in self._es.list()])

    def test_can_create_and_then_update_domain(self):
        """Verify that a domain can be created and then updated"""
        elasticsearch_name = "logs"
        es_config = self._es._get_es_config(elasticsearch_name)
        self._es.update(elasticsearch_name, es_config)
        original_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        original_instance_type = original_config["DomainStatus"]["ElasticsearchClusterConfig"]["InstanceType"]
        desired_instance_type = "m3.xlarge.elasticsearch"
        self.assertIn(
            "ElasticsearchVersion",
            self._es._conn.create_elasticsearch_domain.call_args[1]
        )

        es_config["ElasticsearchClusterConfig"]["InstanceType"] = desired_instance_type
        self._es.update(elasticsearch_name, es_config)
        new_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        new_instance_type = new_config["DomainStatus"]["ElasticsearchClusterConfig"]["InstanceType"]
        self.assertNotEquals(original_instance_type, new_instance_type)
        self.assertEquals(new_instance_type, desired_instance_type)
        self.assertNotIn(
            "ElasticsearchVersion",
            self._es._conn.update_elasticsearch_domain_config.call_args[1]
        )

    def test_can_create_and_then_update_all_domains(self):
        """Verify that all domains can be created and then one updated"""
        elasticsearch_name = "logs"
        es_config = self._es._get_es_config(elasticsearch_name)
        self._es.update(elasticsearch_name, es_config)
        original_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        original_instance_type = original_config["DomainStatus"]["ElasticsearchClusterConfig"]["InstanceType"]
        desired_instance_type = "m3.xlarge.elasticsearch"
        es_config["ElasticsearchClusterConfig"]["InstanceType"] = desired_instance_type
        self._es.update(es_config=es_config)
        new_config = self._es._describe_es_domain(self._es.get_domain_name(elasticsearch_name))
        new_instance_type = new_config["DomainStatus"]["ElasticsearchClusterConfig"]["InstanceType"]
        self.assertNotEquals(original_instance_type, new_instance_type)
        self.assertEquals(new_instance_type, desired_instance_type)

    def test_update_nonexistant_domain(self):
        """Verify that calling update on a nonexistant domain has no effect on existing domains"""
        self._es.update("logs")
        logs_config_before_update = self._es._describe_es_domain(self._es.get_domain_name("logs"))
        self.assertEquals(len(self._es.list()), 1)
        self._es.update("some-other-logs")
        self.assertEquals(len(self._es.list()), 1)
        logs_config_after_update = self._es._describe_es_domain(self._es.get_domain_name("logs"))
        self.assertEquals(logs_config_before_update, logs_config_after_update)
