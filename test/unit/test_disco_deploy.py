"""
Tests of disco_bake
"""
from __future__ import print_function
import random
from unittest import TestCase

import boto.ec2.instance
from mock import MagicMock, create_autospec, call

from disco_aws_automation import DiscoDeploy, DiscoAWS, DiscoAutoscale, DiscoBake, DiscoELB
from disco_aws_automation.disco_constants import DEPLOYMENT_STRATEGY_BLUE_GREEN
from disco_aws_automation.exceptions import (
    TimeoutError,
    MaintenanceModeError,
    IntegrationTestError,
    TooManyAutoscalingGroups
)
from test.helpers.patch_disco_aws import get_mock_config

# Don't limit number of tests
# pylint: disable=R0904

MOCK_PIPELINE_DEFINITION = [
    {
        'hostclass': 'mhcintegrated',
        'min_size': 1,
        'desired_size': 1,
        'integration_test': 'foo_service',
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcbluegreen',
        'min_size': 2,
        'desired_size': 2,
        'integration_test': 'blue_green_service',
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcbluegreennondeployable',
        'min_size': 1,
        'desired_size': 1,
        'integration_test': 'blue_green_service',
        'deployable': 'no'
    },
    {
        'hostclass': 'mhcsmokey',
        'min_size': 2,
        'desired_size': 2,
        'integration_test': None,
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcscarey',
        'min_size': 1,
        'desired_size': 1,
        'integration_test': None,
        'deployable': 'no'
    },
    {
        'hostclass': 'mhcfoo',
        'min_size': 1,
        'desired_size': 1,
        'integration_test': None,
        'deployable': 'no'
    },
    {
        'hostclass': 'mhctimedautoscale',
        'min_size': '3@30 16 * * 1-5:4@00 17 * * 1-5',
        'desired_size': '5@30 16 * * 1-5:6@00 17 * * 1-5',
        'max_size': '5@30 16 * * 1-5:6@00 17 * * 1-5',
        'integration_test': None,
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhctimedautoscalenodeploy',
        'min_size': '3@30 16 * * 1-5:4@00 17 * * 1-5',
        'desired_size': '5@30 16 * * 1-5:6@00 17 * * 1-5',
        'max_size': '5@30 16 * * 1-5:6@00 17 * * 1-5',
        'integration_test': None,
        'deployable': 'no'
    }
]

MOCK_CONFIG_DEFINITON = {
    "test": {
        "test_user": "test_user",
        "command": "test_command",
        "hostclass": "test_hostclass"
    },
    "hostclass_being_tested": {
        "test_hostclass": "another_test_hostclass"
    },
    "mhcbluegreen": {
        "deployment_strategy": DEPLOYMENT_STRATEGY_BLUE_GREEN,
        "elb": "yes"
    },
    "mhcbluegreennondeployable": {
        "deployment_strategy": DEPLOYMENT_STRATEGY_BLUE_GREEN
    }
}


# Too many tests is probably not a bad thing
# pylint: disable=too-many-lines
class DiscoDeployTests(TestCase):
    '''Test DiscoDeploy class'''

    # This tells a parallel nose run to share this class's fixtures rather than run setUp in each process.
    # Useful for when lengthy setUp runs can cause a parallel nose run to time out.
    _multiprocess_shared_ = True

    def mock_ami(self, name, stage=None, state=u'available'):
        '''Create a mock AMI'''
        ami = create_autospec(boto.ec2.image.Image)
        ami.name = name
        ami.tags = MagicMock()
        ami.tags.get = MagicMock(return_value=stage)
        ami.id = 'ami-' + ''.join(random.choice("0123456789abcdef") for _ in range(8))
        ami.state = state
        return ami

    def mock_instance(self):
        '''Create a mock Instance'''
        inst = create_autospec(boto.ec2.instance.Instance)
        inst.id = 'i-' + ''.join(random.choice("0123456789abcdef") for _ in range(8))
        inst.instance_id = inst.id
        inst.image_id = 'ami-' + ''.join(random.choice("0123456789abcdef") for _ in range(8))
        inst.tags = {"hostclass": "hostclass_being_tested"}
        return inst

    def mock_group(self, hostclass, min_size=None, max_size=None, desired_size=None, instances=None):
        '''Creates a mock autoscaling group for hostclass'''
        group_mock = MagicMock()
        timestamp = ''.join(random.choice("0123456789") for _ in range(13))
        group_mock.name = self._environment_name + '_' + hostclass + "_" + timestamp
        group_mock.min_size = min_size or 1
        group_mock.max_size = max_size or 1
        group_mock.desired_capacity = desired_size or 1
        group_mock.instances = instances or []
        return group_mock

    def add_ami(self, name, stage, state=u'available'):
        '''Add one Instance AMI Mock to an AMI list'''
        ami = self.mock_ami(name, stage, state)
        assert ami.name == name
        assert ami.tags.get() == stage
        self._amis.append(ami)
        self._amis_by_name[ami.name] = ami
        return ami

    def setUp(self):
        self._environment_name = "foo"
        self._disco_autoscale = create_autospec(DiscoAutoscale, instance=True)
        self._disco_elb = create_autospec(DiscoELB, instance=True)
        self._disco_aws = create_autospec(DiscoAWS, instance=True)
        self._test_aws = self._disco_aws
        self._existing_group = self.mock_group("mhcfoo")
        self._disco_autoscale.get_existing_group.return_value = self._existing_group
        self._disco_bake = MagicMock()
        self._disco_bake.promote_ami = MagicMock()
        self._disco_bake.ami_stages = MagicMock(return_value=['untested', 'failed', 'tested'])
        self._disco_bake.get_ami_creation_time = DiscoBake.extract_ami_creation_time_from_ami_name
        self._ci_deploy = DiscoDeploy(
            self._disco_aws, self._test_aws, self._disco_bake, self._disco_autoscale, self._disco_elb,
            pipeline_definition=MOCK_PIPELINE_DEFINITION,
            ami=None, hostclass=None, allow_any_hostclass=False,
            config=get_mock_config(MOCK_CONFIG_DEFINITON))
        self._ci_deploy._disco_aws.terminate = MagicMock()
        self._amis = []
        self._amis_by_name = {}
        self.add_ami('mhcfoo 1', 'untested')
        self.add_ami('mhcbar 2', 'tested')
        self.add_ami('mhcfoo 4', 'tested')
        self.add_ami('mhcfoo 5', None)
        self.add_ami('mhcbar 1', 'tested')
        self.add_ami('mhcfoo 2', 'tested')
        self.add_ami('mhcfoo 3', 'tested')
        self.add_ami('mhcfoo 6', 'untested')
        self.add_ami('mhcnew 1', 'untested')
        self.add_ami('mhcfoo 7', 'failed')
        self.add_ami('mhcintegrated 1', None)
        self.add_ami('mhcintegrated 2', 'tested')
        self.add_ami('mhcintegrated 3', None)
        self.add_ami('mhcbluegreen 1', 'tested')
        self.add_ami('mhcbluegreen 2', 'untested')
        self.add_ami('mhcbluegreennondeployable 1', 'tested')
        self.add_ami('mhcbluegreennondeployable 2', 'untested')
        self.add_ami('mhctimedautoscale 1', 'untested')
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=self._amis)

    def test_filter_with_ami_restriction(self):
        '''Tests that filter on ami works when ami is set'''
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcbar 2'].id]
        self.assertEqual(self._ci_deploy._filter_amis(self._amis),
                         [self._amis_by_name['mhcbar 2']])

    def test_filter_on_hostclass_wo_restriction(self):
        '''Tests that filter on hostclass does nothing when filtering is not restricted'''
        self._ci_deploy._allow_any_hostclass = True
        self.assertEqual(self._ci_deploy._filter_amis(self._amis), self._amis)

    def test_filter_with_hostclass_restriction(self):
        '''Tests that filter on hostclass filters when the filtering hostclass is set'''
        self._ci_deploy._restrict_hostclass = 'mhcbar'
        self.assertEqual(self._ci_deploy._filter_amis(self._amis),
                         [self._amis_by_name['mhcbar 2'], self._amis_by_name['mhcbar 1']])

    def test_filter_with_pipeline_restriction(self):
        '''Tests that filter on hostclass filters to pipeline when no hostclass filter set'''
        self.assertEqual(self._ci_deploy._filter_amis(self._amis),
                         [self._amis_by_name["mhcfoo 1"],
                          self._amis_by_name["mhcfoo 4"],
                          self._amis_by_name["mhcfoo 5"],
                          self._amis_by_name["mhcfoo 2"],
                          self._amis_by_name["mhcfoo 3"],
                          self._amis_by_name["mhcfoo 6"],
                          self._amis_by_name["mhcfoo 7"],
                          self._amis_by_name["mhcintegrated 1"],
                          self._amis_by_name["mhcintegrated 2"],
                          self._amis_by_name["mhcintegrated 3"],
                          self._amis_by_name["mhcbluegreen 1"],
                          self._amis_by_name["mhcbluegreen 2"],
                          self._amis_by_name["mhcbluegreennondeployable 1"],
                          self._amis_by_name["mhcbluegreennondeployable 2"],
                          self._amis_by_name["mhctimedautoscale 1"]])

    def test_filter_by_hostclass_beats_pipeline(self):
        '''Tests that filter overrides pipeline filtering when hostclass is set'''
        self._ci_deploy._restrict_hostclass = 'mhcbar'
        self.assertEqual(self._ci_deploy._filter_amis(self._amis),
                         [self._amis_by_name['mhcbar 2'], self._amis_by_name['mhcbar 1']])

    def test_all_stage_amis_with_any_hostclass(self):
        '''Tests that all_stage_amis calls list_amis correctly without restrictions'''
        self._ci_deploy._allow_any_hostclass = True
        self.assertEqual(self._ci_deploy.all_stage_amis, self._amis)

    def test_all_stage_amis_without_any_hostclass(self):
        '''Tests that all_stage_amis calls list_amis correctly with restrictions'''
        self.assertEqual(self._ci_deploy.all_stage_amis,
                         [self._amis_by_name["mhcfoo 1"],
                          self._amis_by_name["mhcfoo 4"],
                          self._amis_by_name["mhcfoo 5"],
                          self._amis_by_name["mhcfoo 2"],
                          self._amis_by_name["mhcfoo 3"],
                          self._amis_by_name["mhcfoo 6"],
                          self._amis_by_name["mhcfoo 7"],
                          self._amis_by_name["mhcintegrated 1"],
                          self._amis_by_name["mhcintegrated 2"],
                          self._amis_by_name["mhcintegrated 3"],
                          self._amis_by_name["mhcbluegreen 1"],
                          self._amis_by_name["mhcbluegreen 2"],
                          self._amis_by_name["mhcbluegreennondeployable 1"],
                          self._amis_by_name["mhcbluegreennondeployable 2"],
                          self._amis_by_name["mhctimedautoscale 1"]])

    def test_get_newest_in_either_map(self):
        '''Tests that get_newest_in_either_map works with simple input'''
        list_a = [self.mock_ami("mhcfoo 1"), self.mock_ami("mhcbar 2"), self.mock_ami("mhcmoo 1")]
        list_b = [self.mock_ami("mhcfoo 3"), self.mock_ami("mhcbar 1"), self.mock_ami("mhcmoo 2")]
        list_c = [list_b[0], list_a[1], list_b[2]]
        map_a = {DiscoBake.ami_hostclass(ami): ami for ami in list_a}
        map_b = {DiscoBake.ami_hostclass(ami): ami for ami in list_b}
        map_c = {DiscoBake.ami_hostclass(ami): ami for ami in list_c}
        self.assertEqual(self._ci_deploy.get_newest_in_either_map(map_a, map_b), map_c)

    def test_get_newest_in_either_map_old_first(self):
        '''Tests that get_newest_in_either_map works if hostclass not in first list'''
        list_a = [self.mock_ami("mhcfoo 1"), self.mock_ami("mhcbar 2")]
        list_b = [self.mock_ami("mhcfoo 3"), self.mock_ami("mhcbar 1"), self.mock_ami("mhcmoo 2")]
        list_c = [list_b[0], list_a[1], list_b[2]]
        map_a = {DiscoBake.ami_hostclass(ami): ami for ami in list_a}
        map_b = {DiscoBake.ami_hostclass(ami): ami for ami in list_b}
        map_c = {DiscoBake.ami_hostclass(ami): ami for ami in list_c}
        self.assertEqual(self._ci_deploy.get_newest_in_either_map(map_a, map_b), map_c)

    def test_get_latest_untested_amis_works(self):
        '''Tests that get_latest_untested_amis() returns untested amis'''
        self.assertEqual(self._ci_deploy.get_latest_untested_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 6'])

    def test_get_latest_untagged_amis_works(self):
        '''Tests that get_latest_untagged_amis() returns untagged amis'''
        self.assertEqual(self._ci_deploy.get_latest_untagged_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 5'])

    def test_get_latest_tested_amis_works_inc(self):
        '''Tests that get_latest_tested_amis() returns latest tested amis (inc)'''
        self.assertEqual(self._ci_deploy.get_latest_tested_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 4'])

    def test_get_latest_tested_amis_works_dec(self):
        '''Tests that get_latest_tested_amis() returns latest tested amis (dec)'''
        self._ci_deploy._allow_any_hostclass = True
        self.assertEqual(self._ci_deploy.get_latest_tested_amis()['mhcbar'],
                         self._amis_by_name['mhcbar 2'])

    def test_get_latest_tested_amis_works_no_date(self):
        '''Tests that get_latest_tested_amis() works when an AMI is without a date'''
        def _special_date(ami):
            return (None if ami.name == 'mhcfoo 4' else
                    DiscoBake.extract_ami_creation_time_from_ami_name(ami))
        self._ci_deploy._disco_bake.get_ami_creation_time = _special_date
        self.assertEqual(self._ci_deploy.get_latest_tested_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 3'])

    def test_get_latest_failed_amis_works(self):
        '''Tests that get_latest_failed_amis() returns latest failed amis'''
        self.assertEqual(self._ci_deploy.get_latest_failed_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 7'])

    def test_get_test_amis_from_any_hostclass(self):
        '''Tests that we can find the next untested ami to test for each hostclass without restrictions'''
        self._ci_deploy._allow_any_hostclass = True
        self.assertEqual([ami.name for ami in self._ci_deploy.get_test_amis()],
                         ['mhcfoo 6',
                          'mhcbluegreennondeployable 2',
                          'mhcnew 1',
                          'mhcbluegreen 2',
                          'mhctimedautoscale 1'])

    def test_get_test_amis_from_pipeline(self):
        '''Tests that we can find the next untested ami to test for each hostclass restricted to pipeline'''
        self.assertEqual([ami.name for ami in self._ci_deploy.get_test_amis()],
                         ['mhcfoo 6', 'mhcbluegreennondeployable 2',
                          'mhcbluegreen 2', 'mhctimedautoscale 1'])

    def test_get_failed_amis(self):
        '''Tests that we can find the next untested ami to test for each hostclass'''
        self.assertEqual([ami.name for ami in self._ci_deploy.get_failed_amis()],
                         ['mhcfoo 7'])

    def test_get_latest_running_amis(self):
        '''get_latest_running_amis returns later ami if hostclass has two AMIs running'''
        amis = [self._amis_by_name['mhcintegrated 1'], self._amis_by_name['mhcintegrated 2']]
        self._ci_deploy._disco_bake.get_amis = MagicMock(return_value=amis)
        self.assertEqual(self._ci_deploy.get_latest_running_amis()['mhcintegrated'], amis[1])

    def test_get_update_amis_untested(self):
        '''Tests that we can find the next untested AMI to deploy in prod'''
        amis = {"mhcintegrated": self._amis_by_name['mhcintegrated 2']}
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value=amis)
        self.assertEqual([ami.name for ami in self._ci_deploy.get_update_amis()],
                         ['mhcbluegreen 1', 'mhcintegrated 3'])

    def test_get_update_amis_tested(self):
        '''Tests that we can find the next tested AMI to deploy in prod'''
        amis = {"mhcintegrated": self._amis_by_name['mhcintegrated 2']}
        self.add_ami('mhcintegrated 4', 'tested')
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value=amis)
        self.assertEqual([ami.name for ami in self._ci_deploy.get_update_amis()],
                         ['mhcbluegreen 1', 'mhcintegrated 4'])

    def test_get_update_amis_none(self):
        '''Tests that we can don't return any amis to update in prod when we are up to date'''
        amis = {"mhcintegrated": self._amis_by_name['mhcintegrated 3'],
                "mhcbluegreen": self._amis_by_name['mhcbluegreen 2']}
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value=amis)
        self.assertEqual(self._ci_deploy.get_update_amis(), [])

    def test_get_update_amis_failed(self):
        '''Tests that we can don't return failed AMIs to update to in prod'''
        amis = {"mhcintegrated": self._amis_by_name['mhcintegrated 3'],
                "mhcbluegreen": self._amis_by_name['mhcbluegreen 2']}
        self.add_ami('mhcintegrated 4', 'failed')
        self.add_ami('mhcbluegreen 3', 'failed')
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value=amis)
        self.assertEqual(self._ci_deploy.get_update_amis(), [])

    def test_get_update_amis_not_running(self):
        '''Tests that update an AMI that is not runnng'''
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value={})
        self.assertEqual([ami.name for ami in self._ci_deploy.get_update_amis()],
                         ['mhcbluegreen 1', 'mhcintegrated 3'])

    def test_is_deployable(self):
        '''Tests if DiscoDeploy.is_deployable works correctly'''
        self.assertTrue(self._ci_deploy.is_deployable('mhcintegrated'))
        self.assertTrue(self._ci_deploy.is_deployable('mhcbluegreen'))
        self.assertTrue(self._ci_deploy.is_deployable('mhcsmokey'))
        self.assertTrue(self._ci_deploy.is_deployable('mhcundefined'))
        self.assertFalse(self._ci_deploy.is_deployable('mhcscarey'))
        self.assertFalse(self._ci_deploy.is_deployable('mhcbluegreennondeployable'))

    def test_get_integration_test(self):
        '''Tests if DiscoDeploy.get_integration_test works correctly'''
        self.assertEqual(self._ci_deploy.get_integration_test('mhcintegrated'), 'foo_service')
        self.assertEqual(self._ci_deploy.get_integration_test('mhcbluegreen'), 'blue_green_service')
        self.assertIsNone(self._ci_deploy.get_integration_test('mhcundefined'))
        self.assertIsNone(self._ci_deploy.get_integration_test('mhcscarey'))

    def test_wait_for_smoketests_does_wait(self):
        '''Tests that we wait for autoscaling to complete'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock(side_effect=TimeoutError())
        self._ci_deploy._disco_aws.smoketest = MagicMock(return_value=True)
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2), False)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2)
        self.assertEqual(self._ci_deploy._disco_aws.smoketest.call_count, 0)

    def test_wait_for_smoketests_does_smoke(self):
        '''Tests that we do smoketests'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(return_value=True)
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2), True)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'])
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_wait_for_smoketests_does_smoke_time(self):
        '''Tests that we handle smoketest Timeout'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(side_effect=TimeoutError())
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2), False)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'])
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_promote_no_throw(self):
        '''_promote_ami swallows exceptions'''
        self._ci_deploy._disco_bake.promote_ami = MagicMock(side_effect=Exception())
        ami = MagicMock()
        self._ci_deploy._promote_ami(ami, "super")

    def test_nodeploy_ami_dry_run(self):
        """We don't call spinup in a no deploy AMI dry_run"""
        self._ci_deploy.handle_nodeploy_ami(MagicMock(), dry_run=True)
        self.assertEqual(self._disco_aws.spinup.call_count, 0)

    def test_tested_ami_dry_run(self):
        """We don't call spinup in a deployed AMI dry_run"""
        self._ci_deploy.handle_tested_ami(MagicMock(), dry_run=True)
        self.assertEqual(self._disco_aws.spinup.call_count, 0)

    def test_blue_green_dry_run(self):
        """We don't call spinup in a blue/green dry_run"""
        self.assertTrue(self._ci_deploy.handle_blue_green_ami(MagicMock(), dry_run=True))
        self.assertEqual(self._disco_aws.spinup.call_count, 0)

    def test_bg_deploy_works_with_no_orig_group(self):
        '''Blue/green deploy works with no existing group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'max_size': 2,
                    'min_size': 2, 'integration_test': "blue_green_service", 'desired_size': 2,
                    'smoke_test': 'no', 'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'desired_size': 2, 'max_size': 2,
                    'integration_test': "blue_green_service", 'smoke_test': 'no',
                    'hostclass': 'mhcbluegreen'}], group_name=new_group.name)])
        self._disco_autoscale.delete_groups.assert_not_called()
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_nodeploy_works(self):
        '''Blue/green deploy works when the ami is not deployable'''
        ami = MagicMock()
        ami.name = "mhcbluegreennondeployable 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcbluegreennondeployable")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 1, 'max_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreennondeployable'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_not_called()

    def test_bg_deploy_works_with_original_group(self):
        '''Blue/green deploy works with an existing group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        old_group = self.mock_group("mhcbluegreen", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4,
                    'smoke_test': 'no', 'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4,
                    'integration_test': "blue_green_service", 'smoke_test': 'no',
                    'hostclass': 'mhcbluegreen'}], group_name=new_group.name)])
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=old_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_deploy_with_bad_new_group_name(self):
        '''Blue/green deploy throws an exception if it gets the wrong new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.return_value = group
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)

    def test_bg_deploy_with_failing_tests(self):
        '''Blue/green deploy fails if tests fail, and destroys the new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=False)
        old_group = self.mock_group("mhcbluegreen", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'failed')
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_deploy_when_unable_to_test(self):
        '''Blue/green deploy fails if unable to run tests, and destroys the new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(side_effect=IntegrationTestError)
        old_group = self.mock_group("mhcbluegreen", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_deploy_with_failing_elbs(self):
        '''Blue/green deploy fails if elbs fail, and destroys the new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_elb.wait_for_instance_health_state.side_effect = TimeoutError
        old_group = self.mock_group("mhcbluegreen", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        instance_ids = [inst.instance_id for inst in instances]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_called_with(hostclass="mhcbluegreen",
                                                                          instance_ids=instance_ids)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4,
                    'smoke_test': 'no', 'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4,
                    'integration_test': "blue_green_service", 'smoke_test': 'no',
                    'hostclass': 'mhcbluegreen'}], group_name=new_group.name)])
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_deploy_with_bad_testing_mode(self):
        '''Blue/green deploy fails if unable to exit testing mode, and destroys the new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        old_group = self.mock_group("mhcbluegreen", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_with_hc_not_in_pl_and_no_group(self):
        '''Blue/green is non-deployable if hostclass is not in pipeline, and dies with no existing group'''
        ami = MagicMock()
        ami.name = "mhcfoo 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcfoo")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertTrue(self._ci_deploy.test_ami(ami,
                                                 deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                 dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with(
            [{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 1,
              'integration_test': None, 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
              'hostclass': 'mhcfoo'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_hc_not_in_pl_and_group(self):
        '''Blue/green is non-deployable if hostclass is not in pipeline, dies, and updates existing group'''
        ami = MagicMock()
        ami.name = "mhcfoo 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        old_group = self.mock_group("mhcfoo", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcfoo")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertTrue(self._ci_deploy.test_ami(ami,
                                                 deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                 dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 2,
                    'integration_test': None, 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
                    'hostclass': 'mhcfoo'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 2,
                    'integration_test': None, 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
                    'hostclass': 'mhcfoo'}], group_name=old_group.name)])
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_spinup_error_and_og(self):
        '''Blue/green can handle an an exception when spinning up the new ASG with old group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        old_group = self.mock_group("mhcbluegreen")
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_spinup_error_and_no_og(self):
        '''Blue/green can handle an an exception when spinning up the new ASG with no old group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        new_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 2, 'max_size': 2, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_spinup_error_and_no_groups(self):
        '''Blue/green can handle an an exception when spinning up the new ASG with no groups'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        self._disco_autoscale.get_existing_group.side_effect = [None, None]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 2, 'max_size': 2, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_not_called()

    def test_bg_with_error_and_og_and_no_ng(self):
        '''Blue/green can handle an an exception when spinning up the new ASG w/ old group and no new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        old_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, None]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_not_called()

    def test_bg_with_too_many_autoscaling_groups(self):
        '''Blue/green can handle too many autoscaling groups error when spinning up a new ASG'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = TooManyAutoscalingGroups
        old_group = self.mock_group("mhcbluegreen")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, None]
        self.assertFalse(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_autoscale.delete_groups.assert_not_called()

    def test_bg_timed_autoscaling(self):
        '''Blue/green can handle creating timed autoscaling actions'''
        ami = MagicMock()
        ami.name = "mhctimedautoscale 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhctimedautoscale")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami,
                                                 deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                 dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 3, 'desired_size': 6, 'max_size': 6}], create_if_exists=True, testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 3, 'desired_size': 6, 'max_size': 6}], group_name=new_group.name)])
        self._disco_aws.create_scaling_schedule.assert_called_once_with(
            min_size='3@30 16 * * 1-5:4@00 17 * * 1-5',
            desired_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            max_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            group_name=new_group.name,
            hostclass=None
        )

    def test_bg_ta_respects_og_size(self):
        '''Blue/green can handle creating timed autoscaling actions and respects the old group's sizing'''
        ami = MagicMock()
        ami.name = "mhctimedautoscale 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        old_group = self.mock_group("mhctimedautoscale", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhctimedautoscale")
        self._disco_autoscale.get_existing_group.side_effect = [old_group, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami,
                                                 deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                 dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4}], create_if_exists=True, testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4}], group_name=new_group.name)])
        self._disco_aws.create_scaling_schedule.assert_called_once_with(
            min_size='3@30 16 * * 1-5:4@00 17 * * 1-5',
            desired_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            max_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            group_name=new_group.name,
            hostclass=None
        )

    def test_bg_timed_autoscaling_nd(self):
        '''Blue/green doesn't bother with timed autoscaling for non-deployable hostclasses'''
        ami = MagicMock()
        ami.name = "mhctimedautoscalenodeploy 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhctimedautoscalenodeploy")
        self._disco_autoscale.get_existing_group.side_effect = [None, new_group]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_autoscale.get_instances.return_value = instances
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.test_ami(ami,
                                                 deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                 dry_run=False))

        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_called_once_with(
            [{
                'ami': 'ami-12345678',
                'sequence': 1,
                'deployable': 'no',
                'integration_test': None,
                'smoke_test': 'no',
                'hostclass': 'mhctimedautoscalenodeploy',
                'min_size': 3,
                'desired_size': 6,
                'max_size': 6
            }],
            create_if_exists=True,
            testing=True
        )
        self._disco_aws.create_scaling_schedule.assert_not_called()

    def test_integration_tests_with_elb(self):
        '''Integration tests should wait for ELB'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.get_host = MagicMock()
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertTrue(self._ci_deploy.run_integration_tests(ami, True))
        self._disco_elb.wait_for_instance_health_state.assert_called_with(hostclass="mhcbluegreen",
                                                                          testing=True)

    def test_integration_tests_with_elb_timeout(self):
        '''Integration tests should fail if they can't wait for ELB'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._disco_elb.wait_for_instance_health_state.side_effect = TimeoutError
        self.assertFalse(self._ci_deploy.run_integration_tests(ami, True))
        self._disco_elb.wait_for_instance_health_state.assert_called_with(hostclass="mhcbluegreen",
                                                                          testing=True)

    def test_nodeploy_ami_success(self):
        '''No deploy instances are promoted and autoscaling updated, when smoketest passes'''
        ami = MagicMock()
        ami.name = "mhcscarey 1 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'tested')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, 1)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no',
                    'min_size': 1, 'integration_test': None, 'desired_size': 2,
                    'smoke_test': 'no', 'max_size': 2, 'hostclass': 'mhcscarey'}],
                  testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no',
                    'min_size': 1, 'desired_size': 1, 'max_size': 1,
                    'integration_test': None, 'smoke_test': 'no',
                    'hostclass': 'mhcscarey'}])])

    def test_nodeploy_no_dict(self):
        '''Instance not in pipeline is still tested and promoted'''
        ami = MagicMock()
        ami.name = "mhcnewscarey 1 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.handle_nodeploy_ami(ami, pipeline_dict=None, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'tested')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, 1)
        self._disco_aws.spinup.assert_called_once_with(
            [{'ami': 'ami-12345678', 'sequence': 1, 'min_size': 0, 'desired_size': 1,
              'smoke_test': 'no', 'max_size': 1, 'hostclass': 'mhcnewscarey'}], testing=True)
        self._disco_autoscale.delete_groups.assert_called_once_with(hostclass='mhcnewscarey', force=True)

    def test_nodeploy_ami_failure(self):
        '''No deploy instances are failed and not promoted when smoketest fails'''
        ami = MagicMock()
        ami.name = "mhcscarey 1 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=False)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'failed')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, 1)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no',
                    'min_size': 1, 'integration_test': None, 'desired_size': 2,
                    'smoke_test': 'no', 'max_size': 2, 'hostclass': 'mhcscarey'}],
                  testing=True),
             call([{'sequence': 1, 'deployable': 'no', 'min_size': 1,
                    'integration_test': None, 'desired_size': 1, 'smoke_test': 'no',
                    'max_size': 1, 'hostclass': 'mhcscarey'}])])

    def test_smoketest_ami_success(self):
        '''Smoketest instances are promoted and autoscaling updated on success'''
        ami = MagicMock()
        ami.name = "mhcsmokey 1 2"
        ami.id = "ami-12345678"
        self._existing_group.desired_capacity = 2
        self._existing_group.max_size = 2
        self._existing_group.min_size = 2
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'tested')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, self._existing_group.desired_capacity)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': None, 'desired_size': 4,
                    'smoke_test': 'no', 'max_size': 4, 'hostclass': 'mhcsmokey'}]),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': None, 'desired_size': 2,
                    'smoke_test': 'no', 'max_size': 2, 'hostclass': 'mhcsmokey'}])])

    def test_smoketest_ami_failure(self):
        '''Smoketest instances are failed and autoscaling updated on failure'''
        ami = MagicMock()
        ami.name = "mhcsmokey 1 2"
        ami.id = "ami-12345678"
        self._existing_group.desired_capacity = 2
        self._existing_group.max_size = 2
        self._existing_group.min_size = 2
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=False)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'failed')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, self._existing_group.desired_capacity)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': None, 'desired_size': 4,
                    'smoke_test': 'no', 'max_size': 4, 'hostclass': 'mhcsmokey'}]),
             call([{'deployable': 'yes', 'sequence': 1, 'min_size': 2, 'integration_test': None,
                    'desired_size': 2, 'max_size': 2, 'hostclass': 'mhcsmokey',
                    'smoke_test': 'no'}])])

    def test_timed_autoscaling_ami_success(self):
        '''Timed autoscaling instances are promoted and correct autoscaling sizes updated on success'''
        ami = MagicMock()
        ami.name = "mhctimedautoscale 1 2"
        ami.id = "ami-12345678"
        self._existing_group.desired_capacity = 2
        self._existing_group.max_size = 2
        self._existing_group.min_size = 2
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'tested')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, self._existing_group.desired_capacity)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 4, 'max_size': 4}]),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 2, 'max_size': 2}])])
        self._disco_aws.create_scaling_schedule.assert_called_once_with(
            min_size='3@30 16 * * 1-5:4@00 17 * * 1-5',
            desired_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            max_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            group_name=None,
            hostclass='mhctimedautoscale'
        )

    def test_timed_autoscaling_ami_success_nd(self):
        '''Timed autoscaling is not set for non-deployable hostclasses'''
        ami = MagicMock()
        ami.name = "mhctimedautoscalenodeploy 1 2"
        ami.id = "ami-12345678"
        self._existing_group.desired_capacity = 2
        self._existing_group.max_size = 2
        self._existing_group.min_size = 2
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_with(ami, 'tested')
        self._ci_deploy.wait_for_smoketests.assert_called_with(ami.id, self._existing_group.desired_capacity)
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscalenodeploy',
                    'min_size': 2, 'desired_size': 4, 'max_size': 4}], testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscalenodeploy',
                    'min_size': 2, 'desired_size': 2, 'max_size': 2}])])
        self._disco_aws.create_scaling_schedule.assert_called_once_with(
            min_size='3@30 16 * * 1-5:4@00 17 * * 1-5',
            desired_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            max_size='5@30 16 * * 1-5:6@00 17 * * 1-5',
            group_name=None,
            hostclass='mhctimedautoscalenodeploy'
        )

    def test_set_maintenance_mode_on(self):
        '''_set_maintenance_mode makes expected remotecmd call'''
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(0, ""))
        self._ci_deploy._set_maintenance_mode(hostclass="mhcfoo", instances=["i-1"], mode_on=True)
        self._ci_deploy._disco_aws.remotecmd.assert_called_with(
            "i-1", ["sudo", "/opt/wgen/bin/maintenance-mode.sh", "on"],
            user="test_user", nothrow=True)

    def test_set_maintenance_mode_error(self):
        '''_set_maintenance_mode handles errors'''
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(1, ""))
        self.assertRaises(MaintenanceModeError, self._ci_deploy._set_maintenance_mode,
                          hostclass="foo", instances=["i-1"], mode_on=False)
        self._ci_deploy._disco_aws.remotecmd.assert_called_with(
            "i-1", ["sudo", "/opt/wgen/bin/maintenance-mode.sh", "off"],
            user="test_user", nothrow=True)

    def test_get_latest_other_image_id_1(self):
        '''_get_latest_other_image_id uses amis of old deployed instances'''
        ami = self.mock_ami("mhcabc 1")
        inst2 = self.mock_instance()
        inst2.image_id = ami.id
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst2])
        self._ci_deploy._disco_bake.get_amis = MagicMock(return_value=[ami])
        self.assertEqual(self._ci_deploy._get_latest_other_image_id('ami-11112222'), ami.id)
        self._ci_deploy._disco_bake.get_amis.assert_called_with(image_ids=[inst2.image_id])

    def test_get_latest_other_image_id_2(self):
        '''_get_latest_other_image_id returns latest of multiple amis'''
        amis = [self.mock_ami("mhcabc 1"), self.mock_ami("mhcabc 3"), self.mock_ami("mhcabc 2")]
        insts = [self.mock_instance() for _ in range(3)]
        for index in range(3):
            insts[index].image_id = amis[index].id
        self._ci_deploy._get_old_instances = MagicMock(return_value=insts)
        self._ci_deploy._disco_bake.get_amis = MagicMock(return_value=amis)
        self.assertEqual(self._ci_deploy._get_latest_other_image_id('ami-11112222'), amis[1].id)

    def test_maintenance_mode_failure(self):
        '''Test that we handle maintenance mode failure appropriately'''
        ami = MagicMock()
        ami.name = "mhcintegrated 1 2"
        ami.id = "ami-12345678"
        inst1 = self.mock_instance()
        inst2 = self.mock_instance()
        self._existing_group.desired_capacity = 2
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst1])
        self._ci_deploy._get_new_instances = MagicMock(return_value=[inst2])
        self._ci_deploy.get_host = MagicMock()
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(1, ''))
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._ci_deploy._get_old_instances.assert_called_with(ami.id)
        self._ci_deploy._disco_aws.terminate.assert_has_calls(
            [call([inst1]), call([inst2], use_autoscaling=True)])

    def test_pre_test_failure(self):
        '''Test that an exception is raised if the pre-test fails'''
        ami = self.mock_ami("mhcintegrated 1 2")
        self._existing_group.desired_capacity = 2
        self._ci_deploy.run_integration_tests = MagicMock(return_value=False)
        self.assertRaises(Exception, self._ci_deploy.test_ami, ami, dry_run=False)

    def test_get_host(self):
        '''get_host returns a host for the testing hostclass'''
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=["i-12345678"])
        self.assertEqual(self._ci_deploy.get_host(['test_hostclass']), "i-12345678")
        self.assertEqual(self._disco_aws.smoketest_once.call_count, 1)

    def test_get_host_raises_on_failure(self):
        '''get_host raises an IntegrationTestError when a host can not be found'''
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=["i-12345678"])
        self._disco_aws.smoketest_once = MagicMock(side_effect=TimeoutError)
        self.assertRaises(IntegrationTestError, self._ci_deploy.get_host, ['test_hostclass'])

    def test_run_integration_tests_command(self):
        '''run_integration_tests runs the correct command on the correct instance'''
        ami = self.mock_ami("mhcintegrated 1 2")
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(0, ""))
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=["i-12345678"])
        self.assertEqual(self._ci_deploy.run_integration_tests(ami), True)
        self._ci_deploy._disco_aws.remotecmd.assert_called_with(
            "i-12345678", ["test_command", "foo_service"],
            user="test_user", nothrow=True)

    def test_run_integration_tests_get_host_fail(self):
        '''run_integration_tests raises exception when a get_host fails to find a host'''
        ami = self.mock_ami("mhcintegrated 1 2")
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(0, ""))
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=[])
        self.assertRaises(IntegrationTestError, self._ci_deploy.run_integration_tests, ami)

    def test_run_integration_tests_success(self):
        '''Test that handle run_tests success appropriately'''
        ami = self.mock_ami("mhcintegrated 1 2")
        inst1 = self.mock_instance()
        inst2 = self.mock_instance()
        self._existing_group.desired_capacity = 2
        self._ci_deploy._set_maintenance_mode = MagicMock(return_value=True)
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst1])
        self._ci_deploy._get_new_instances = MagicMock(return_value=[inst2])
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._ci_deploy._get_old_instances.assert_called_with(ami.id)
        self._ci_deploy._disco_aws.terminate.assert_has_calls(
            [call([inst1], use_autoscaling=True)])

    def test_wait_for_smoketests_fail(self):
        '''Test that handle smoketest failure appropriately'''
        ami = self.mock_ami("mhcintegrated 1 2")
        inst1 = self.mock_instance()
        inst2 = self.mock_instance()
        self._existing_group.desired_capacity = 2
        self._ci_deploy._set_maintenance_mode = MagicMock(return_value=True)
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=False)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst1])
        self._ci_deploy._get_new_instances = MagicMock(return_value=[inst2])
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._ci_deploy._get_new_instances.assert_called_with(ami.id)
        self._ci_deploy._disco_aws.terminate.assert_has_calls(
            [call([inst2], use_autoscaling=True)])

    def test_run_integration_tests_fail(self):
        '''Test that run_integration_tests handles failure appropriately'''
        ami = self.mock_ami("mhcintegrated 1 2")
        inst1 = self.mock_instance()
        inst2 = self.mock_instance()
        self._existing_group.desired_capacity = 2
        self._ci_deploy._set_maintenance_mode = MagicMock(return_value=True)
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(side_effect=[True, False])
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst1])
        self._ci_deploy._get_new_instances = MagicMock(return_value=[inst2])
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._ci_deploy._get_new_instances.assert_called_with(ami.id)
        self._ci_deploy._disco_aws.terminate.assert_has_calls(
            [call([inst2], use_autoscaling=True)])
        self.assertEqual(self._ci_deploy._set_maintenance_mode.call_count, 2)

    def test_run_integration_tests_fail_fallback(self):
        '''Test that run_integration_tests handles failure with fallback amis'''
        ami1 = self.mock_ami("mhcintegrated 1")
        inst1 = self.mock_instance()
        inst1.image_id = ami1.id
        ami2 = self.mock_ami("mhcintegrated 2")
        inst2 = self.mock_instance()
        inst2.image_id = ami2.id
        self._existing_group.desired_capacity = 2
        self._existing_group.max_size = 2
        self._ci_deploy._set_maintenance_mode = MagicMock(return_value=True)
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(side_effect=[True, False])
        self._ci_deploy._get_old_instances = MagicMock(return_value=[inst1])
        self._ci_deploy._get_new_instances = MagicMock(return_value=[inst2])
        self._ci_deploy._get_latest_other_image_id = MagicMock(return_value=ami1.id)
        self._ci_deploy.test_ami(ami2, dry_run=False)
        self._disco_aws.spinup.assert_has_calls(
            [
                call([{'ami': ami2.id, 'sequence': 1, 'deployable': 'yes',
                       'min_size': 2, 'integration_test': 'foo_service', 'desired_size': 4,
                       'smoke_test': 'no', 'max_size': 4, 'hostclass': 'mhcintegrated'}]),
                call([{'ami': ami1.id, 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
                       'integration_test': 'foo_service', 'desired_size': 2, 'smoke_test': 'no',
                       'max_size': 2, 'hostclass': 'mhcintegrated'}])])

    def test_ami_of_non_pipeline_hostclass(self):
        '''Test test_ami handling of non-pipeline hostclass'''
        ami = self.mock_ami("mhcbar 1")
        self._existing_group.desired_capacity = 2
        self._ci_deploy.handle_nodeploy_ami = MagicMock()
        self._ci_deploy.test_ami(ami, dry_run=False)
        self._ci_deploy.handle_nodeploy_ami.assert_has_calls([
            call(
                ami,
                pipeline_dict=None,
                dry_run=False,
                old_group=self._existing_group
            )
        ])

    def test_update_ami_not_in_pipeline(self):
        '''Test update_ami handling of non-pipeline hostclass'''
        ami = self.mock_ami("mhcbar 1")
        self._ci_deploy.is_deployable = MagicMock()
        self._ci_deploy.update_ami(ami, dry_run=False)
        self.assertEqual(self._ci_deploy.is_deployable.call_count, 0)

    def test_update_ami_not_in_autoscale_deploy(self):
        '''Test update_ami handling new deployable hostclass'''
        ami = self.mock_ami("mhcsmokey 1")
        self._ci_deploy._disco_autoscale.get_existing_group = MagicMock(return_value=None)
        self._ci_deploy.handle_tested_ami = MagicMock()
        self._ci_deploy.update_ami(ami, dry_run=False)
        self._ci_deploy._disco_autoscale.get_existing_group.assert_called_with("mhcsmokey")
        self._ci_deploy.handle_tested_ami.assert_called_with(
            ami,
            pipeline_dict={
                'min_size': 2,
                'integration_test': None,
                'deployable': 'yes',
                'desired_size': 2,
                'hostclass': 'mhcsmokey'
            },
            dry_run=False,
            old_group=None
        )

    def test_update_ami_not_in_autoscale_nodeploy(self):
        '''Test update_ami handling new non-deployable hostclass'''
        ami = self.mock_ami("mhcscarey 1")
        self._ci_deploy.is_deployable = MagicMock(return_value=False)
        self._ci_deploy._disco_autoscale.get_existing_group = MagicMock(return_value=None)
        self._ci_deploy.handle_nodeploy_ami = MagicMock()
        self._ci_deploy.update_ami(ami, dry_run=False)
        self.assertEqual(self._ci_deploy.is_deployable.call_count, 1)
        self._ci_deploy._disco_autoscale.get_existing_group.assert_called_with("mhcscarey")
        self._ci_deploy.handle_nodeploy_ami.assert_called_with(
            ami,
            pipeline_dict={
                'min_size': 1,
                'integration_test': None,
                'deployable': 'no',
                'desired_size': 1,
                'hostclass': 'mhcscarey'
            },
            dry_run=False,
            old_group=None
        )

    def test_test_with_amis(self):
        '''Test test with amis'''
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)

    def test_test_wo_amis(self):
        '''Test test without amis'''
        self._ci_deploy.get_test_amis = MagicMock(return_value=[])
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.test_ami.call_count, 0)

    def test_update_with_amis(self):
        '''Test update with amis'''
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)

    def test_update_wo_amis(self):
        '''Test update without amis'''
        self._ci_deploy.get_update_amis = MagicMock(return_value=[])
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.update_ami.call_count, 0)

    def test_pending_ami(self):
        '''Ensure pending AMIs are not considered for deployment'''
        expected_ami = self.add_ami('mhcfoo 10', 'untested', 'pending')
        latest_ami = self._ci_deploy.get_latest_untested_amis()['mhcfoo']
        self.assertNotEqual(expected_ami.name, latest_ami.name)

    def test_hostclass_specific_test_host(self):
        '''Tests that hostclass specific test host is returned'''
        expected_hostclass = "another_test_hostclass"
        actual_hostclass = self._ci_deploy.hostclass_option("hostclass_being_tested",
                                                            "test_hostclass")
        self.assertEqual(expected_hostclass, actual_hostclass)
