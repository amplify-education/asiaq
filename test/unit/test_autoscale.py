"""
Tests of disco_autoscale
"""
import random
from unittest import TestCase

from mock import MagicMock, patch, ANY, call
import boto.ec2.autoscale

from disco_aws_automation import DiscoAutoscale


class DiscoAutoscaleTests(TestCase):
    '''Test DiscoAutoscale class'''

    def setUp(self):
        """Pre-test setup"""
        self._mock_connection = MagicMock()
        self._mock_boto3_connection = MagicMock()
        self.environment_name = "us-moon-1"
        self._autoscale = DiscoAutoscale("us-moon-1", self._mock_connection, self._mock_boto3_connection)

    def mock_group(self, hostclass, name=None, launch_config_name=None):
        '''Creates a mock autoscaling group for hostclass'''
        group_mock = MagicMock()
        group_mock.name = name or self._autoscale.get_new_groupname(hostclass)
        group_mock.min_size = 1
        group_mock.max_size = 1
        group_mock.desired_capacity = 1
        if launch_config_name is not None:
            group_mock.launch_config_name = launch_config_name
        else:
            group_mock.launch_config_name = self.mock_lg(hostclass, name=name).name

        return group_mock

    def mock_inst(self, hostclass, group_name=None):
        '''Creates a mock autoscaling inst for hostclass'''
        inst_mock = MagicMock()
        inst_mock.instance_id = 'i-' + ''.join(random.choice('1234567890') for x in range(8))
        inst_mock.group_name = group_name or self._autoscale.get_new_groupname(hostclass)
        return inst_mock

    def mock_lg(self, hostclass, name=None):
        '''Creates a mock autoscaling launch configuration for hostclass'''
        lg_mock = MagicMock()
        lg_mock.name = name or '{0}_{1}_{2}'.format(self.environment_name, hostclass,
                                                    str(random.randrange(0, 9999999)))
        return lg_mock

    def test_get_group_scale_down(self):
        """Test scaling down to 0 hosts"""
        self._autoscale._get_group_generator = MagicMock(return_value=[self.mock_group("mhcdummy")])
        group = self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=0, max_size=1, desired_size=0)
        self.assertEqual(group.min_size, 0)
        self.assertEqual(group.desired_capacity, 0)

    def test_get_group_no_scale(self):
        """Test getting a group and not scaling it"""
        self._autoscale._get_group_generator = MagicMock(return_value=[self.mock_group("mhcdummy")])
        group = self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=None, max_size=None, desired_size=None)
        self.assertEqual(group.min_size, 1)
        self.assertEqual(group.max_size, 1)
        self.assertEqual(group.desired_capacity, 1)

    def test_get_group_scale_up(self):
        """Test getting a group and scaling it up"""
        self._autoscale._get_group_generator = MagicMock(return_value=[self.mock_group("mhcdummy")])
        group = self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=None, max_size=5, desired_size=4)
        self.assertEqual(group.min_size, 1)
        self.assertEqual(group.max_size, 5)
        self.assertEqual(group.desired_capacity, 4)

    def test_get_group_attach_elb(self):
        """Test getting a group and attaching an elb"""
        self._autoscale._get_group_generator = MagicMock(return_value=[self.mock_group("mhcdummy")])

        group = self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            load_balancers=['fake_elb'])

        self._mock_boto3_connection.attach_load_balancers.assert_called_with(
            AutoScalingGroupName=group.name,
            LoadBalancerNames=['fake_elb'])

    @patch("boto.ec2.autoscale.group.AutoScalingGroup")
    def test_get_fresh_group_with_none_min(self, mock_group_init):
        '''Test getting a fresh group with None as min_size'''
        self._autoscale._get_group_generator = MagicMock(return_value=[])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=None, max_size=5, desired_size=4)
        mock_group_init.assert_called_with(
            min_size=0, max_size=5, desired_capacity=4,
            connection=ANY, name=ANY, launch_config=ANY,
            load_balancers=ANY, default_cooldown=ANY,
            health_check_type=ANY, health_check_period=ANY,
            placement_group=ANY, vpc_zone_identifier=ANY,
            tags=ANY, termination_policies=ANY,
            instance_id=ANY)

    @patch("boto.ec2.autoscale.group.AutoScalingGroup")
    def test_get_fresh_group_with_none_max(self, mock_group_init):
        '''Test getting a fresh group with None as max_size'''
        self._autoscale._get_group_generator = MagicMock(return_value=[])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=1, max_size=None, desired_size=4)
        mock_group_init.assert_called_with(
            min_size=1, max_size=4, desired_capacity=4,
            connection=ANY, name=ANY, launch_config=ANY,
            load_balancers=ANY, default_cooldown=ANY,
            health_check_type=ANY, health_check_period=ANY,
            placement_group=ANY, vpc_zone_identifier=ANY,
            tags=ANY, termination_policies=ANY,
            instance_id=ANY)

    @patch("boto.ec2.autoscale.group.AutoScalingGroup")
    def test_get_fresh_group_with_none_desired(self, mock_group_init):
        '''Test getting a fresh group with None as max_size'''
        self._autoscale._get_group_generator = MagicMock(return_value=[])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=1, max_size=5, desired_size=None)
        mock_group_init.assert_called_with(
            min_size=1, max_size=5, desired_capacity=5,
            connection=ANY, name=ANY, launch_config=ANY,
            load_balancers=ANY, default_cooldown=ANY,
            health_check_type=ANY, health_check_period=ANY,
            placement_group=ANY, vpc_zone_identifier=ANY,
            tags=ANY, termination_policies=ANY,
            instance_id=ANY)

    @staticmethod
    def mock_launchconfig(env, hostclass, lc_num=1):
        '''Create a dummy LaunchConfiguration'''
        launchconfig = boto.ec2.autoscale.LaunchConfiguration()
        launchconfig.name = '{0}_{1}_{2}'.format(env, hostclass, lc_num)
        launchconfig.block_device_mappings = {
            "/dev/root": MagicMock(),
            "/dev/snap": MagicMock(),
            "/dev/ephemeral": MagicMock()
        }
        for _name, bdm in launchconfig.block_device_mappings.iteritems():
            bdm.snapshot_id = None
        launchconfig.block_device_mappings["/dev/snap"].snapshot_id = "snap-12345678"
        return launchconfig

    def test_get_snapshot_dev(self):
        """_get_snapshot_dev returns the one device with a snapshot attached"""
        mock_lc = self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo")
        self.assertEqual(DiscoAutoscale._get_snapshot_dev(mock_lc, "mhcfoo"), "/dev/snap")

    def test_update_snapshot_using_latest(self):
        """Calling update_snapshot when already running latest snapshot does nothing"""
        self._autoscale.get_launch_config = MagicMock(
            return_value=self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo"))
        self._autoscale.update_group = MagicMock()
        self._autoscale.update_snapshot("snap-12345678", 99, hostclass="mhcfoo")
        self.assertEqual(self._autoscale.update_group.call_count, 0)

    def test_update_snapshot_with_update(self):
        """Calling update_snapshot when not running latest snapshot calls update_group with new config"""
        mock_lc = self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo", 1)
        self._autoscale.get_launch_config = MagicMock(return_value=mock_lc)
        self._autoscale.update_group = MagicMock()
        self._autoscale.get_existing_group = MagicMock(return_value="group")
        self._autoscale.update_snapshot("snap-NEW", 99, hostclass="mhcfoo")
        self.assertNotEqual(self._autoscale.update_group.mock_calls, [call("group", mock_lc.name)])
        self.assertEqual(mock_lc.block_device_mappings["/dev/snap"].snapshot_id, "snap-NEW")
        self.assertEqual(self._autoscale.update_group.call_count, 1)

    def test_update_elb_with_new_lb(self):
        '''update_elb will add new lb and remove old when there is no overlap in sets'''
        grp = self.mock_group("mhcfoo")
        grp.load_balancers = ["old_lb1", "old_lb2"]
        self._autoscale.get_existing_group = MagicMock(return_value=grp)
        ret = self._autoscale.update_elb(["new_lb"], hostclass="mhcfoo")
        self.assertEqual(ret, (set(["new_lb"]), set(["old_lb1", "old_lb2"])))

    def test_update_elb_with_new_lb_and_old_lb(self):
        '''update_elb will not churn an lb that is in both the existing config and new config'''
        grp = self.mock_group("mhcfoo")
        grp.load_balancers = ["old_lb", "both_lb"]
        self._autoscale.get_existing_group = MagicMock(return_value=grp)
        ret = self._autoscale.update_elb(["new_lb", "both_lb"], hostclass="mhcfoo")
        self.assertEqual(ret, (set(["new_lb"]), set(["old_lb"])))

    def test_update_elb_without_new_lb(self):
        '''update_elb will remove all load balancers when none are configured'''
        grp = self.mock_group("mhcfoo")
        grp.load_balancers = ["old_lb1", "old_lb2"]
        self._autoscale.get_existing_group = MagicMock(return_value=grp)
        ret = self._autoscale.update_elb([], hostclass="mhcfoo")
        self.assertEqual(ret, (set([]), set(["old_lb1", "old_lb2"])))

    def test_gg_filters_env_correctly(self):
        '''group_generator correctly filters based on the environment'''
        good_groups = [self.mock_group("mhcfoo"), self.mock_group("mhcbar"), self.mock_group("mhcfoobar")]
        bad_groups = [self.mock_group("mhcnoncomformist", name="foo-mhcnoncomformist-123141231123")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_groups + bad_groups
        self._mock_connection.get_all_groups.return_value = groups

        self.assertEqual(set(self._autoscale.get_existing_groups()), set(good_groups))

    def test_gg_filters_hostclass_correctly(self):
        '''get_existing_groups correctly filters based on the hostclass'''
        good_groups = [self.mock_group("mhcneedle")]
        bad_groups = [self.mock_group("mhcfoo"), self.mock_group("mhcbar"), self.mock_group("mhcfoobar")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_groups + bad_groups
        self._mock_connection.get_all_groups.return_value = groups

        self.assertEqual(set(self._autoscale.get_existing_groups(hostclass="mhcneedle")), set(good_groups))

    def test_ig_filters_env_correctly(self):
        '''inst_generator correctly filters based on the environment'''
        good_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        bad_insts = [self.mock_inst("mhcnoncomformist", group_name="foo_mhcnoncomformist_123141231123")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_insts + bad_insts
        self._mock_connection.get_all_autoscaling_instances.return_value = groups

        self.assertEqual(self._autoscale.get_instances(), good_insts)

    def test_ig_filters_hostclass_correctly(self):
        '''inst_generator correctly filters based on the hostclass'''
        good_insts = [self.mock_inst("mhcneedle")]
        bad_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_insts + bad_insts
        self._mock_connection.get_all_autoscaling_instances.return_value = groups

        self.assertEqual(self._autoscale.get_instances(hostclass="mhcneedle"), good_insts)

    def test_ig_filters_groupname_correctly(self):
        '''inst_generator correctly filters based on the group name'''
        good_insts = [self.mock_inst("mhcneedle")]
        bad_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_insts + bad_insts
        self._mock_connection.get_all_autoscaling_instances.return_value = groups

        self.assertEqual(self._autoscale.get_instances(group_name=good_insts[0].group_name),
                         good_insts)

    def test_cg_filters_env_correctly(self):
        '''config_generator correctly filters based on the environment'''
        good_lgs = [self.mock_lg("mhcfoo"), self.mock_lg("mhcbar"), self.mock_lg("mhcfoobar")]
        bad_lgs = [self.mock_lg("mhcnoncomformist", name="foo_mhcnoncomformist_123141231123")]
        groups = MagicMock()
        groups.next_token = None
        groups.__iter__.return_value = good_lgs + bad_lgs
        self._mock_connection.get_all_launch_configurations.return_value = groups

        self.assertEqual(self._autoscale.get_configs(), good_lgs)

    def test_get_launch_configs_filter(self):
        '''get_launch_configs correctly filters out empty launch config names'''
        mock_groups = [
            self.mock_group("mhcfoo"),
            self.mock_group("mhcbar"),
            self.mock_group("mhcfoo", launch_config_name="")
        ]

        self._autoscale.get_existing_groups = MagicMock(return_value=mock_groups)
        self._autoscale.get_configs = MagicMock()

        self._autoscale.get_launch_configs()

        self._autoscale.get_configs.assert_called_once_with(
            names=[
                mock_groups[0].launch_config_name,
                mock_groups[1].launch_config_name
            ]
        )
