"""
Tests of disco_aws
"""
from __future__ import print_function

import unittest
from unittest import TestCase

from datetime import datetime
from datetime import timedelta

import boto.ec2.instance
from boto.exception import EC2ResponseError
from mock import MagicMock, call, patch, create_autospec
from moto import mock_elb

from disco_aws_automation import DiscoAWS
from disco_aws_automation.exceptions import TimeoutError, SmokeTestError
from disco_aws_automation.disco_elb import DiscoELBPortConfig, DiscoELBPortMapping

from tests.helpers.patch_disco_aws import (patch_disco_aws,
                                           get_default_config_dict,
                                           get_mock_config,
                                           TEST_ENV_NAME)


def _get_meta_network_mock():
    ret = MagicMock()
    ret.security_group = MagicMock()
    ret.security_group.id = "sg-1234abcd"
    ret.disco_subnets = {}
    for _ in xrange(3):
        zone_name = 'zone{0}'.format(_)
        ret.disco_subnets[zone_name] = MagicMock()
        ret.disco_subnets[zone_name].subnet_dict = dict()
        ret.disco_subnets[zone_name].subnet_dict['SubnetId'] = "s-1234abcd"
    return MagicMock(return_value=ret)


# Not every test will use the mocks in **kwargs, so disable the unused argument warning
# pylint: disable=W0613
class DiscoAWSTests(TestCase):
    '''Test DiscoAWS class'''

    def setUp(self):
        self.instance = create_autospec(boto.ec2.instance.Instance)
        self.instance.state = "running"
        self.instance.tags = create_autospec(boto.ec2.tag.TagSet)
        self.instance.id = "i-12345678"

    @patch_disco_aws
    def test_create_scaling_schedule_only_desired(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, discogroup=MagicMock())
        aws.create_scaling_schedule("1", "2@1 0 * * *:3@6 0 * * *", "5", hostclass="mhcboo")
        aws.discogroup.assert_has_calls([
            call.delete_all_recurring_group_actions(hostclass='mhcboo', group_name=None),
            call.create_recurring_group_action('1 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=None, desired_capacity=2, max_size=None),
            call.create_recurring_group_action('6 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=None, desired_capacity=3, max_size=None)
        ], any_order=True)

    @patch_disco_aws
    def test_create_scaling_schedule_no_sched(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, discogroup=MagicMock())
        aws.create_scaling_schedule("1", "2", "5", hostclass="mhcboo")
        aws.discogroup.assert_has_calls([
            call.delete_all_recurring_group_actions(hostclass='mhcboo', group_name=None)
        ])

    @patch_disco_aws
    def test_create_scaling_schedule_overlapping(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, discogroup=MagicMock())
        aws.create_scaling_schedule(
            "1@1 0 * * *:2@6 0 * * *",
            "2@1 0 * * *:3@6 0 * * *",
            "6@1 0 * * *:9@6 0 * * *",
            hostclass="mhcboo"
        )
        aws.discogroup.assert_has_calls([
            call.delete_all_recurring_group_actions(hostclass='mhcboo', group_name=None),
            call.create_recurring_group_action('1 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=1, desired_capacity=2, max_size=6),
            call.create_recurring_group_action('6 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=2, desired_capacity=3, max_size=9)
        ], any_order=True)

    @patch_disco_aws
    def test_create_scaling_schedule_mixed(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, discogroup=MagicMock())
        aws.create_scaling_schedule(
            "1@1 0 * * *:2@7 0 * * *",
            "2@1 0 * * *:3@6 0 * * *",
            "6@2 0 * * *:9@6 0 * * *",
            hostclass="mhcboo"
        )
        aws.discogroup.assert_has_calls([
            call.delete_all_recurring_group_actions(hostclass='mhcboo', group_name=None),
            call.create_recurring_group_action('1 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=1, desired_capacity=2, max_size=None),
            call.create_recurring_group_action('2 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=None, desired_capacity=None, max_size=6),
            call.create_recurring_group_action('6 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=None, desired_capacity=3, max_size=9),
            call.create_recurring_group_action('7 0 * * *', hostclass='mhcboo', group_name=None,
                                               min_size=2, desired_capacity=None, max_size=None)
        ], any_order=True)

    def _get_image_mock(self, aws):
        reservation = aws.connection.run_instances('ami-1234abcd')
        instance = reservation.instances[0]
        mock_ami = MagicMock()
        mock_ami.id = aws.connection.create_image(instance.id, "test-ami", "this is a test ami")
        return mock_ami

    @patch_disco_aws
    def test_provision_hostclass_simple(self, mock_config, **kwargs):
        """
        Provision creates the proper launch configuration and autoscaling group
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, log_metrics=MagicMock())
        mock_ami = self._get_image_mock(aws)
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        metadata = aws.provision(ami=mock_ami, hostclass="mhcunittest",
                                                 owner="unittestuser",
                                                 min_size=1, desired_size=1, max_size=1)

        self.assertEqual(metadata["hostclass"], "mhcunittest")
        self.assertFalse(metadata["no_destroy"])
        self.assertTrue(metadata["chaos"])
        _lc = aws.discogroup.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.discogroup.get_existing_group(hostclass="mhcunittest"))
        _ag = aws.discogroup.get_existing_groups()[0]
        self.assertRegexpMatches(_ag['name'], r"unittestenv_mhcunittest_[0-9]*")
        self.assertEqual(_ag['min_size'], 1)
        self.assertEqual(_ag['max_size'], 1)
        self.assertEqual(_ag['desired_capacity'], 1)

    @patch_disco_aws
    def test_provision_hc_simple_with_no_chaos(self, mock_config, **kwargs):
        """
        Provision creates the proper launch configuration and autoscaling group with no chaos
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, log_metrics=MagicMock())
        mock_ami = self._get_image_mock(aws)
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        metadata = aws.provision(ami=mock_ami, hostclass="mhcunittest",
                                                 owner="unittestuser",
                                                 min_size=1, desired_size=1, max_size=1,
                                                 chaos="False")

        self.assertEqual(metadata["hostclass"], "mhcunittest")
        self.assertFalse(metadata["no_destroy"])
        self.assertFalse(metadata["chaos"])
        _lc = aws.discogroup.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.discogroup.get_existing_group(hostclass="mhcunittest"))
        _ag = aws.discogroup.get_existing_groups()[0]
        self.assertRegexpMatches(_ag['name'], r"unittestenv_mhcunittest_[0-9]*")
        self.assertEqual(_ag['min_size'], 1)
        self.assertEqual(_ag['max_size'], 1)
        self.assertEqual(_ag['desired_capacity'], 1)

    @patch_disco_aws
    def test_provision_hc_with_chaos_using_config(self, mock_config, **kwargs):
        """
        Provision creates the proper launch configuration and autoscaling group with chaos from config
        """
        config_dict = get_default_config_dict()
        config_dict["mhcunittest"]["chaos"] = "True"
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME,
                       log_metrics=MagicMock())
        mock_ami = self._get_image_mock(aws)
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        metadata = aws.provision(ami=mock_ami, hostclass="mhcunittest",
                                                 owner="unittestuser",
                                                 min_size=1, desired_size=1, max_size=1)

        self.assertEqual(metadata["hostclass"], "mhcunittest")
        self.assertFalse(metadata["no_destroy"])
        self.assertTrue(metadata["chaos"])
        _lc = aws.discogroup.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.discogroup.get_existing_group(hostclass="mhcunittest"))
        _ag = aws.discogroup.get_existing_groups()[0]
        self.assertRegexpMatches(_ag['name'], r"unittestenv_mhcunittest_[0-9]*")
        self.assertEqual(_ag['min_size'], 1)
        self.assertEqual(_ag['max_size'], 1)
        self.assertEqual(_ag['desired_capacity'], 1)

    @patch_disco_aws
    def test_provision_hostclass_schedules(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, log_metrics=MagicMock())
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="1@1 0 * * *:2@6 0 * * *",
                                      desired_size="2@1 0 * * *:3@6 0 * * *",
                                      max_size="6@1 0 * * *:9@6 0 * * *")

        _ag = aws.discogroup.get_existing_groups()[0]
        self.assertEqual(_ag['min_size'], 1)  # minimum of listed sizes
        self.assertEqual(_ag['desired_capacity'], 3)  # maximum of listed sizes
        self.assertEqual(_ag['max_size'], 9)  # maximum of listed sizes

    @patch_disco_aws
    def test_provision_hostclass_sched_some_none(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, log_metrics=MagicMock())
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="",
                                      desired_size="2@1 0 * * *:3@6 0 * * *", max_size="")

        _ag = aws.discogroup.get_existing_groups()[0]
        print("({0}, {1}, {2})".format(_ag['min_size'], _ag['desired_capacity'], _ag['max_size']))
        self.assertEqual(_ag['min_size'], 0)  # minimum of listed sizes
        self.assertEqual(_ag['desired_capacity'], 3)  # maximum of listed sizes
        self.assertEqual(_ag['max_size'], 3)  # maximum of listed sizes

    @patch_disco_aws
    @unittest.skip("temporarily disabled until we find out why this is failing :(")
    def test_provision_hostclass_sched_all_none(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, log_metrics=MagicMock())
        aws.update_elb = MagicMock(return_value=None)
        aws.discogroup.elastigroup.spotinst_client = MagicMock()

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="", desired_size="", max_size="")

        _ag0 = aws.discogroup.get_existing_groups()[0]

        self.assertEqual(_ag0['min_size'], 0)  # minimum of listed sizes
        self.assertEqual(_ag0['desired_capacity'], 0)  # maximum of listed sizes
        self.assertEqual(_ag0['max_size'], 0)  # maximum of listed sizes

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="3", desired_size="6", max_size="9")

        _ag1 = aws.discogroup.get_existing_groups()[0]

        self.assertEqual(_ag1['min_size'], 3)  # minimum of listed sizes
        self.assertEqual(_ag1['desired_capacity'], 6)  # maximum of listed sizes
        self.assertEqual(_ag1['max_size'], 9)  # maximum of listed sizes

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="", desired_size="", max_size="")

        _ag2 = aws.discogroup.get_existing_groups()[0]

        self.assertEqual(_ag2['min_size'], 3)  # minimum of listed sizes
        self.assertEqual(_ag2['desired_capacity'], 6)  # maximum of listed sizes
        self.assertEqual(_ag2['max_size'], 9)  # maximum of listed sizes

    @patch_disco_aws
    def test_update_elb_delete(self, mock_config, **kwargs):
        '''Update ELB deletes ELBs that are no longer configured'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME, elb=MagicMock())
        aws.elb.get_elb = MagicMock(return_value=True)
        aws.elb.delete_elb = MagicMock()
        aws.update_elb("mhcfoo", update_autoscaling=False)
        aws.elb.delete_elb.assert_called_once_with("mhcfoo")

    def _get_elb_config(self, overrides=None):
        overrides = overrides or {}
        config = get_default_config_dict()
        config["mhcelb"] = {
            "subnet": "intranet",
            "security_group": "intranet",
            "ssh_key_name": "unittestkey",
            "instance_profile_name": "unittestprofile",
            "public_ip": "False",
            "ip_address": None,
            "eip": None,
            "domain_name": "example.com",
            "elb": "yes",
            "elb_health_check_url": "/foo",
            "product_line": "mock_productline"
        }
        config["mhcelb"].update(overrides)

        return get_mock_config(config)

    @mock_elb
    @patch_disco_aws
    def test_update_elb_all_defaults(self, mock_config, **kwargs):
        """
        update_elb calls get_or_create_elb with default port and protocol values if all are missing
        """
        aws = DiscoAWS(config=self._get_elb_config(), environment_name=TEST_ENV_NAME, elb=MagicMock())
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 80, 'HTTP'),
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_some_defaults(self, mock_config, **kwargs):
        """
        update_elb calls get_or_create_elb with default port and protocol values if some are missing
        """
        overrides = {
            'elb_instance_port': '80, 80',
            'elb_instance_protocol': 'HTTP',
            'elb_port': '443',
            'elb_protocol': 'HTTPS, HTTPS'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS')
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_no_defaults(self, mock_config, **kwargs):
        """
        update_elb calls get_or_create_elb with port and protocol values
        """
        overrides = {
            'elb_instance_port': '80, 80, 27017',
            'elb_instance_protocol': 'HTTP, HTTP, TCP',
            'elb_port': '443, 443, 27017',
            'elb_protocol': 'HTTPS, HTTPS, TCP'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                    DiscoELBPortMapping(27017, 'TCP', 27017, 'TCP')
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_single(self, mock_config, **kwargs):
        """
        update_elb calls get_or_create_elb with port and protocol values for a single port and protocol
        """
        overrides = {
            'elb_instance_port': '80',
            'elb_instance_protocol': 'HTTP',
            'elb_port': '443',
            'elb_protocol': 'HTTPS'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_lowercase(self, mock_config, **kwargs):
        """
        update_elb accepts lowercase protocols
        """
        overrides = {
            'elb_instance_port': '80',
            'elb_instance_protocol': 'http',
            'elb_port': '443',
            'elb_protocol': 'https'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_mismatch(self, mock_config, **kwargs):
        """
        update_elb sets instance=ELB when given mismatched numbers of instance and ELB ports
        """
        overrides = {
            'elb_instance_port': '80, 9001',
            'elb_instance_protocol': 'HTTP, HTTP',
            'elb_port': '443, 80, 9002',
            'elb_protocol': 'HTTPS, HTTP, HTTP'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()
        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                    DiscoELBPortMapping(9001, 'HTTP', 80, 'HTTP'),
                    DiscoELBPortMapping(9002, 'HTTP', 9002, 'HTTP')
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_mismatch_no_external(self, mock_config, **kwargs):
        """
        update_elb sets instance=ELB when given a single instance port/protocol and no ELB port/protocol
        """
        overrides = {
            'elb_instance_port': '80',
            'elb_instance_protocol': 'HTTP',
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()
        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 80, 'HTTP'),
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @mock_elb
    @patch_disco_aws
    def test_update_elb_replicate(self, mock_config, **kwargs):
        """
        update_elb replicates the instance configuration when given a single instance port and protocol
        """
        overrides = {
            'elb_instance_port': '80',
            'elb_instance_protocol': 'HTTP',
            'elb_port': '443, 9001',
            'elb_protocol': 'HTTPS, HTTP'
        }
        aws = DiscoAWS(
            config=self._get_elb_config(overrides),
            environment_name=TEST_ENV_NAME,
            elb=MagicMock()
        )
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()
        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb',
            health_check_url='/foo',
            hosted_zone_name='example.com',
            port_config=DiscoELBPortConfig(
                [
                    DiscoELBPortMapping(80, 'HTTP', 443, 'HTTPS'),
                    DiscoELBPortMapping(80, 'HTTP', 9001, 'HTTP')
                ]
            ),
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            elb_dns_alias=None,
            connection_draining_timeout=300, idle_timeout=300, testing=False,
            tags={
                'environment': 'unittestenv',
                'hostclass': 'mhcelb',
                'is_testing': '0',
                'productline': 'mock_productline'
            },
            cross_zone_load_balancing=True,
            cert_name=None
        )

    @patch_disco_aws
    def test_create_userdata_with_eip(self, **kwargs):
        """
        create_userdata sets 'eip' key when an EIP is required
        """
        config_dict = get_default_config_dict()
        eip = "54.201.250.76"
        config_dict["mhcunittest"]["eip"] = eip
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME)

        user_data = aws.create_userdata(hostclass="mhcunittest", owner="unittestuser")
        self.assertEqual(user_data["eip"], eip)

    @patch_disco_aws
    def test_create_userdata_with_zookeeper(self, **kwargs):
        """
        create_userdata sets 'zookeepers' key
        """
        config_dict = get_default_config_dict()
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME)

        user_data = aws.create_userdata(hostclass="mhcunittest", owner="unittestuser")
        self.assertEqual(user_data["zookeepers"], "[\\\"mhczookeeper-{}.example.com:2181\\\"]".format(
            aws.vpc.environment_name))

    @patch_disco_aws
    def test_create_userdata_with_spotinst(self, **kwargs):
        """
        create_userdata sets 'spotinst' key
        """
        config_dict = get_default_config_dict()
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME)

        user_data = aws.create_userdata(hostclass="mhcunittest", owner="unittestuser", is_spotinst=True)
        self.assertEqual(user_data["is_spotinst"], "1")

    @patch_disco_aws
    def test_create_userdata_without_spotinst(self, **kwargs):
        """
        create_userdata doesn't set 'spotinst' key
        """
        config_dict = get_default_config_dict()
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME)

        user_data = aws.create_userdata(hostclass="mhcunittest", owner="unittestuser", is_spotinst=False)
        self.assertEqual(user_data["is_spotinst"], "0")

    @patch_disco_aws
    def test_smoketest_all_good(self, mock_config, **kwargs):
        '''smoketest_once raises TimeoutError if instance is not tagged as smoketested'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        self.instance.tags.get = MagicMock(return_value="100")
        self.assertTrue(aws.smoketest_once(self.instance))

    @patch_disco_aws
    def test_smoketest_once_is_terminated(self, mock_config, **kwargs):
        '''smoketest_once raises SmokeTestError if instance has terminated'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        with patch("disco_aws_automation.DiscoAWS.is_terminal_state", return_value=True):
            self.assertRaises(SmokeTestError, aws.smoketest_once, self.instance)

    @patch_disco_aws
    def test_smoketest_once_no_instance(self, mock_config, **kwargs):
        '''smoketest_once Converts instance not found to TimeoutError'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        self.instance.update = MagicMock(side_effect=EC2ResponseError(
            400, "Bad Request",
            body={
                "RequestID": "df218052-63f2-4a11-820f-542d97d078bd",
                "Error": {"Code": "InvalidInstanceID.NotFound", "Message": "test"}}))
        self.assertRaises(TimeoutError, aws.smoketest_once, self.instance)

    @patch_disco_aws
    def test_smoketest_once_passes_exception(self, mock_config, **kwargs):
        '''smoketest_once passes random EC2ResponseErrors'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        self.instance.update = MagicMock(side_effect=EC2ResponseError(
            400, "Bad Request",
            body={
                "RequestID": "df218052-63f2-4a11-820f-542d97d078bd",
                "Error": {"Code": "Throttled", "Message": "test"}}))
        self.assertRaises(EC2ResponseError, aws.smoketest_once, self.instance)

    @patch_disco_aws
    def test_smoketest_not_tagged(self, mock_config, **kwargs):
        '''smoketest_once raises TimeoutError if instance is not tagged as smoketested'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        self.instance.tags.get = MagicMock(return_value=None)
        self.assertRaises(TimeoutError, aws.smoketest_once, self.instance)

    @patch_disco_aws
    def test_is_terminal_state_updates(self, mock_config, **kwargs):
        '''is_terminal_state calls instance update'''
        DiscoAWS.is_terminal_state(self.instance)
        self.assertEqual(self.instance.update.call_count, 1)

    @patch_disco_aws
    def test_is_terminal_state_termianted(self, mock_config, **kwargs):
        '''is_terminal_state returns true if instance has terminated or failed to start'''
        self.instance.state = "terminated"
        self.assertTrue(DiscoAWS.is_terminal_state(self.instance))
        self.instance.state = "failed"
        self.assertTrue(DiscoAWS.is_terminal_state(self.instance))

    @patch_disco_aws
    def test_is_terminal_state_running(self, mock_config, **kwargs):
        '''is_terminal_state returns false for running instance'''
        self.assertFalse(DiscoAWS.is_terminal_state(self.instance))

    @patch_disco_aws
    def test_is_running_updates(self, mock_config, **kwargs):
        '''is_running calls instance update'''
        DiscoAWS.is_running(self.instance)
        self.assertEqual(self.instance.update.call_count, 1)

    @patch_disco_aws
    def test_is_running_termianted(self, mock_config, **kwargs):
        '''is_running returns false if instance has terminated'''
        self.instance.state = "terminated"
        self.assertFalse(DiscoAWS.is_running(self.instance))

    @patch_disco_aws
    def test_is_running_running(self, mock_config, **kwargs):
        '''is_running returns true for running instance'''
        self.assertTrue(DiscoAWS.is_running(self.instance))

    @patch_disco_aws
    def test_instances_from_amis(self, mock_config, **kwargs):
        '''test get instances using ami ids '''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        instance = create_autospec(boto.ec2.instance.Instance)
        instance.id = "i-123123aa"
        instances = [instance]
        aws.instances = MagicMock(return_value=instances)
        self.assertEqual(aws.instances_from_amis('ami-12345678'), instances)
        aws.instances.assert_called_with(filters={"image_id": 'ami-12345678'}, instance_ids=None)

    @patch_disco_aws
    def test_instances_from_amis_with_group_name(self, mock_config, **kwargs):
        '''test get instances using ami ids in a specified group name'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        instance = create_autospec(boto.ec2.instance.Instance)
        instance.id = "i-123123aa"
        instances = [instance]
        aws.instances_from_asgs = MagicMock(return_value=instances)
        aws.instances = MagicMock(return_value=instances)
        self.assertEqual(aws.instances_from_amis('ami-12345678', group_name='test_group'), instances)
        aws.instances_from_asgs.assert_called_with(['test_group'])

    @patch_disco_aws
    def test_instances_from_amis_with_launch_date(self, mock_config, **kwargs):
        '''test get instances using ami ids and with date after a specified date time'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        now = datetime.utcnow()

        instance1 = create_autospec(boto.ec2.instance.Instance)
        instance1.id = "i-123123aa"
        instance1.launch_time = str(now + timedelta(minutes=10))
        instance2 = create_autospec(boto.ec2.instance.Instance)
        instance2.id = "i-123123ff"
        instance2.launch_time = str(now - timedelta(days=1))
        instances = [instance1, instance2]

        aws.instances = MagicMock(return_value=instances)
        self.assertEqual(aws.instances_from_amis('ami-12345678', launch_time=now),
                         [instance1])
        aws.instances.assert_called_with(filters={"image_id": 'ami-12345678'}, instance_ids=None)

    @patch_disco_aws
    def test_wait_for_autoscaling_using_amiid(self, mock_config, **kwargs):
        '''test wait for autoscaling using the ami id to identify the instances'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        instances = [{"InstanceId": "i-123123aa"}]
        aws.instances_from_amis = MagicMock(return_value=instances)
        aws.wait_for_autoscaling('ami-12345678', 1)
        aws.instances_from_amis.assert_called_with(['ami-12345678'], group_name=None, launch_time=None)

    @patch_disco_aws
    def test_wait_for_autoscaling_using_gp_name(self, mock_config, **kwargs):
        '''test wait for autoscaling using the group name to identify the instances'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        instances = [{"InstanceId": "i-123123aa"}]
        aws.instances_from_amis = MagicMock(return_value=instances)
        aws.wait_for_autoscaling('ami-12345678', 1, group_name='test_group')
        aws.instances_from_amis.assert_called_with(['ami-12345678'], group_name='test_group',
                                                   launch_time=None)

    @patch_disco_aws
    def test_wait_for_autoscaling_using_time(self, mock_config, **kwargs):
        '''test wait for autoscaling using the ami id to identify the instances and the launch time'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        instances = [{"InstanceId": "i-123123aa"}]
        yesterday = datetime.utcnow() - timedelta(days=1)
        aws.instances_from_amis = MagicMock(return_value=instances)
        aws.wait_for_autoscaling('ami-12345678', 1, launch_time=yesterday)
        aws.instances_from_amis.assert_called_with(['ami-12345678'], group_name=None,
                                                   launch_time=yesterday)
