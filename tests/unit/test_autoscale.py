"""
Tests of disco_autoscale
"""
import random
from unittest import TestCase

from mock import MagicMock, patch, ANY, call

from disco_aws_automation import DiscoAutoscale


class DiscoAutoscaleTests(TestCase):
    """Test DiscoAutoscale class"""

    def setUp(self):
        """Pre-test setup"""
        self._mock_boto3_connection = MagicMock()
        self.environment_name = "us-moon-1"
        self._autoscale = DiscoAutoscale("us-moon-1", self._mock_boto3_connection)

    def mock_group_dictionary(self, hostclass, name=None, launch_config_name=None):
        """Creates a mock autoscaling group for hostclass"""
        group_mock = dict()
        group_mock['AutoScalingGroupName'] = name or self._autoscale.get_new_groupname(hostclass)
        group_mock['MinSize'] = 1
        group_mock['MaxSize'] = 1
        group_mock['DesiredCapacity'] = 1
        group_mock['LoadBalancerNames'] = []
        group_mock['TargetGroupARNs'] = []
        group_mock['Tags'] = [{"Key": "Fake", "Value": "Fake"}]
        if launch_config_name is not None:
            group_mock['LaunchConfigurationName'] = launch_config_name
        else:
            mock_lg = self.mock_lg(hostclass, name=name)
            group_mock['LaunchConfigurationName'] = mock_lg['LaunchConfigurationName']

        return group_mock

    def mock_inst(self, hostclass, group_name=None):
        """Creates a mock autoscaling inst for hostclass"""
        return {
            'InstanceId': 'i-' + ''.join(random.choice('1234567890') for x in range(8)),
            'AutoScalingGroupName': group_name or self._autoscale.get_new_groupname(hostclass)
        }

    def mock_lg(self, hostclass, name=None):
        """Creates a mock autoscaling launch configuration for hostclass"""
        lg_mock = {}
        default_name = '{0}_{1}_{2}'.format(self.environment_name, hostclass, random.randrange(0, 9999999))
        lg_mock['LaunchConfigurationName'] = name or default_name
        return lg_mock

    def test_get_group_scale_down(self):
        """Test scaling down to 0 hosts"""
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[self.mock_group_dictionary("mhcdummy")])):
            group = self._autoscale.get_group(
                hostclass="mhcdummy",
                launch_config="launch_config-X", vpc_zone_id="zone-X",
                min_size=0, max_size=1, desired_size=0)
            self.assertEqual(group['min_size'], 0)
            self.assertEqual(group['desired_capacity'], 0)

    def test_get_group_no_scale(self):
        """Test getting a group and not scaling it"""
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[self.mock_group_dictionary("mhcdummy")])):
            group = self._autoscale.get_group(
                hostclass="mhcdummy",
                launch_config="launch_config-X", vpc_zone_id="zone-X",
                min_size=None, max_size=None, desired_size=None)
            self.assertEqual(group['min_size'], 1)
            self.assertEqual(group['max_size'], 1)
            self.assertEqual(group['desired_capacity'], 1)

    def test_get_group_scale_up(self):
        """Test getting a group and scaling it up"""
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[self.mock_group_dictionary("mhcdummy")])):
            group = self._autoscale.get_group(
                hostclass="mhcdummy",
                launch_config="launch_config-X", vpc_zone_id="zone-X",
                min_size=None, max_size=5, desired_size=4)
            self.assertEqual(group['min_size'], 1)
            self.assertEqual(group['max_size'], 5)
            self.assertEqual(group['desired_capacity'], 4)

    def test_get_group_add_policies(self):
        """Test getting a group automatically adds scaling policies"""
        self._mock_boto3_connection.describe_auto_scaling_groups.return_value = {
            'AutoScalingGroups': [self.mock_group_dictionary("mhcdummy")]
        }

        group = self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X",
            vpc_zone_id="zone-X",
        )

        self._mock_boto3_connection.put_scaling_policy.assert_has_calls([
            call(
                AutoScalingGroupName=group['name'],
                PolicyName='up',
                PolicyType='SimpleScaling',
                AdjustmentType='PercentChangeInCapacity',
                ScalingAdjustment=10,
                Cooldown=600,
                MinAdjustmentMagnitude=1
            ),
            call(
                AutoScalingGroupName=group['name'],
                PolicyName='down',
                PolicyType='SimpleScaling',
                AdjustmentType='PercentChangeInCapacity',
                ScalingAdjustment=-10,
                Cooldown=600,
                MinAdjustmentMagnitude=1
            )
        ])

    def test_get_group_attach_elb(self):
        """Test getting a group and attaching an elb"""
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[self.mock_group_dictionary("mhcdummy")])):

            group = self._autoscale.get_group(
                hostclass="mhcdummy",
                launch_config="launch_config-X", vpc_zone_id="zone-X",
                load_balancers=['fake_elb'])

            self._mock_boto3_connection.attach_load_balancers.assert_called_with(
                AutoScalingGroupName=group['name'],
                LoadBalancerNames=['fake_elb'])

    def test_get_group_attach_tg(self):
        """Test getting a group and attaching a target group"""
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[self.mock_group_dictionary("mhcdummy")])):

            group = self._autoscale.get_group(
                hostclass="mhcdummy",
                launch_config="launch_config-X", vpc_zone_id="zone-X",
                target_groups=['fake_tg'])

            self._mock_boto3_connection.attach_load_balancer_target_groups.assert_called_with(
                AutoScalingGroupName=group['name'],
                TargetGroupARNs=['fake_tg'])

    def test_get_fresh_group_with_none_min(self):
        """Test getting a fresh group with None as min_size"""
        self._autoscale.get_existing_groups = MagicMock(side_effect=[[], [MagicMock()]])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X",
            vpc_zone_id="zone-X",
            min_size=None,
            max_size=5,
            desired_size=4
        )
        self._mock_boto3_connection.create_auto_scaling_group.assert_called_with(
            MinSize=0,
            MaxSize=5,
            DesiredCapacity=4,
            AutoScalingGroupName=ANY,
            LaunchConfigurationName=ANY,
            LoadBalancerNames=ANY,
            VPCZoneIdentifier=ANY,
            Tags=ANY,
            TerminationPolicies=ANY,
        )

    def test_get_fresh_group_with_none_max(self):
        """Test getting a fresh group with None as max_size"""
        self._autoscale.get_existing_groups = MagicMock(side_effect=[[], [MagicMock()]])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=1, max_size=None, desired_size=4)
        self._mock_boto3_connection.create_auto_scaling_group.assert_called_with(
            MinSize=1,
            MaxSize=4,
            DesiredCapacity=4,
            AutoScalingGroupName=ANY,
            LaunchConfigurationName=ANY,
            LoadBalancerNames=ANY,
            VPCZoneIdentifier=ANY,
            Tags=ANY,
            TerminationPolicies=ANY,
        )

    def test_create_policy_simple_scaling(self):
        """Test create plicy with simple scaling"""
        mock_group_name = "mock_group_name"
        mock_policy_name = "mock_policy_name"
        mock_adjustment_type = "PercentChangeInCapacity"
        min_adjustment_magnitude = 1
        scaling_adjustment = 10

        self._autoscale.create_policy(group_name=mock_group_name,
                                      policy_name=mock_policy_name,
                                      adjustment_type=mock_adjustment_type,
                                      scaling_adjustment=scaling_adjustment,
                                      min_adjustment_magnitude=min_adjustment_magnitude)

        self._mock_boto3_connection.put_scaling_policy.assert_called_with(
            AdjustmentType=mock_adjustment_type,
            AutoScalingGroupName=mock_group_name,
            Cooldown=600, MinAdjustmentMagnitude=min_adjustment_magnitude,
            PolicyName=mock_policy_name,
            PolicyType='SimpleScaling', ScalingAdjustment=scaling_adjustment)

    def test_create_policy_step_scaling(self):
        """Test create plicy with step scaling"""
        mock_group_name = "mock_group_name"
        mock_policy_name = "mock_policy_name"
        mock_adjustment_type = "mock_adjustment_type"
        step_scaling = "StepScaling"
        scaling_adjustment = 10
        metric_aggregation_type = "Maximum"
        step_adjustments = [{'MetricIntervalLowerBound': 25,
                             'MetricIntervalUpperBound': 75,
                             'ScalingAdjustment': 30}]
        estimated_instance_warmup = 123

        self._autoscale.create_policy(group_name=mock_group_name,
                                      policy_name=mock_policy_name,
                                      policy_type=step_scaling,
                                      adjustment_type=mock_adjustment_type,
                                      scaling_adjustment=scaling_adjustment,
                                      metric_aggregation_type=metric_aggregation_type,
                                      step_adjustments=step_adjustments,
                                      estimated_instance_warmup=estimated_instance_warmup)

        self._mock_boto3_connection.put_scaling_policy.assert_called_with(
            AdjustmentType=mock_adjustment_type,
            AutoScalingGroupName=mock_group_name,
            EstimatedInstanceWarmup=estimated_instance_warmup,
            MetricAggregationType=metric_aggregation_type,
            PolicyName=mock_policy_name,
            PolicyType=step_scaling,
            StepAdjustments=step_adjustments)

    def test_create_policy_invalid_args(self):
        """Test error is raised due to invalid arguments"""
        # Scaling adjustment must be passed in for simple scaling
        with self.assertRaises(TypeError):
            self._autoscale.create_policy(group_name="mock_group_name",
                                          policy_name="mock_policy_name",
                                          policy_type="SimpleScaling")

        # Min adjustment magnitude must be passed in if PercentChangeInCapacity is used
        with self.assertRaises(TypeError):
            self._autoscale.create_policy(group_name="mock_group_name",
                                          policy_name="mock_policy_name",
                                          adjustment_type="PercentChangeInCapacity",
                                          policy_type="SimpleScaling")

    def test_list_policies(self):
        """Test listing ploicies"""
        group_name = "mock_group_name"
        policy_types = ["mock_policy_type"]
        policy_names = ["mock_policy_name"]
        next_token = "mock_token"
        mock_policies = [{'AutoScalingGroupName': self.environment_name + 'ASG_name_1',
                          'PolicyName': 'policy_name_1',
                          'PolicyType': 'policy_type_1',
                          'AdjustmentType': 'adjustment_type_1',
                          'ScalingAdjustment': 'scaling_adjustment_1',
                          'StepAdjustments': [{'mock_step': 'mock_step_1'}],
                          'MinAdjustmentMagnitude': 1,
                          'Cooldown': 200,
                          'EstimatedInstanceWarmup': 333,
                          'Alarms': ['mock_alarm_1']},
                         {'AutoScalingGroupName': self.environment_name + 'ASG_name_2',
                          'PolicyName': 'policy_name_2',
                          'PolicyType': 'policy_type_2',
                          'AdjustmentType': 'adjustment_type_2',
                          'ScalingAdjustment': 'scaling_adjustment_2',
                          'StepAdjustments': [{'mock_step': 'mock_step_2'}],
                          'MinAdjustmentMagnitude': 2,
                          'Cooldown': 400,
                          'EstimatedInstanceWarmup': 666,
                          'Alarms': ['mock_alarm_2']}]

        shared_vars = {'next_token': next_token}

        def _mock_describe_policies(**_kwargs):
            if shared_vars['next_token']:
                temp_token = shared_vars['next_token']
                shared_vars['next_token'] = None
                return {'ScalingPolicies': [mock_policies[0]],
                        'NextToken': temp_token}

            return {'ScalingPolicies': [mock_policies[1]]}

        self._mock_boto3_connection.describe_policies.side_effect = _mock_describe_policies

        # Calling method under test
        policies = self._autoscale.list_policies(group_name=group_name,
                                                 policy_types=policy_types,
                                                 policy_names=policy_names)

        # Verifying results
        expected_policies = [{'Warmup': mock_policies[0]['EstimatedInstanceWarmup'],
                              'Cooldown': mock_policies[0]['Cooldown'],
                              'ASG': mock_policies[0]['AutoScalingGroupName'],
                              'Name': mock_policies[0]['PolicyName'],
                              'Step Adjustments': mock_policies[0]['StepAdjustments'],
                              'Alarms': mock_policies[0]['Alarms'],
                              'Adjustment Type': mock_policies[0]['AdjustmentType'],
                              'Min Adjustment': mock_policies[0]['MinAdjustmentMagnitude'],
                              'Type': mock_policies[0]['PolicyType'],
                              'Scaling Adjustment': mock_policies[0]['ScalingAdjustment']},
                             {'Warmup': mock_policies[1]['EstimatedInstanceWarmup'],
                              'Cooldown': mock_policies[1]['Cooldown'],
                              'ASG': mock_policies[1]['AutoScalingGroupName'],
                              'Name': mock_policies[1]['PolicyName'],
                              'Step Adjustments': mock_policies[1]['StepAdjustments'],
                              'Alarms': mock_policies[1]['Alarms'],
                              'Adjustment Type': mock_policies[1]['AdjustmentType'],
                              'Min Adjustment': mock_policies[1]['MinAdjustmentMagnitude'],
                              'Type': mock_policies[1]['PolicyType'],
                              'Scaling Adjustment': mock_policies[1]['ScalingAdjustment']}]
        self.assertEqual(expected_policies, policies)

        expected_calls = [call(AutoScalingGroupName=group_name,
                               PolicyNames=policy_names,
                               PolicyTypes=policy_types),
                          call(AutoScalingGroupName=group_name,
                               NextToken=next_token,
                               PolicyNames=policy_names,
                               PolicyTypes=policy_types)]
        self._mock_boto3_connection.describe_policies.assert_has_calls(expected_calls)

    def test_get_fresh_group_with_none_desired(self):
        """Test getting a fresh group with None as max_size"""
        self._autoscale.get_existing_groups = MagicMock(side_effect=[[], [MagicMock()]])
        self._autoscale.get_group(
            hostclass="mhcdummy",
            launch_config="launch_config-X", vpc_zone_id="zone-X",
            min_size=1, max_size=5, desired_size=None)
        self._mock_boto3_connection.create_auto_scaling_group.assert_called_with(
            MinSize=1,
            MaxSize=5,
            DesiredCapacity=5,
            AutoScalingGroupName=ANY,
            LaunchConfigurationName=ANY,
            LoadBalancerNames=ANY,
            VPCZoneIdentifier=ANY,
            Tags=ANY,
            TerminationPolicies=ANY,
        )

    @staticmethod
    def mock_launchconfig(env, hostclass, lc_num=1):
        """Create a dummy LaunchConfiguration"""
        launchconfig = {
            'LaunchConfigurationName': '{0}_{1}_{2}'.format(env, hostclass, lc_num),
            'UserData': '',
            'BlockDeviceMappings': [{
                'DeviceName': '/dev/root',
            }, {
                'DeviceName': '/dev/snap',
                'Ebs': {
                    'SnapshotId': 'snap-12345678'
                },
            }, {
                'DeviceName': '/dev/ephemeral'
            }]

        }
        return launchconfig

    def test_get_snapshot_dev(self):
        """_get_snapshot_dev returns the one device with a snapshot attached"""
        mock_lc = self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo")
        self.assertEqual(DiscoAutoscale._get_snapshot_dev(mock_lc, "mhcfoo")['DeviceName'], "/dev/snap")

    def test_update_snapshot_using_latest(self):
        """Calling update_snapshot when already running latest snapshot does nothing"""
        self._autoscale._get_launch_config = MagicMock(
            return_value=self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo"))
        self._autoscale.modify_group = MagicMock()
        self._autoscale.update_snapshot("snap-12345678", 99, hostclass="mhcfoo")
        self.assertEqual(self._autoscale.modify_group.call_count, 0)

    def test_update_snapshot_with_update(self):
        """Calling update_snapshot when not running latest snapshot calls modify_group with new config"""
        mock_lc = self.mock_launchconfig(self._autoscale.environment_name, "mhcfoo", 1)
        self._autoscale._get_launch_config = MagicMock(return_value=mock_lc)
        self._autoscale.modify_group = MagicMock()
        self._autoscale.get_existing_group = MagicMock(return_value="group")
        self._autoscale.update_snapshot("snap-NEW", 99, hostclass="mhcfoo")

        mock_calls = [call("group", mock_lc['LaunchConfigurationName'])]
        self.assertNotEqual(self._autoscale.modify_group.mock_calls, mock_calls)

        snap_bdm = None
        for device in mock_lc['BlockDeviceMappings']:
            if device['DeviceName'] == '/dev/snap':
                snap_bdm = device
                break

        self.assertEqual(snap_bdm['Ebs']['SnapshotId'], "snap-NEW")
        self.assertEqual(self._autoscale.modify_group.call_count, 1)

    def test_update_elb_with_new_lb(self):
        """update_elb will add new lb and remove old when there is no overlap in sets"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['LoadBalancerNames'] = ["old_lb1", "old_lb2"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_elb(["new_lb"], hostclass="mhcfoo")
            self.assertEqual(ret, (set(["new_lb"]), set(["old_lb1", "old_lb2"])))

    def test_update_elb_with_new_lb_and_old_lb(self):
        """update_elb will not churn an lb that is in both the existing config and new config"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['LoadBalancerNames'] = ["old_lb", "both_lb"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_elb(["new_lb", "both_lb"], hostclass="mhcfoo")
            self.assertEqual(ret, (set(["new_lb"]), set(["old_lb"])))

    def test_update_elb_without_new_lb(self):
        """update_elb will remove all load balancers when none are configured"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['LoadBalancerNames'] = ["old_lb1", "old_lb2"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_elb([], hostclass="mhcfoo")
            self.assertEqual(ret, (set([]), set(["old_lb1", "old_lb2"])))

    def test_update_tg_with_new_lb(self):
        """update_tg will add new tg and remove old when there is no overlap in sets"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['TargetGroupARNs'] = ["old_tg1", "old_tg2"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_tg(["new_tg"], hostclass="mhcfoo")
            self.assertEqual(ret, (set(["new_tg"]), set(["old_tg1", "old_tg2"])))

    def test_update_tg_with_new_lb_and_old_lb(self):
        """update_tg will not churn a tg that is in both the existing config and new config"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['TargetGroupARNs'] = ["old_tg", "both_tg"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_tg(["new_tg", "both_tg"], hostclass="mhcfoo")
            self.assertEqual(ret, (set(["new_tg"]), set(["old_tg"])))

    def test_update_tg_without_new_lb(self):
        """update_tg will remove all target groups when none are configured"""
        grp = self.mock_group_dictionary("mhcfoo")
        grp['TargetGroupARNs'] = ["old_tg1", "old_tg2"]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=[grp])):

            ret = self._autoscale.update_tg([], hostclass="mhcfoo")
            self.assertEqual(ret, (set([]), set(["old_tg1", "old_tg2"])))

    def test_gg_filters_env_correctly(self):
        """group_generator correctly filters based on the environment"""
        good_groups = [
            self.mock_group_dictionary("mhcfoo"),
            self.mock_group_dictionary("mhcbar"),
            self.mock_group_dictionary("mhcfoobar")
        ]
        bad_groups = [
            self.mock_group_dictionary("mhcnoncomformist", name="foo-mhcnoncomformist-123141231123")
        ]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=(good_groups + bad_groups))):

            good_group_ids = [group['AutoScalingGroupName'] for group in good_groups]
            actual_group_ids = [group['name'] for group in self._autoscale.get_existing_groups()]

            self.assertEqual(sorted(good_group_ids), sorted(actual_group_ids))

    def test_gg_filters_hostclass_correctly(self):
        """get_existing_groups correctly filters based on the hostclass"""
        good_groups = [self.mock_group_dictionary("mhcneedle")]
        bad_groups = [
            self.mock_group_dictionary("mhcfoo"),
            self.mock_group_dictionary("mhcbar"),
            self.mock_group_dictionary("mhcfoobar")
        ]
        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=(good_groups + bad_groups))):

            good_group_ids = [group['AutoScalingGroupName'] for group in good_groups]

            actual_groups = self._autoscale.get_existing_groups(hostclass="mhcneedle")
            actual_group_ids = [group['name'] for group in actual_groups]

            self.assertEqual(sorted(good_group_ids), sorted(actual_group_ids))

    def test_ig_filters_env_correctly(self):
        """inst_generator correctly filters based on the environment"""
        good_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        bad_insts = [self.mock_inst("mhcnoncomformist", group_name="foo_mhcnoncomformist_123141231123")]
        self._mock_boto3_connection.describe_auto_scaling_instances.return_value = {
            'AutoScalingInstances': good_insts + bad_insts
        }

        good_inst_ids = [inst['InstanceId'] for inst in good_insts]
        actual_inst_ids = [inst['instance_id'] for inst in self._autoscale.get_instances()]

        self.assertEqual(sorted(actual_inst_ids), sorted(good_inst_ids))

    def test_ig_filters_hostclass_correctly(self):
        """inst_generator correctly filters based on the hostclass"""
        good_insts = [self.mock_inst("mhcneedle")]
        bad_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        self._mock_boto3_connection.describe_auto_scaling_instances.return_value = {
            'AutoScalingInstances': good_insts + bad_insts
        }

        good_inst_ids = [inst['InstanceId'] for inst in good_insts]

        actual_instances = self._autoscale.get_instances(hostclass="mhcneedle")
        actual_inst_ids = [inst['instance_id'] for inst in actual_instances]

        self.assertEqual(sorted(actual_inst_ids), sorted(good_inst_ids))

    def test_ig_filters_groupname_correctly(self):
        """inst_generator correctly filters based on the group name"""
        good_insts = [self.mock_inst("mhcneedle")]
        bad_insts = [self.mock_inst("mhcfoo"), self.mock_inst("mhcbar"), self.mock_inst("mhcfoobar")]
        self._mock_boto3_connection.describe_auto_scaling_instances.return_value = {
            'AutoScalingInstances': good_insts + bad_insts
        }

        good_inst_ids = [inst['InstanceId'] for inst in good_insts]

        actual_instances = self._autoscale.get_instances(group_name=good_insts[0]['AutoScalingGroupName'])
        actual_inst_ids = [inst['instance_id'] for inst in actual_instances]

        self.assertEqual(sorted(actual_inst_ids), sorted(good_inst_ids))

    def test_cg_filters_env_correctly(self):
        """config_generator correctly filters based on the environment"""
        good_lgs = [self.mock_lg("mhcfoo"), self.mock_lg("mhcbar"), self.mock_lg("mhcfoobar")]
        bad_lgs = [self.mock_lg("mhcnoncomformist", name="foo_mhcnoncomformist_123141231123")]

        self._mock_boto3_connection.describe_launch_configurations.return_value = {
            'LaunchConfigurations': good_lgs + bad_lgs
        }

        self.assertEqual(self._autoscale.get_configs(), good_lgs)

    def test_get_launch_configs_filter(self):
        """get_launch_configs correctly filters out empty launch config names"""
        mock_groups = [
            self.mock_group_dictionary("mhcfoo"),
            self.mock_group_dictionary("mhcbar"),
            self.mock_group_dictionary("mhcfoo", launch_config_name="")
        ]

        with patch("disco_aws_automation.disco_autoscale.get_boto3_paged_results",
                   MagicMock(return_value=mock_groups)):
            self._autoscale.get_configs = MagicMock()

            self._autoscale.get_launch_configs()

            self._autoscale.get_configs.assert_called_once_with(
                names=[
                    mock_groups[0]['LaunchConfigurationName'],
                    mock_groups[1]['LaunchConfigurationName']
                ]
            )
