"""
Tests of disco_aws
"""
from __future__ import print_function
from unittest import TestCase

import boto.ec2.instance
from boto.exception import EC2ResponseError
from mock import MagicMock, call, patch, create_autospec
from moto import mock_elb

from disco_aws_automation import DiscoAWS
from disco_aws_automation.exceptions import TimeoutError, SmokeTestError

from test.helpers.matchers import MatchAnything
from test.helpers.patch_disco_aws import (patch_disco_aws,
                                          get_default_config_dict,
                                          get_mock_config,
                                          TEST_ENV_NAME)


def _get_meta_network_mock():
    ret = MagicMock()
    ret = MagicMock()
    ret.security_group = MagicMock()
    ret.security_group.id = "sg-1234abcd"
    ret.subnets = [MagicMock() for _ in xrange(3)]
    for subnet in ret.subnets:
        subnet.id = "s-1234abcd"
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

    def test_size_as_rec_map_with_none(self):
        """_size_as_recurrence_map works with None"""
        self.assertEqual(DiscoAWS._size_as_recurrence_map(None), {"": None})
        self.assertEqual(DiscoAWS._size_as_recurrence_map(''), {"": None})

    def test_size_as_rec_map_with_int(self):
        """_size_as_recurrence_map works with simple integer"""
        self.assertEqual(DiscoAWS._size_as_recurrence_map(5, sentinel="0 0 * * *"),
                         {"0 0 * * *": 5})

    def test_size_as_rec_map_with_map(self):
        """_size_as_recurrence_map works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}
        self.assertEqual(DiscoAWS._size_as_recurrence_map(map_as_string), map_as_dict)

    def test_size_as_rec_map_with_duped_map(self):
        """_size_as_recurrence_map works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}
        self.assertEqual(DiscoAWS._size_as_recurrence_map(map_as_string), map_as_dict)

    @patch_disco_aws
    def test_create_scaling_schedule_only_desired(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.autoscale = MagicMock()
        aws.create_scaling_schedule("mhcboo", "1", "2@1 0 * * *:3@6 0 * * *", "5")
        aws.autoscale.assert_has_calls([
            call.delete_all_recurring_group_actions('mhcboo'),
            call.create_recurring_group_action('mhcboo', '1 0 * * *',
                                               min_size=None, desired_capacity=2, max_size=None),
            call.create_recurring_group_action('mhcboo', '6 0 * * *',
                                               min_size=None, desired_capacity=3, max_size=None)
        ], any_order=True)

    @patch_disco_aws
    def test_create_scaling_schedule_no_sched(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.autoscale = MagicMock()
        aws.create_scaling_schedule("mhcboo", "1", "2", "5")
        aws.autoscale.assert_has_calls([call.delete_all_recurring_group_actions('mhcboo')])

    @patch_disco_aws
    def test_create_scaling_schedule_overlapping(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.autoscale = MagicMock()
        aws.create_scaling_schedule("mhcboo",
                                    "1@1 0 * * *:2@6 0 * * *",
                                    "2@1 0 * * *:3@6 0 * * *",
                                    "6@1 0 * * *:9@6 0 * * *")
        aws.autoscale.assert_has_calls([
            call.delete_all_recurring_group_actions('mhcboo'),
            call.create_recurring_group_action('mhcboo', '1 0 * * *',
                                               min_size=1, desired_capacity=2, max_size=6),
            call.create_recurring_group_action('mhcboo', '6 0 * * *',
                                               min_size=2, desired_capacity=3, max_size=9)
        ], any_order=True)

    @patch_disco_aws
    def test_create_scaling_schedule_mixed(self, mock_config, **kwargs):
        """test create_scaling_schedule with only desired schedule"""
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.autoscale = MagicMock()
        aws.create_scaling_schedule("mhcboo",
                                    "1@1 0 * * *:2@7 0 * * *",
                                    "2@1 0 * * *:3@6 0 * * *",
                                    "6@2 0 * * *:9@6 0 * * *")
        aws.autoscale.assert_has_calls([
            call.delete_all_recurring_group_actions('mhcboo'),
            call.create_recurring_group_action('mhcboo', '1 0 * * *',
                                               min_size=1, desired_capacity=2, max_size=None),
            call.create_recurring_group_action('mhcboo', '2 0 * * *',
                                               min_size=None, desired_capacity=None, max_size=6),
            call.create_recurring_group_action('mhcboo', '6 0 * * *',
                                               min_size=None, desired_capacity=3, max_size=9),
            call.create_recurring_group_action('mhcboo', '7 0 * * *',
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
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        mock_ami = self._get_image_mock(aws)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

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
        _lc = aws.autoscale.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.autoscale.has_group("mhcunittest"))
        _ag = aws.autoscale.get_groups()[0]
        self.assertEqual(_ag.name, "unittestenv_mhcunittest")
        self.assertEqual(_ag.min_size, 1)
        self.assertEqual(_ag.max_size, 1)
        self.assertEqual(_ag.desired_capacity, 1)

    @patch_disco_aws
    def test_provision_hc_simple_with_no_chaos(self, mock_config, **kwargs):
        """
        Provision creates the proper launch configuration and autoscaling group with no chaos
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        mock_ami = self._get_image_mock(aws)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

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
        _lc = aws.autoscale.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.autoscale.has_group("mhcunittest"))
        _ag = aws.autoscale.get_groups()[0]
        self.assertEqual(_ag.name, "unittestenv_mhcunittest")
        self.assertEqual(_ag.min_size, 1)
        self.assertEqual(_ag.max_size, 1)
        self.assertEqual(_ag.desired_capacity, 1)

    @patch_disco_aws
    def test_provision_hc_with_chaos_using_config(self, mock_config, **kwargs):
        """
        Provision creates the proper launch configuration and autoscaling group with chaos from config
        """
        config_dict = get_default_config_dict()
        config_dict["mhcunittest"]["chaos"] = "True"
        aws = DiscoAWS(config=get_mock_config(config_dict), environment_name=TEST_ENV_NAME)
        mock_ami = self._get_image_mock(aws)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

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
        _lc = aws.autoscale.get_configs()[0]
        self.assertRegexpMatches(_lc.name, r".*_mhcunittest_[0-9]*")
        self.assertEqual(_lc.image_id, mock_ami.id)
        self.assertTrue(aws.autoscale.has_group("mhcunittest"))
        _ag = aws.autoscale.get_groups()[0]
        self.assertEqual(_ag.name, "unittestenv_mhcunittest")
        self.assertEqual(_ag.min_size, 1)
        self.assertEqual(_ag.max_size, 1)
        self.assertEqual(_ag.desired_capacity, 1)

    @patch_disco_aws
    def test_provision_hostclass_schedules(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

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

        _ag = aws.autoscale.get_groups()[0]
        self.assertEqual(_ag.min_size, 1)  # minimum of listed sizes
        self.assertEqual(_ag.desired_capacity, 3)  # maximum of listed sizes
        self.assertEqual(_ag.max_size, 9)  # maximum of listed sizes

    @patch_disco_aws
    def test_provision_hostclass_sched_some_none(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="",
                                      desired_size="2@1 0 * * *:3@6 0 * * *", max_size="")

        _ag = aws.autoscale.get_groups()[0]
        print("({0}, {1}, {2})".format(_ag.min_size, _ag.desired_capacity, _ag.max_size))
        self.assertEqual(_ag.min_size, 0)  # minimum of listed sizes
        self.assertEqual(_ag.desired_capacity, 3)  # maximum of listed sizes
        self.assertEqual(_ag.max_size, 3)  # maximum of listed sizes

    @patch_disco_aws
    def test_provision_hostclass_sched_all_none(self, mock_config, **kwargs):
        """
        Provision creates the proper autoscaling group sizes with scheduled sizes
        """
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws.log_metrics = MagicMock()
        aws.update_elb = MagicMock(return_value=None)

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="", desired_size="", max_size="")

        _ag0 = aws.autoscale.get_groups()[0]

        self.assertEqual(_ag0.min_size, 0)  # minimum of listed sizes
        self.assertEqual(_ag0.desired_capacity, 0)  # maximum of listed sizes
        self.assertEqual(_ag0.max_size, 0)  # maximum of listed sizes

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="3", desired_size="6", max_size="9")

        _ag1 = aws.autoscale.get_groups()[0]

        self.assertEqual(_ag1.min_size, 3)  # minimum of listed sizes
        self.assertEqual(_ag1.desired_capacity, 6)  # maximum of listed sizes
        self.assertEqual(_ag1.max_size, 9)  # maximum of listed sizes

        with patch("disco_aws_automation.DiscoAWS.get_meta_network", return_value=_get_meta_network_mock()):
            with patch("boto.ec2.connection.EC2Connection.get_all_snapshots", return_value=[]):
                with patch("disco_aws_automation.DiscoAWS.create_scaling_schedule", return_value=None):
                    with patch("boto.ec2.autoscale.AutoScaleConnection.create_or_update_tags",
                               return_value=None):
                        aws.provision(ami=self._get_image_mock(aws),
                                      hostclass="mhcunittest", owner="unittestuser",
                                      min_size="", desired_size="", max_size="")

        _ag2 = aws.autoscale.get_groups()[0]

        self.assertEqual(_ag2.min_size, 3)  # minimum of listed sizes
        self.assertEqual(_ag2.desired_capacity, 6)  # maximum of listed sizes
        self.assertEqual(_ag2.max_size, 9)  # maximum of listed sizes

    @patch_disco_aws
    def test_update_elb_delete(self, mock_config, **kwargs):
        '''Update ELB deletes ELBs that are no longer configured'''
        aws = DiscoAWS(config=mock_config, environment_name=TEST_ENV_NAME)
        aws._elb = MagicMock()
        aws.elb.get_elb = MagicMock(return_value=True)
        aws.elb.delete_elb = MagicMock()
        aws.update_elb("mhcfoo", update_autoscaling=False)
        aws.elb.delete_elb.assert_called_once_with("mhcfoo")

    def _get_elb_config(self):
        config = get_default_config_dict()
        config["mhcelb"] = {
            "subnet": "intranet",
            "security_group": "intranet",
            "ssh_key_name": "unittestkey",
            "instance_profile_name": "unittestprofile",
            "public_ip": "False",
            "ip_address": None,
            "eip": None,
            "route": None,
            "source_dest_check": "yes",
            "domain_name": "example.com",
            "elb": "yes",
            "elb_health_check_url": "/foo",
            'product_line': 'unittest'
        }
        return get_mock_config(config)

    @mock_elb
    @patch_disco_aws
    def test_update_elb_create(self, mock_config, **kwargs):
        '''DiscoELB called to update or create ELB when one is configured'''
        aws = DiscoAWS(config=self._get_elb_config(), environment_name=TEST_ENV_NAME)
        aws.elb.get_or_create_elb = MagicMock(return_value=MagicMock())
        aws.get_meta_network_by_name = _get_meta_network_mock()
        aws.elb.delete_elb = MagicMock()

        aws.update_elb("mhcelb", update_autoscaling=False)

        aws.elb.delete_elb.assert_not_called()
        aws.elb.get_or_create_elb.assert_called_once_with(
            'mhcelb', elb_port=80, health_check_url='/foo',
            hosted_zone_name='example.com', instance_port=80,
            elb_protocol='HTTP', instance_protocol='HTTP',
            security_groups=['sg-1234abcd'], elb_public=False,
            sticky_app_cookie=None, subnets=['s-1234abcd', 's-1234abcd', 's-1234abcd'],
            connection_draining_timeout=300, idle_timeout=300,
            tags={'owner': MatchAnything(),
                  'environment': 'unittestenv',
                  'productline': 'unittest',
                  'hostclass': 'mhcelb'}
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

        user_data = aws.create_userdata(hostclass="mhcunittest", owner="unittestuser", testing=False)
        self.assertEqual(user_data["eip"], eip)

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
