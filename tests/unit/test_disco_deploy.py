"""
Tests of disco_bake
"""
from __future__ import print_function

import random
from unittest import TestCase
from datetime import datetime, timedelta

import boto.ec2.instance
import requests
import requests_mock
from mock import MagicMock, create_autospec, call, patch, ANY

from disco_aws_automation import DiscoDeploy, DiscoAWS, DiscoGroup, DiscoBake, DiscoELB, DiscoSSM
from disco_aws_automation.disco_constants import DEPLOYMENT_STRATEGY_BLUE_GREEN
from disco_aws_automation.disco_deploy import DiscoDeployTestHelper, DiscoDeployUpdateHelper
from disco_aws_automation.exceptions import (
    TimeoutError,
    IntegrationTestError,
    TooManyAutoscalingGroups,
    UnknownDeploymentStrategyException
)
from tests.helpers.patch_disco_aws import get_mock_config

# Don't limit number of tests
# pylint: disable=R0904

MOCK_PIPELINE_DEFINITION = [
    {
        'hostclass': 'mhcintegrated',
        'min_size': "1",
        'desired_size': "1",
        'integration_test': 'foo_service',
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcbluegreen',
        'min_size': "2",
        'desired_size': "2",
        'integration_test': 'blue_green_service',
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcbluegreennondeployable',
        'min_size': "1",
        'desired_size': "1",
        'integration_test': 'blue_green_service',
        'deployable': 'no'
    },
    {
        'hostclass': 'mhcsmokey',
        'min_size': "2",
        'desired_size': "2",
        'integration_test': None,
        'deployable': 'yes'
    },
    {
        'hostclass': 'mhcscarey',
        'min_size': "1",
        'desired_size': "1",
        'integration_test': None,
        'deployable': 'no'
    },
    {
        'hostclass': 'mhcfoo',
        'min_size': "1",
        'desired_size': "1",
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
    },
    {
        'hostclass': 'mhcssmdocs',
        'min_size': "1",
        'desired_size': "1",
        'integration_test': "ssm_service",
        'deployable': 'no'
    }
]

SOCIFY_API_BASE = 'https://socify-ci.aws.wgen.net'
SSM_DOC_TESTING_MODE = "fake_ssm_doc_testing_mode"
SSM_DOC_INTEGRATION_TESTS = "fake_ssm_doc_integration_tests"

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
    },
    "mhcssmdocs": {
        "deployment_strategy": DEPLOYMENT_STRATEGY_BLUE_GREEN,
        "ssm_doc_testing_mode": SSM_DOC_TESTING_MODE,
        "ssm_doc_integration_tests": SSM_DOC_INTEGRATION_TESTS
    },
    "socify": {
        'socify_baseurl': SOCIFY_API_BASE
    }
}


# Too many tests is probably not a bad thing
# pylint: disable=too-many-lines
class DiscoDeployTests(TestCase):
    '''Test DiscoDeploy class'''

    # This tells a parallel nose run to share this class's fixtures rather than run setUp in each process.
    # Useful for when lengthy setUp runs can cause a parallel nose run to time out.
    _multiprocess_shared_ = True

    def mock_ami(self, name, stage=None, state=u'available', is_private=False):
        '''Create a mock AMI'''
        ami = create_autospec(boto.ec2.image.Image)
        ami.name = name
        ami.tags = {"stage": stage, "is_private": str(is_private)}
        # ami.tags.get = MagicMock(return_value=stage)
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

    def add_ami(self, name, stage, state=u'available', is_private=False):
        '''Add one Instance AMI Mock to an AMI list'''
        ami = self.mock_ami(name, stage, state, is_private)
        assert ami.name == name
        assert ami.tags.get('stage') == stage
        assert ami.tags.get('is_private') == str(is_private)
        self._amis.append(ami)
        self._amis_by_name[ami.name] = ami
        return ami

    def init_latest_running_amis(self):
        '''Create the mock result for DiscoDeploy.get_latest_running_amis'''
        amis = {
            "mhcintegrated": self._amis_by_name['mhcintegrated 2'],
            "mhcfoo": self._amis_by_name['mhcfoo 4'],
            "mhcbluegreen": self._amis_by_name['mhcbluegreen 1'],
            "mhcbluegreennondeployable": self._amis_by_name['mhcbluegreennondeployable 1'],
            "mhcbar": self._amis_by_name['mhcbar 3']
        }
        self._real_get_latest_running_amis = self._ci_deploy.get_latest_running_amis
        self._ci_deploy.get_latest_running_amis = MagicMock(return_value=amis)

    def setUp(self):
        self._environment_name = "foo"
        self._disco_group = create_autospec(DiscoGroup, instance=True)
        self._disco_elb = create_autospec(DiscoELB, instance=True)
        self._disco_aws = create_autospec(DiscoAWS, instance=True)
        self._disco_ssm = create_autospec(DiscoSSM, instance=True)
        self._disco_aws.environment_name = "test_env"
        self._test_aws = self._disco_aws
        self._existing_group = self.mock_group("mhcfoo")
        self._disco_group.get_existing_group.return_value = self._existing_group.__dict__
        self._disco_bake = MagicMock()
        self._disco_bake.promote_ami = MagicMock()
        self._disco_bake.ami_stages = MagicMock(return_value=['untested', 'failed', 'tested'])
        self._disco_bake.get_ami_creation_time = DiscoBake.extract_ami_creation_time_from_ami_name
        self._ci_deploy = DiscoDeploy(
            self._disco_aws, self._test_aws, self._disco_bake, self._disco_group, self._disco_elb,
            self._disco_ssm, pipeline_definition=MOCK_PIPELINE_DEFINITION, ami=None, hostclass=None,
            allow_any_hostclass=False, config=get_mock_config(MOCK_CONFIG_DEFINITON))
        self._ci_deploy._disco_aws.terminate = MagicMock()
        self._amis = []
        self._amis_by_name = {}
        self.add_ami('mhcfoo 1', 'untested')
        self.add_ami('mhcbar 2', 'tested')
        self.add_ami('mhcbar 3', 'tested', is_private=True)
        self.add_ami('mhcfoo 4', 'tested')
        self.add_ami('mhcfoo 5', None)
        self.add_ami('mhcbar 1', 'tested')
        self.add_ami('mhcfoo 2', 'tested')
        self.add_ami('mhcfoo 3', 'tested')
        self.add_ami('mhcfoo 6', 'untested')
        self.add_ami('mhcnew 1', 'untested')
        self.add_ami('mhcfoo 7', 'failed')
        self.add_ami('mhcfoo 8', 'untested', is_private=True)
        self.add_ami('mhcfoo 9', None, is_private=True)
        self.add_ami('mhcfoo 10', 'tested', is_private=True)
        self.add_ami('mhcfoo 11', 'failed', is_private=True)
        self.add_ami('mhcintegrated 1', None)
        self.add_ami('mhcintegrated 2', 'tested')
        self.add_ami('mhcintegrated 3', None)
        self.add_ami('mhcbluegreen 1', 'tested')
        self.add_ami('mhcbluegreen 2', 'untested')
        self.add_ami('mhcbluegreennondeployable 1', 'tested')
        self.add_ami('mhcbluegreennondeployable 2', 'untested')
        self.add_ami('mhctimedautoscale 1', 'untested')
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=self._amis)
        self.init_latest_running_amis()

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
                         [self._amis_by_name['mhcbar 2'], self._amis_by_name['mhcbar 3'],
                          self._amis_by_name['mhcbar 1']])

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
                          self._amis_by_name["mhcfoo 8"],
                          self._amis_by_name["mhcfoo 9"],
                          self._amis_by_name["mhcfoo 10"],
                          self._amis_by_name["mhcfoo 11"],
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
                         [self._amis_by_name['mhcbar 2'], self._amis_by_name['mhcbar 3'],
                          self._amis_by_name['mhcbar 1']])

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
                          self._amis_by_name["mhcfoo 8"],
                          self._amis_by_name["mhcfoo 9"],
                          self._amis_by_name["mhcfoo 10"],
                          self._amis_by_name["mhcfoo 11"],
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
        '''Tests that get_latest_untested_amis() returns non private untested amis'''
        self.assertEqual(self._ci_deploy.get_latest_untested_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 6'])

    def test_get_latest_untagged_amis_works(self):
        '''Tests that get_latest_untagged_amis() returns non private untagged amis'''
        self.assertEqual(self._ci_deploy.get_latest_untagged_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 5'])

    def test_get_latest_tested_amis_works_inc(self):
        '''Tests that get_latest_tested_amis() returns non private latest tested amis (inc)'''
        self.assertEqual(self._ci_deploy.get_latest_tested_amis()['mhcfoo'],
                         self._amis_by_name['mhcfoo 4'])

    def test_get_latest_tested_amis_works_dec(self):
        '''Tests that get_latest_tested_amis() returns non private latest tested amis (dec)'''
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
        '''Tests that get_latest_failed_amis() returns non private latest failed amis'''
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
        '''
        Tests that we can find the next non private untested ami to test
        for each hostclass restricted to pipeline
        '''
        self.assertEqual([ami.name for ami in self._ci_deploy.get_test_amis()],
                         ['mhcfoo 6', 'mhcbluegreennondeployable 2',
                          'mhcbluegreen 2', 'mhctimedautoscale 1'])

    def test_get_failed_amis(self):
        '''Tests that we can find the next non private failed ami to test for each hostclass'''
        self.assertEqual([ami.name for ami in self._ci_deploy.get_failed_amis()],
                         ['mhcfoo 7'])

    def test_get_latest_running_amis(self):
        '''get_latest_running_amis returns the latest non private running AMIs'''
        amis = [self._amis_by_name['mhcintegrated 1'], self._amis_by_name['mhcintegrated 2'],
                self._amis_by_name['mhcbar 2'], self._amis_by_name['mhcbar 3']]
        self._ci_deploy._disco_bake.get_amis = MagicMock(return_value=amis)
        self._ci_deploy.get_latest_running_amis = self._real_get_latest_running_amis
        latest_running_amis = self._ci_deploy.get_latest_running_amis()
        self.assertEqual(latest_running_amis['mhcintegrated'], amis[1])
        self.assertEqual(latest_running_amis['mhcbar'], amis[2])

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
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2,
                                                                           group_name=None, launch_time=None)
        self.assertEqual(self._ci_deploy._disco_aws.smoketest.call_count, 0)

    def test_wait_for_smoketests_does_smoke(self):
        '''Tests that we do smoketests'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(return_value=True)
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2), True)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2,
                                                                           group_name=None, launch_time=None)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'], None, None)
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_wait_for_smoketests_does_smoke_time(self):
        '''Tests that we handle smoketest Timeout'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(side_effect=TimeoutError())
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2), False)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2,
                                                                           group_name=None, launch_time=None)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'], None, None)
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_wait_for_smoketests_asg_does_smoke(self):
        '''Tests that we do smoketests'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(return_value=True)
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2, group_name='test_group'),
                         True)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2,
                                                                           group_name='test_group',
                                                                           launch_time=None)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'], 'test_group',
                                                                          None)
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_wait_for_smoketests_date_does_smoke(self):
        '''Tests that we do smoketests'''
        self._ci_deploy._disco_aws.wait_for_autoscaling = MagicMock()
        self._ci_deploy._disco_aws.smoketest = MagicMock(return_value=True)
        self._ci_deploy._disco_aws.instances_from_amis = MagicMock(return_value=['a', 'b'])
        now = datetime.utcnow()
        self.assertEqual(self._ci_deploy.wait_for_smoketests('ami-12345678', 2, launch_time=now), True)
        self._ci_deploy._disco_aws.wait_for_autoscaling.assert_called_with('ami-12345678', 2,
                                                                           group_name=None, launch_time=now)
        self._ci_deploy._disco_aws.instances_from_amis.assert_called_with(['ami-12345678'], None,
                                                                          now)
        self._ci_deploy._disco_aws.smoketest.assert_called_with(['a', 'b'])

    def test_promote_no_throw(self):
        '''_promote_ami swallows exceptions'''
        self._ci_deploy._disco_bake.promote_ami = MagicMock(side_effect=Exception())
        ami = MagicMock()
        self._ci_deploy._promote_ami(ami, "super")

    def test_blue_green_dry_run(self):
        """We don't call spinup in a blue/green dry_run"""
        self.assertIsNone(self._ci_deploy.handle_blue_green_ami(MagicMock(), dry_run=True))
        self.assertEqual(self._disco_aws.spinup.call_count, 0)

    def test_bg_deploy_works_with_no_orig_group(self):
        '''Blue/green deploy works with no existing group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcbluegreen")
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'max_size': 2,
                    'min_size': 2, 'integration_test': "blue_green_service", 'desired_size': 2,
                    'smoke_test': 'no', 'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'desired_size': 2, 'max_size': 2,
                    'integration_test': "blue_green_service", 'smoke_test': 'no',
                    'hostclass': 'mhcbluegreen'}], group_name=new_group.name)])
        self._disco_group.delete_groups.assert_not_called()
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_nodeploy_works(self):
        '''Blue/green deploy works when the ami is not deployable'''
        ami = MagicMock()
        ami.name = "mhcbluegreennondeployable 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcbluegreennondeployable")
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 1, 'max_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreennondeployable'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami, dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4,
                    'smoke_test': 'no', 'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4,
                    'integration_test': "blue_green_service", 'smoke_test': 'no',
                    'hostclass': 'mhcbluegreen'}], group_name=new_group.name)])
        self._disco_group.delete_groups.assert_called_once_with(group_name=old_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_deploy_with_bad_new_group_name(self):
        '''Blue/green deploy throws an exception if it gets the wrong new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        group = self.mock_group("mhcbluegreen")
        self._disco_group.get_existing_group.return_value = group.__dict__
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'failed')
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        instance_ids = [inst.instance_id for inst in instances]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertRaises(TimeoutError, self._ci_deploy.test_ami, ami, dry_run=False)
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
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 3, 'max_size': 4, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)
        self._disco_elb.delete_elb.assert_called_once_with("mhcbluegreen", testing=True)

    def test_bg_with_hc_not_in_pl_and_no_group(self):
        '''Blue/green is non-deployable if hostclass is not in pipeline, and dies with no existing group'''
        ami = MagicMock()
        ami.name = "mhcfoo 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhcfoo")
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami,
                                                   deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                   dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with(
            [{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'no', 'min_size': 1,
              'integration_test': None, 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
              'hostclass': 'mhcfoo'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_hc_not_in_pl_and_group(self):
        '''Blue/green is non-deployable if hostclass is not in pipeline, dies, and updates existing group'''
        ami = MagicMock()
        ami.name = "mhcfoo 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        old_group = self.mock_group("mhcfoo", min_size=2, max_size=4, desired_size=3)
        new_group = self.mock_group("mhcfoo")
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (1, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami,
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
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_spinup_error_and_no_og(self):
        '''Blue/green can handle an an exception when spinning up the new ASG with no old group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        new_group = self.mock_group("mhcbluegreen")
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 2, 'max_size': 2, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_called_once_with(group_name=new_group.name, force=True)

    def test_bg_with_spinup_error_and_no_groups(self):
        '''Blue/green can handle an an exception when spinning up the new ASG with no groups'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        self._disco_group.get_existing_group.side_effect = [None, None]
        self.assertRaises(RuntimeError, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 2,
            'integration_test': "blue_green_service", 'desired_size': 2, 'max_size': 2, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_not_called()

    def test_bg_with_error_and_og_and_no_ng(self):
        '''Blue/green can handle an an exception when spinning up the new ASG w/ old group and no new group'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = Exception
        old_group = self.mock_group("mhcbluegreen")
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, None]
        self.assertRaises(Exception, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_not_called()

    def test_bg_with_too_many_autoscaling_groups(self):
        '''Blue/green can handle too many autoscaling groups error when spinning up a new ASG'''
        ami = MagicMock()
        ami.name = "mhcbluegreen 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        self._disco_aws.spinup.side_effect = TooManyAutoscalingGroups
        old_group = self.mock_group("mhcbluegreen")
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, None]
        self.assertRaises(TooManyAutoscalingGroups, self._ci_deploy.test_ami, ami, dry_run=False)
        self._disco_bake.promote_ami.assert_not_called()
        self._disco_elb.wait_for_instance_health_state.assert_not_called()
        self._disco_aws.spinup.assert_called_once_with([{
            'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes', 'min_size': 1,
            'integration_test': "blue_green_service", 'desired_size': 1, 'max_size': 1, 'smoke_test': 'no',
            'hostclass': 'mhcbluegreen'}], testing=True, create_if_exists=True)
        self._disco_group.delete_groups.assert_not_called()

    def test_bg_timed_autoscaling(self):
        '''Blue/green can handle creating timed autoscaling actions'''
        ami = MagicMock()
        ami.name = "mhctimedautoscale 2"
        ami.id = "ami-12345678"
        self._ci_deploy.wait_for_smoketests = MagicMock(return_value=True)
        self._ci_deploy.run_integration_tests = MagicMock(return_value=True)
        new_group = self.mock_group("mhctimedautoscale")
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami,
                                                   deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                   dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 3, 'desired_size': 6, 'max_size': 6}], create_if_exists=True, testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 3, 'desired_size': 6, 'max_size': 6}],
                  group_name=new_group.name)])
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
        self._disco_group.get_existing_group.side_effect = [old_group.__dict__, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami,
                                                   deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
                                                   dry_run=False))
        self._disco_bake.promote_ami.assert_called_once_with(ami, 'tested')
        self._disco_aws.spinup.assert_has_calls(
            [call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4}], create_if_exists=True, testing=True),
             call([{'ami': 'ami-12345678', 'sequence': 1, 'deployable': 'yes',
                    'integration_test': None, 'smoke_test': 'no', 'hostclass': 'mhctimedautoscale',
                    'min_size': 2, 'desired_size': 3, 'max_size': 4}],
                  group_name=new_group.name)])
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
        self._disco_group.get_existing_group.side_effect = [None, new_group.__dict__]
        instances = [self.mock_instance(), self.mock_instance(), self.mock_instance()]
        self._disco_group.get_instances.return_value = [_i.__dict__ for _i in instances]
        self._disco_aws.instances.return_value = instances
        self._disco_aws.remotecmd.return_value = (0, "")
        self.assertIsNone(self._ci_deploy.test_ami(ami,
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

    def test_get_new_instances_with_launch_time(self):
        '''test get new instances using launch time'''
        now = datetime.utcnow()
        ami_id = "ami-12345678"

        inst1 = self.mock_instance()
        inst1.launch_time = str(now + timedelta(minutes=10))
        inst2 = self.mock_instance()
        inst2.launch_time = str(now - timedelta(days=1))
        instances = [inst1, inst2]

        self._disco_aws.instances = MagicMock(return_value=instances)
        self.assertEquals(self._ci_deploy._get_new_instances(ami_id, now), [inst1])

    def test_get_new_instances_no_launch_time(self):
        '''test get new instances without launch time'''
        now = datetime.utcnow()
        ami_id = "ami-12345678"

        inst1 = self.mock_instance()
        inst1.launch_time = str(now + timedelta(minutes=10))
        inst2 = self.mock_instance()
        inst2.launch_time = str(now - timedelta(days=1))
        instances = [inst1, inst2]

        self._disco_aws.instances = MagicMock(return_value=instances)
        self.assertEquals(self._ci_deploy._get_new_instances(ami_id), instances)

    def test_get_old_instances_with_launch_time(self):
        '''test get old instances using launch time'''
        now = datetime.utcnow()
        ami_id = "ami-12345678"

        inst1 = self.mock_instance()
        inst1.image_id = ami_id
        inst1.launch_time = str(now + timedelta(minutes=10))
        inst2 = self.mock_instance()
        inst2.image_id = ami_id
        inst2.launch_time = str(now - timedelta(days=1))
        instances = [inst1, inst2]

        self._disco_aws.instances = MagicMock(return_value=instances)
        self.assertEquals(self._ci_deploy._get_old_instances(ami_id, now), [inst2])

    def test_get_old_instances_no_launch_time(self):
        '''test get old instances without launch time'''
        now = datetime.utcnow()
        ami_id1 = "ami-12345678"
        ami_id2 = "ami-12345699"

        inst1 = self.mock_instance()
        inst1.image_id = ami_id1
        inst1.launch_time = str(now + timedelta(minutes=10))
        inst2 = self.mock_instance()
        inst2.image_id = ami_id2
        inst2.launch_time = str(now - timedelta(days=1))
        instances = [inst1, inst2]

        self._disco_aws.instances = MagicMock(return_value=instances)
        self.assertEquals(self._ci_deploy._get_old_instances(ami_id1), [inst2])

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

    def test_run_integration_tests_ssh(self):
        '''run_integration_tests runs the correct command on the correct instance via ssh'''
        ami = self.mock_ami("mhcintegrated 1 2")
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(0, ""))
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=["i-12345678"])
        self.assertEqual(self._ci_deploy.run_integration_tests(ami), True)
        self._ci_deploy._disco_aws.remotecmd.assert_called_with(
            "i-12345678", ["test_command", "foo_service"],
            user="test_user", nothrow=True)

    def test_run_integration_tests_ssm(self):
        '''run_integration_tests runs the correct command on the correct instance via ssm'''
        ami = self.mock_ami("mhcssmdocs 1 2")
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=[MagicMock(id="i-12345678")])
        self._ci_deploy._disco_ssm.execute.return_value = True
        self.assertEqual(self._ci_deploy.run_integration_tests(ami), True)
        self._ci_deploy._disco_ssm.execute.assert_called_with(
            instance_ids=["i-12345678"],
            document_name=SSM_DOC_INTEGRATION_TESTS,
            parameters={
                "command": ["test_command"],
                "test": ["ssm_service"],
                "user": ["test_user"]
            },
            comment=ANY
        )

    def test_setting_testing_mode_ssm(self):
        '''toggles testing mode correctly via ssm'''
        self._ci_deploy._disco_ssm.execute.return_value = True
        self.assertEqual(
            self._ci_deploy._set_testing_mode(
                "mhcssmdocs",
                [MagicMock(id="i-12345678")],
                True
            ),
            True
        )
        self._ci_deploy._disco_ssm.execute.assert_called_with(
            instance_ids=["i-12345678"],
            document_name=SSM_DOC_TESTING_MODE,
            parameters={
                "mode": ["on"]
            },
            comment=ANY
        )

    def test_setting_testing_mode_ssm_error(self):
        '''toggling testing mode fails if execute fails'''
        self._ci_deploy._disco_ssm.execute.return_value = False
        self.assertEqual(
            self._ci_deploy._set_testing_mode(
                "mhcssmdocs",
                [MagicMock(id="i-12345678")],
                True
            ),
            False
        )
        self._ci_deploy._disco_ssm.execute.assert_called_with(
            instance_ids=["i-12345678"],
            document_name=SSM_DOC_TESTING_MODE,
            parameters={
                "mode": ["on"]
            },
            comment=ANY
        )

    def test_run_integration_tests_get_host_fail(self):
        '''run_integration_tests raises exception when a get_host fails to find a host'''
        ami = self.mock_ami("mhcintegrated 1 2")
        self._ci_deploy._disco_aws.remotecmd = MagicMock(return_value=(0, ""))
        self._disco_aws.instances_from_hostclasses = MagicMock(return_value=[])
        self.assertRaises(IntegrationTestError, self._ci_deploy.run_integration_tests, ami)

    def test_update_ami_not_in_pipeline(self):
        '''Test update_ami handling of non-pipeline hostclass'''
        ami = self.mock_ami("mhcbar 1")
        self._ci_deploy.is_deployable = MagicMock()
        self.assertRaises(RuntimeError, self._ci_deploy.update_ami, ami, dry_run=False)
        self.assertEqual(self._ci_deploy.is_deployable.call_count, 0)

    def test_test_with_amis(self):
        '''Test test with amis'''
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)

    @requests_mock.Mocker()
    def test_test_with_amis_ticketid(self, mock_requests):
        '''Test test with amis and calls to socify'''
        self._ci_deploy.test_ami = MagicMock()
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        self._ci_deploy.test(ticket_id="AL-1102")
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_test_with_amis_ticketid_error(self, mock_requests):
        '''Test test with amis and calls to socify'''
        self._ci_deploy.test_ami = MagicMock(side_effect=RuntimeError())
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaises(RuntimeError):
            self._ci_deploy.test(ticket_id="AL-1102")

    @requests_mock.Mocker()
    def test_test_with_amis_validate_failed(self, mock_requests):
        '''Test test with amis and failed socify validate'''
        self._ci_deploy.test_ami = MagicMock()

        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Failed', 'err_msgs': ["Some error message"]}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaisesRegexp(RuntimeError,
                                     "The SOC validation of the associated Ticket and AMI failed."):
            self._ci_deploy.test(ticket_id="AL-1102")

        self.assertEqual(self._ci_deploy.test_ami.call_count, 0)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_test_with_amis_validate_error(self, mock_requests):
        '''Test test with amis and error returned by socify validate'''
        self._ci_deploy.test_ami = MagicMock()

        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", exc=requests.exceptions.ConnectTimeout)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaisesRegexp(RuntimeError,
                                     "The SOC validation of the associated Ticket and AMI failed."):
            self._ci_deploy.test(ticket_id="AL-1102")

        self.assertEqual(self._ci_deploy.test_ami.call_count, 0)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_test_with_amis_soc_event_error(self, mock_requests):
        '''Test test with amis and failed socify event'''
        self._ci_deploy.test_ami = MagicMock()
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_response = {
            'errorMessage': 'SOCIFY failed executing the event request'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_response, status_code=400)

        self._ci_deploy.test(ticket_id="AL-1102")
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)
        self.assertEqual(mock_requests.call_count, 2)

    def test_test_wo_amis(self):
        '''Test test without amis '''
        self._ci_deploy.get_test_amis = MagicMock(return_value=[])
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.test_ami.call_count, 0)

    def test_test_with_restrict_ami(self):
        '''Test test with specified restrict amis'''
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcbar 2'].id]
        amis = [self._amis_by_name['mhcbar 2']]
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=amis)
        self._ci_deploy.get_test_amis = MagicMock(return_value=[])
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.get_test_amis.call_count, 0)
        self._ci_deploy._disco_bake.list_amis.assert_called_with(ami_ids=[self._amis_by_name['mhcbar 2'].id])
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)

    def test_test_with_invalid_restrict_ami(self):
        '''Test test with specified restrict amis'''
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcbar 2'].id]
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=[])
        self._ci_deploy.get_test_amis = MagicMock(return_value=[])
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.get_test_amis.call_count, 0)
        self._ci_deploy._disco_bake.list_amis.assert_called_with(ami_ids=[self._amis_by_name['mhcbar 2'].id])
        self.assertEqual(self._ci_deploy.test_ami.call_count, 0)

    def test_test_wo_restrict_ami(self):
        '''Test test without specified restrict amis'''
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=[])
        self._ci_deploy.get_test_amis = MagicMock(return_value=[self._amis_by_name['mhcbar 2']])
        self._ci_deploy.test_ami = MagicMock()
        self._ci_deploy.test()
        self.assertEqual(self._ci_deploy.get_test_amis.call_count, 1)
        self.assertEqual(self._ci_deploy.test_ami.call_count, 1)

    def test_update_with_amis(self):
        '''Test update with amis'''
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)

    @requests_mock.Mocker()
    def test_update_with_amis_ticketid(self, mock_requests):
        '''Test update with amis and calls to socify'''
        self._ci_deploy.update_ami = MagicMock()
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        self._ci_deploy.update(ticket_id="AL-1102")
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_update_with_amis_ticketid_error(self, mock_requests):
        '''Test update with amis and calls to socify'''
        self._ci_deploy.update_ami = MagicMock(side_effect=RuntimeError())
        mock_validate_response = {
            'message': 'SOCIFY has successfully processed the validate request: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaises(RuntimeError):
            self._ci_deploy.update(ticket_id="AL-1102")

    @requests_mock.Mocker()
    def test_update_with_amis_validate_failed(self, mock_requests):
        '''Test update with amis and failed socify validate'''
        self._ci_deploy.update_ami = MagicMock()

        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Failed', 'err_msgs': ["Some error message"]}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaisesRegexp(RuntimeError,
                                     "The SOC validation of the associated Ticket and AMI failed."):
            self._ci_deploy.update(ticket_id="AL-1102")

        self.assertEqual(self._ci_deploy.update_ami.call_count, 0)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_update_with_amis_validate_error(self, mock_requests):
        '''Test test with amis and error returned from validate'''
        self._ci_deploy.update_ami = MagicMock()

        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", exc=requests.exceptions.ConnectTimeout)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaisesRegexp(RuntimeError,
                                     "The SOC validation of the associated Ticket and AMI failed."):
            self._ci_deploy.update(ticket_id="AL-1102")

        self.assertEqual(self._ci_deploy.update_ami.call_count, 0)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    def test_update_with_amis_soc_event_error(self, mock_requests):
        '''Test test with amis and error during Socify event'''
        self._ci_deploy.update_ami = MagicMock()
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_response = {
            'errorMessage': 'SOCIFY failed executing the event request'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_response, status_code=400)

        self._ci_deploy.update(ticket_id="AL-1102")
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)
        self.assertEqual(mock_requests.call_count, 2)

    @requests_mock.Mocker()
    @patch("disco_aws_automation.disco_deploy.DiscoDeployUpdateHelper._get_ami_to_deploy")
    def test_update_with_invalid_ami_soc_event(self, mock_requests, mock_get_ami):
        '''Test update with exception when getting ami and send error event to Socify'''
        mock_get_ami.side_effect = RuntimeError("Invalid amiId")
        mock_validate_response = {
            'message': 'SOCIFY-Mock has successfully processed the validate request:: DeployEvent',
            'result': {'status': 'Passed', 'err_msgs': []}
        }
        mock_event_response = {
            'message': 'SOCIFY-Mock has successfully processed the event: DeployEvent'
        }
        mock_requests.post(SOCIFY_API_BASE + "/validate", json=mock_validate_response, status_code=200)
        mock_requests.post(SOCIFY_API_BASE + "/event", json=mock_event_response)

        with self.assertRaisesRegexp(RuntimeError, "Invalid amiId"):
            self._ci_deploy.update(ticket_id="AL-1102")
        self.assertEqual(mock_requests.call_count, 1)

    def test_update_wo_amis(self):
        '''Test update without amis'''
        self._ci_deploy.get_update_amis = MagicMock(return_value=[])
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.update_ami.call_count, 0)

    def test_update_with_restrict_ami(self):
        '''Test test with specified restrict amis'''
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcbar 2'].id]
        amis = [self._amis_by_name['mhcbar 2']]
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=amis)
        self._ci_deploy.get_update_amis = MagicMock(return_value=[])
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.get_update_amis.call_count, 0)
        self._ci_deploy._disco_bake.list_amis.assert_called_with(ami_ids=[self._amis_by_name['mhcbar 2'].id])
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)

    def test_update_with_invalid_restrict_ami(self):
        '''Test update with specified restrict amis'''
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcbar 2'].id]
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=[])
        self._ci_deploy.get_update_amis = MagicMock(return_value=[])
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.get_update_amis.call_count, 0)
        self._ci_deploy._disco_bake.list_amis.assert_called_with(ami_ids=[self._amis_by_name['mhcbar 2'].id])
        self.assertEqual(self._ci_deploy.update_ami.call_count, 0)

    def test_update_wo_restrict_ami(self):
        '''Test update without specified restrict amis'''
        self._ci_deploy._disco_bake.list_amis = MagicMock(return_value=[])
        self._ci_deploy.get_update_amis = MagicMock(return_value=[self._amis_by_name['mhcbar 2']])
        self._ci_deploy.update_ami = MagicMock()
        self._ci_deploy.update()
        self.assertEqual(self._ci_deploy.get_update_amis.call_count, 1)
        self.assertEqual(self._ci_deploy.update_ami.call_count, 1)

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

    def test_correct_zero_pipeline_sizing(self):
        '''Tests that get deploy sizing corrects zero pipeline sizing'''
        post_deploy_pipeline = self._ci_deploy._generate_deploy_pipeline(
            pipeline_dict={
                'desired_size': "0",
                'min_size': "0",
                'max_size': "0",
            },
            old_group=None,
            ami=MagicMock(id='ami-1234567890')
        )

        self.assertEqual(post_deploy_pipeline['desired_size'], 1)
        self.assertEqual(post_deploy_pipeline['min_size'], 0)
        self.assertEqual(post_deploy_pipeline['max_size'], 1)

    def test_unsupported_strategy_test(self):
        """Tests exception for bad strategy with test_ami"""
        self.assertRaises(
            UnknownDeploymentStrategyException,
            self._ci_deploy.test_ami,
            ami=self._amis_by_name['mhcbar 2'],
            deployment_strategy="foobar",
            dry_run=False
        )

    def test_unsupported_strategy_update(self):
        """Tests exception for bad strategy with update_ami"""
        self.assertRaises(
            UnknownDeploymentStrategyException,
            self._ci_deploy.update_ami,
            ami=self._amis_by_name['mhcfoo 4'],
            deployment_strategy="foobar",
            dry_run=False
        )

    def test_deployable_option_in_test(self):
        """Tests that providing a deployable option overrides in test"""
        self._ci_deploy.handle_blue_green_ami = MagicMock()

        self._ci_deploy.test_ami(
            ami=self._amis_by_name['mhcfoo 4'],
            deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
            dry_run=False,
            force_deployable=False
        )

        self._ci_deploy.test_ami(
            ami=self._amis_by_name['mhcfoo 4'],
            deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
            dry_run=False,
            force_deployable=True
        )

        self._ci_deploy.handle_blue_green_ami.assert_has_calls([
            call(ANY, dry_run=ANY, old_group=ANY, pipeline_dict=ANY, run_tests=ANY, deployable=False),
            call(ANY, dry_run=ANY, old_group=ANY, pipeline_dict=ANY, run_tests=ANY, deployable=True)
        ])

    def test_deployable_option_in_update(self):
        """Tests that providing a deployable option overrides in update"""
        self._ci_deploy.handle_blue_green_ami = MagicMock()

        self._ci_deploy.update_ami(
            ami=self._amis_by_name['mhcfoo 4'],
            deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
            dry_run=False,
            force_deployable=False
        )

        self._ci_deploy.update_ami(
            ami=self._amis_by_name['mhcfoo 4'],
            deployment_strategy=DEPLOYMENT_STRATEGY_BLUE_GREEN,
            dry_run=False,
            force_deployable=True
        )

        self._ci_deploy.handle_blue_green_ami.assert_has_calls([
            call(ANY, dry_run=ANY, old_group=ANY, pipeline_dict=ANY, run_tests=ANY, deployable=False),
            call(ANY, dry_run=ANY, old_group=ANY, pipeline_dict=ANY, run_tests=ANY, deployable=True)
        ])

    def test_test_get_ami_to_deploy_hostclass(self):
        """Test DiscoDeployTestHelper get_ami_to_deploy for specific host return non private ami"""
        self._ci_deploy._restrict_hostclass = 'mhcfoo'
        disco_deploy_helper = DiscoDeployTestHelper(self._ci_deploy)
        ami = disco_deploy_helper._get_ami_to_deploy()
        self.assertEqual(ami, self._amis_by_name['mhcfoo 6'])

    def test_test_get_ami_to_deploy_private(self):
        """Test DiscoDeployTestHelper get_ami_to_deploy for specific private ami"""
        disco_deploy_helper = DiscoDeployTestHelper(self._ci_deploy)
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcfoo 8'].id]
        ami = disco_deploy_helper._get_ami_to_deploy()
        self.assertEqual(ami, self._amis_by_name['mhcfoo 8'])

    def test_update_get_ami_to_deploy_hostclass(self):
        """Test DiscoDeployUpdateHelper get_ami_to_deploy for specific host return non private ami"""
        self._ci_deploy._restrict_hostclass = 'mhcfoo'
        # Mark mhcfoo host deployable
        self._ci_deploy._hostclasses['mhcfoo']['deployable'] = 'yes'
        disco_deploy_helper = DiscoDeployUpdateHelper(self._ci_deploy)
        ami = disco_deploy_helper._get_ami_to_deploy()
        self.assertEqual(ami, self._amis_by_name['mhcfoo 5'])

    def test_update_get_ami_to_deploy_private(self):
        """Test DiscoDeployUpdateHelper get_ami_to_deploy for specific private ami"""
        disco_deploy_helper = DiscoDeployUpdateHelper(self._ci_deploy)
        self._ci_deploy._restrict_amis = [self._amis_by_name['mhcfoo 10'].id]
        ami = disco_deploy_helper._get_ami_to_deploy()
        self.assertEqual(ami, self._amis_by_name['mhcfoo 10'])
