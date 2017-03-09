'Contains DiscoDeploy class'

import copy
import logging
import random
import sys
from datetime import datetime

from ConfigParser import NoOptionError, NoSectionError
from boto.exception import EC2ResponseError

from . import DiscoBake
from .disco_config import read_config
from .disco_aws_util import get_instance_launch_time
from .exceptions import (
    TimeoutError,
    MaintenanceModeError,
    IntegrationTestError,
    SmokeTestError,
    TooManyAutoscalingGroups
)
from .disco_aws_util import is_truthy, size_as_minimum_int_or_none, size_as_maximum_int_or_none
from .disco_constants import (DEFAULT_CONFIG_SECTION, DEPLOYMENT_STRATEGY_BLUE_GREEN,
                              DEPLOYMENT_STRATEGY_CLASSIC)

logger = logging.getLogger(__name__)


def snap_to_range(val, mini, maxi):
    '''Returns a value snapped into [mini, maxi]'''
    return min(max(int(val), int(mini)), int(maxi))


class DiscoDeploy(object):
    '''DiscoDeploy takes care of testing, promoting and deploying the latests AMIs'''

    # pylint: disable=too-many-arguments
    def __init__(self, aws, test_aws, bake, autoscale, elb, pipeline_definition,
                 ami=None, hostclass=None, allow_any_hostclass=False, config=None):
        '''
        Constructor for DiscoDeploy

        :param aws a DiscoAWS instance to use
        :param test_aws DiscoAWS instance for integration tests. may be different environment than "aws" param
        :param bake a DiscoBake instance to use
        :param autoscale a DiscoAutoscale instance to use
        :param elb a DiscoELB instance to use
        :param pipeline_definition a list of dicts containing hostname, deployable and other pipeline values
        :param allow_any_hostclass do not restrict to hostclasses in the pipeline definition
        :param config: Configuration object to use.
        '''
        self._config = config or read_config()

        self._restrict_amis = [ami] if ami else None
        self._restrict_hostclass = hostclass
        self._disco_aws = aws
        self._test_aws = test_aws
        self._disco_bake = bake
        self._disco_autoscale = autoscale
        self._disco_elb = elb
        self._all_stage_amis = None
        self._hostclasses = self._get_hostclasses_from_pipeline_definition(pipeline_definition)
        self._allow_any_hostclass = allow_any_hostclass

    def _get_hostclasses_from_pipeline_definition(self, pipeline_definition):
        ''' Return hostclasses from pipeline definitions, validating numeric input '''
        return {entry["hostclass"]: entry for entry in pipeline_definition}

    def _filter_amis(self, amis):
        if self._restrict_amis:
            return [ami for ami in amis if ami.id in self._restrict_amis]
        elif self._restrict_hostclass:
            return [ami for ami in amis if DiscoBake.ami_hostclass(ami) == self._restrict_hostclass]
        elif not self._allow_any_hostclass:
            return [ami for ami in amis if DiscoBake.ami_hostclass(ami) in self._hostclasses]
        else:
            return amis

    @property
    def all_stage_amis(self):
        '''Returns AMIs filtered on AMI ids, hostclass and state == available'''
        if not self._all_stage_amis:
            self._all_stage_amis = [ami for ami in self._filter_amis(
                self._disco_bake.list_amis(ami_ids=self._restrict_amis)) if ami.state == u'available']
        return self._all_stage_amis

    def get_latest_ami_in_stage_dict(self, stage):
        '''Returns latest AMI for each hostclass in a specific stage

        :param stage If set filter by stage, else only return instance without tag
        '''
        latest_ami = {}
        for ami in self.all_stage_amis:
            if stage and ami.tags.get("stage") != stage:
                continue
            elif stage is None and ami.tags.get("stage") is not None:
                continue
            hostclass = DiscoBake.ami_hostclass(ami)
            old_ami = latest_ami.get(hostclass)
            new_time = self._disco_bake.get_ami_creation_time(ami)
            if not new_time:
                continue
            if not old_ami:
                latest_ami[hostclass] = ami
                continue
            old_time = self._disco_bake.get_ami_creation_time(old_ami)
            if old_time and (new_time > old_time):
                latest_ami[hostclass] = ami
        return latest_ami

    def get_latest_untested_amis(self):
        '''Returns latest untested AMI for each hostclass'''
        return self.get_latest_ami_in_stage_dict(self._disco_bake.ami_stages()[0])

    def get_latest_untagged_amis(self):
        '''Returns latest untagged AMI for each hostclass'''
        return self.get_latest_ami_in_stage_dict(None)

    def get_latest_tested_amis(self):
        '''Returns latest tested AMI for each hostclass'''
        return self.get_latest_ami_in_stage_dict(self._disco_bake.ami_stages()[-1])

    def get_latest_failed_amis(self):
        '''Returns latest failed AMI for each hostclass'''
        return self.get_latest_ami_in_stage_dict('failed')

    def get_items_newer_in_second_map(self, first, second):
        '''Returns AMIs from second dict which are newer than the corresponding item in the first dict'''
        return [ami for (hostclass, ami) in second.iteritems()
                if (first.get(hostclass) is None) or (
                    self._disco_bake.get_ami_creation_time(ami) >
                    self._disco_bake.get_ami_creation_time(first[hostclass]))]

    def get_newest_in_either_map(self, first, second):
        '''Returns AMIs which are newest for each hostclass'''
        newest_for_hostclass = first
        for (hostclass, ami) in second.iteritems():
            if hostclass not in newest_for_hostclass:
                newest_for_hostclass[hostclass] = ami
            elif (self._disco_bake.get_ami_creation_time(ami) >
                  self._disco_bake.get_ami_creation_time(first[hostclass])):
                newest_for_hostclass[hostclass] = ami
        return newest_for_hostclass

    def get_test_amis(self):
        '''Returns untested AMIs that are newer than the newest tested AMIs'''
        return self.get_items_newer_in_second_map(
            self.get_latest_tested_amis(), self.get_latest_untested_amis())

    def get_failed_amis(self):
        '''Returns failed AMIs that are newer than the newest tested AMIs'''
        return self.get_items_newer_in_second_map(
            self.get_latest_tested_amis(), self.get_latest_failed_amis())

    def get_latest_running_amis(self):
        '''Retuns hostclass: ami mapping with latest running AMIs'''
        running_ami_ids = list({instance.image_id for instance in self._disco_aws.instances()})
        running_amis = self._disco_bake.get_amis(running_ami_ids)
        sorted_amis = sorted(running_amis, key=self._disco_bake.get_ami_creation_time)
        return {DiscoBake.ami_hostclass(ami): ami for ami in sorted_amis}

    def get_update_amis(self):
        '''
        Returns list of AMIs that are ready to be deployed in production.

        Hosts must be in the pipeline definition and marked as deployable and
        the AMI must be newer than the one currently running AMI for that host.
        '''
        available = self.get_newest_in_either_map(
            self.get_latest_tested_amis(), self.get_latest_untagged_amis())
        newer = self.get_items_newer_in_second_map(self.get_latest_running_amis(), available)
        return [ami for ami in newer
                if (DiscoBake.ami_hostclass(ami) in self._hostclasses and
                    self.is_deployable(DiscoBake.ami_hostclass(ami)))]

    def is_deployable(self, hostclass):
        """Returns true for all hostclasses which aren't tagged as non-ZDD hostclasses"""
        return ((hostclass in self._hostclasses and
                 is_truthy(self._hostclasses[hostclass].get("deployable"))) or
                hostclass not in self._hostclasses)

    def get_integration_test(self, hostclass):
        """Returns the integration test for this hostclass, or None if none exists"""
        return (hostclass in self._hostclasses and
                self._hostclasses[hostclass].get("integration_test")) or None

    def wait_for_smoketests(self, ami_id, min_count, group_name=None, launch_time=None):
        '''
        Waits for smoketests to complete for an AMI.

        Returns True on success, False on failure.
        '''

        try:
            self._disco_aws.wait_for_autoscaling(ami_id, min_count,
                                                 group_name=group_name, launch_time=launch_time)
        except TimeoutError:
            logger.info("autoscaling timed out")
            return False

        try:
            self._disco_aws.smoketest(self._disco_aws.instances_from_amis([ami_id], group_name, launch_time))
        except TimeoutError:
            logger.info("smoketest timed out")
            return False
        except SmokeTestError:
            logger.info("smoketest instance was terminated")
            return False

        return True

    # Disable W0702 We want to swallow all the exceptions here
    # pylint: disable=W0702
    def _promote_ami(self, ami, stage):
        """
        Promote AMI to specified stage. And, conditionally, make executable by
        production account if ami is staged as tested.
        """

        prod_baker = self._disco_bake.option("prod_baker")
        promote_conditions = [
            stage == "tested",
            prod_baker,
            ami.tags.get("baker") == prod_baker,
        ]

        try:
            self._disco_bake.promote_ami(ami, stage)
            if all(promote_conditions):
                self._disco_bake.promote_ami_to_production(ami)
        except:
            logger.exception("promotion failed")

    def handle_nodeploy_ami(self, ami, pipeline_dict=None, dry_run=False, old_group=None):
        '''Promotes a non-deployable host and updates the autoscaling group to use it next time

        A host is launched in testing mode and if it passes smoketests it is promoted and
        the autoscaling group for that hostclass is updated.

        The currently active instances of this hostclass are not put in maintenance mode and
        are not replaced by the new AMI.

        '''
        ami_test_failed = False
        hostclass = DiscoBake.ami_hostclass(ami)

        if not pipeline_dict:
            pipeline_dict = {"hostclass": hostclass}

        logger.info(
            "Smoke testing non-deploy Hostclass %s AMI %s with %s deployment strategy",
            hostclass, ami.id, DEPLOYMENT_STRATEGY_CLASSIC
        )

        if dry_run:
            return

        # Generate the two pipelines we'll need, one with double instance sizing for deployment, and another
        # that has correct instance sizing for the final form of the ASG.
        new_hostclass_dict = self._generate_deploy_pipeline(
            deployment_strategy=DEPLOYMENT_STRATEGY_CLASSIC,
            pipeline_dict=pipeline_dict,
            old_group=old_group,
            ami=ami
        )

        rollback_hostclass_dict = self._generate_post_deploy_pipeline(
            pipeline_dict=pipeline_dict,
            old_group=old_group,
            ami=ami
        )

        now = datetime.utcnow()

        self._disco_aws.spinup([new_hostclass_dict], testing=True)

        if self.wait_for_smoketests(ami.id, rollback_hostclass_dict["desired_size"], launch_time=now):
            self._promote_ami(ami, "tested")
        else:
            self._promote_ami(ami, "failed")
            rollback_hostclass_dict.pop("ami", None)
            logger.error("AMI smoke test failed.")
            ami_test_failed = True

        if old_group:
            self._disco_aws.terminate(self._get_new_instances(ami.id, launch_time=now), use_autoscaling=True)
            self._disco_aws.spinup([rollback_hostclass_dict])
            # Create scheduled actions on the ASG.
            self._create_scaling_schedule(pipeline_dict, hostclass=hostclass)
        else:
            self._disco_autoscale.delete_groups(hostclass=hostclass, force=True)

        if ami_test_failed:
            raise RuntimeError("Smoke test for non-deploy Hostclass %s AMI %s failed", hostclass, ami.id)

    def _get_old_instances(self, new_ami_id, launch_time=None):
        '''
        Returns instances for the hostclass of new_ami_id that are not running new_ami_id
        or which were launched before the specified launch time
        :param new_ami_id: The new ami_id current used for the hostclass
        :param launch_time: If launch time is specified only instances launched before the specified
        launch time will be returned.
        :return: List of instances
        '''
        hostclass = DiscoBake.ami_hostclass(self._disco_bake.connection.get_image(new_ami_id))
        all_ids = [inst.instance_id for inst in self._disco_autoscale.get_instances(hostclass=hostclass)]
        all_instances = self._disco_aws.instances(instance_ids=all_ids)
        return [inst for inst in all_instances
                if (inst.image_id != new_ami_id) or
                (launch_time and get_instance_launch_time(inst) < launch_time)]

    def _get_new_instances(self, new_ami_id, launch_time=None):
        '''
        Returns instances running new_ami_id
        If launch_time is specified, select only instances launched after the specified date
        :param new_ami_id:
        :param launch_time: If launch time is specified only instances launched after the specified
        launch time will be returned.
        :return: List of instances
        '''
        hostclass = DiscoBake.ami_hostclass(self._disco_bake.connection.get_image(new_ami_id))
        all_ids = [inst.instance_id for inst in self._disco_autoscale.get_instances(hostclass=hostclass)]
        all_instances = self._disco_aws.instances(filters={"image_id": [new_ami_id]}, instance_ids=all_ids)
        return [inst for inst in all_instances
                if not launch_time or get_instance_launch_time(inst) >= launch_time]

    def _get_latest_other_image_id(self, new_ami_id):
        '''
        Returns image id of latest currently deployed image other than the specified one.

        Returns None if none of the images currently deployed still exist.
        '''
        old_instances = self._get_old_instances(new_ami_id)
        deployed_ami_ids = list(set([instance.image_id for instance in old_instances]))
        images = []
        for ami_id in deployed_ami_ids:
            try:
                images.extend(self._disco_bake.get_amis(image_ids=[ami_id]))
            except EC2ResponseError as err:
                if err.code == "InvalidAMIID.NotFound":
                    logger.warning("Unable to find old AMI %s, it was probably deleted", ami_id)
                else:
                    raise
        return max(images, key=self._disco_bake.get_ami_creation_time).id if len(images) else None

    def run_tests_with_maintenance_mode(self, ami):
        '''
        Runs integration tests for a hostclass beloning to a particular AMI.

        This method puts all AMIs not matching the passed in AMI into maintenance mode.
        If tests pass the old instances are left in maintenance mode, otherwise they are returned to normal.
        '''
        hostclass = DiscoBake.ami_hostclass(ami)
        self._set_maintenance_mode(hostclass, self._get_old_instances(ami.id), True)
        ret = self.run_integration_tests(ami)
        if not ret:
            self._set_maintenance_mode(hostclass, self._get_old_instances(ami.id), False)
        return ret

    def handle_tested_ami(self, ami, pipeline_dict=None, run_tests=False, dry_run=False, old_group=None):
        '''
        Tests hostclasses which we can deploy normally.

        Deploys AMIs inside the same autoscaling group, and destroys old instances on successful passing,
        otherwise destroys the new instances and rolls back the ASG's configuration.
        '''
        ami_test_failed = False
        hostclass = DiscoBake.ami_hostclass(ami)

        logger.info(
            "testing deployable hostclass %s AMI %s with %s deployment strategy",
            hostclass,
            ami.id,
            DEPLOYMENT_STRATEGY_CLASSIC
        )

        if dry_run:
            return

        if not pipeline_dict:
            pipeline_dict = {}

        if old_group and run_tests and not self.run_integration_tests(ami):
            raise RuntimeError("Failed pre-test -- not testing AMI {}".format(ami.id))

        # Generate the two pipelines we'll need, one with double instance sizing for deployment, and another
        # that has correct instance sizing for the final form of the ASG.
        new_hostclass_dict = self._generate_deploy_pipeline(
            deployment_strategy=DEPLOYMENT_STRATEGY_CLASSIC,
            pipeline_dict=pipeline_dict,
            old_group=old_group,
            ami=ami
        )

        post_hostclass_dict = self._generate_post_deploy_pipeline(
            pipeline_dict=pipeline_dict,
            old_group=old_group,
            ami=ami
        )

        now = datetime.utcnow()

        self._disco_aws.spinup([new_hostclass_dict])

        try:
            if (self.wait_for_smoketests(ami.id, post_hostclass_dict["desired_size"], launch_time=now) and
                    (not run_tests or self.run_tests_with_maintenance_mode(ami))):
                # Roll forward with new configuration
                self._disco_aws.terminate(self._get_old_instances(ami.id, launch_time=now),
                                          use_autoscaling=True)
                self._disco_aws.spinup([post_hostclass_dict])
                # Create scheduled actions on the ASG.
                self._create_scaling_schedule(pipeline_dict, hostclass=hostclass)
                self._promote_ami(ami, "tested")
                return
            else:
                self._promote_ami(ami, "failed")
                logger.error("AMI smoke test failed.")
                ami_test_failed = True
        except (MaintenanceModeError, IntegrationTestError):
            logger.exception("Failed to run integration test")

        # Revert to old configuration
        post_hostclass_dict["smoke_test"] = "no"
        post_hostclass_dict.pop("ami", None)

        # Revert to the latest tested AMI if possible
        old_ami_id = self._get_latest_other_image_id(ami.id)
        if old_ami_id:
            post_hostclass_dict["ami"] = old_ami_id
        else:
            logger.error("Unable to rollback to old AMI. Autoscaling group will use new AMI on next event!")

        self._disco_aws.terminate(self._get_new_instances(ami.id, launch_time=now), use_autoscaling=True)
        self._disco_aws.spinup([post_hostclass_dict])

        if ami_test_failed:
            raise RuntimeError("Testing of deployable hostclass %s AMI %s failed", hostclass, ami.id)

        raise RuntimeError("Failed to run integration test")

    # This method handles blue/green from end to end, so it has a lot of logic in it. We should at some point
    # look at breaking it up a bit and/or the feasibility of that.
    # pylint: disable=too-many-locals,too-many-branches,too-many-statements,too-many-return-statements
    def handle_blue_green_ami(self, ami, pipeline_dict=None, old_group=None,
                              deployable=False, run_tests=False, dry_run=False):
        '''
        Tests hostclasses which we can deploy normally

        Deploys AMIs into a new autoscaling group. If the new AMI passes tests, the old ASG is destroyed,
        and the new ASG is preserved. Otherwise, the original ASG is preserved.

        Also creates a separate testing ELB that is used for the purposes of integration tests.
        '''
        hostclass = DiscoBake.ami_hostclass(ami)
        logger.info("testing %s hostclass %s AMI %s with %s deployment strategy",
                    "deployable" if deployable else "non-deployable", hostclass, ami.id,
                    DEPLOYMENT_STRATEGY_BLUE_GREEN)

        if dry_run:
            return

        # Default pipeline dict to being an empty dictionary so that it works with the generate pipeline
        # functions as well as the scheduled actions functions
        if not pipeline_dict:
            pipeline_dict = {}

        uses_elb = is_truthy(self.hostclass_option_default(hostclass, "elb", "no"))

        new_group_config = self._generate_post_deploy_pipeline(
            pipeline_dict=pipeline_dict,
            old_group=old_group,
            ami=ami
        )

        try:
            # Spinup our new autoscaling group in testing mode, making one even if one already exists.
            self._disco_aws.spinup([new_group_config], create_if_exists=True, testing=True)
        except TooManyAutoscalingGroups:
            logger.exception("Too many autoscaling groups exist. Unable to determine which ASG to delete,"
                             "so refusing to do anything. Manual cleanup probably required.")
            raise
        except Exception:
            logger.exception("Spinning up a new autoscaling group failed")

            # Try to grab the new group. If it exists, we get a group. If not, we get a `None`.
            new_group = self._disco_autoscale.get_existing_group(hostclass=hostclass,
                                                                 throw_on_two_groups=False)

            # It's possible that we might have ended up grabbing the old group instead of the new group we
            # just made. So check that the group we just got isn't the same as the group that already exists.
            old_group_is_not_new_group = new_group and old_group and old_group.name != new_group.name

            # If we did get a new group and its not the same as the old group (or no old group exists), let's
            # tear down the new testing group and its ELB if it exists.
            if new_group and (not old_group or old_group_is_not_new_group):
                logger.info('Destroying the testing group')
                # Destroy the testing ASG
                self._disco_autoscale.delete_groups(group_name=new_group.name, force=True)
                if uses_elb:
                    # Destroy the testing ELB
                    self._disco_elb.delete_elb(hostclass, testing=True)
            raise RuntimeError("Spinning up a new autoscaling group failed")

        new_group = self._disco_autoscale.get_existing_group(hostclass=hostclass, throw_on_two_groups=False)

        if old_group and old_group.name == new_group.name:
            raise RuntimeError("Old group and new group should not be the same.")

        try:
            smoke_tests = self.wait_for_smoketests(ami.id, new_group_config["desired_size"] or 1,
                                                   group_name=new_group.name)
            if smoke_tests and run_tests:
                # If smoke tests passed and we should run integration tests, run them
                integration_tests = self.run_integration_tests(ami, wait_for_elb=uses_elb)
            elif not run_tests:
                # Otherwise, if the tests should not be run, simply default them to passed
                integration_tests = True
            if smoke_tests and integration_tests:
                # If testing passed, mark AMI as tested
                self._promote_ami(ami, "tested")
                # Get list of instances in group
                group_instance_ids = [inst.instance_id for inst in
                                      self._disco_autoscale.get_instances(group_name=new_group.name)]
                if not group_instance_ids:
                    raise RuntimeError("Could not find any instances in new group %s", new_group.name)
                group_instances = self._disco_aws.instances(instance_ids=group_instance_ids)
                # If we are actually deploying and are able to leave testing mode
                if deployable and self._set_testing_mode(hostclass, group_instances, False):
                    logger.info("Successfully left testing mode for group %s", new_group.name)
                    # Update ASG to exit testing mode and attach to the normal ELB if applicable.
                    self._disco_aws.spinup([new_group_config], group_name=new_group.name)

                    if uses_elb:
                        try:
                            # Wait until the new ASG is registered and marked as healthy by ELB.
                            self._disco_elb.wait_for_instance_health_state(hostclass=hostclass,
                                                                           instance_ids=group_instance_ids)
                        except TimeoutError:
                            logger.exception("Waiting for health of instances attached to ELB timed out")
                            # Destroy the testing ASG
                            self._disco_autoscale.delete_groups(group_name=new_group.name, force=True)
                            if uses_elb:
                                # Destroy the testing ELB
                                self._disco_elb.delete_elb(hostclass, testing=True)
                            raise

                    # Create scheduled actions on the new ASG now that we will likely keep it.
                    self._create_scaling_schedule(pipeline_dict, group_name=new_group.name)

                    # we can destroy the old group
                    if old_group:
                        # Empty the original ASG for connection draining purposes
                        self._disco_autoscale.scaledown_groups(group_name=old_group.name, wait=True,
                                                               noerror=True)
                        # Destroy the original ASG
                        self._disco_autoscale.delete_groups(group_name=old_group.name, force=True)
                    if uses_elb:
                        # Destroy the testing ELB
                        self._disco_elb.delete_elb(hostclass, testing=True)
                    return
                else:
                    # Otherwise, we need to keep the old group and destroy the new one
                    if deployable:
                        reason = "Unable to exit testing mode for group {}".format(new_group.name)
                    else:
                        reason = "{} is not deployable".format(hostclass)

                    logger.error("%s, destroying new autoscaling group", reason)

                    # Destroy the testing ASG
                    self._disco_autoscale.delete_groups(group_name=new_group.name, force=True)

                    if uses_elb:
                        # Destroy the testing ELB
                        self._disco_elb.delete_elb(hostclass, testing=True)

                    # If the hostclass isn't deployable and an old group exists, we should update the old
                    # group so that new instances from that old group are spun up with the newly tested AMI.
                    if not deployable and old_group:
                        self._disco_aws.spinup([new_group_config], group_name=old_group.name)

                    # If deployable was False, return True, otherwise we're here because testing mode broke,
                    # so return False
                    if deployable:
                        raise RuntimeError(reason)
                    return
            else:
                self._promote_ami(ami, "failed")
        except (MaintenanceModeError, IntegrationTestError):
            logger.exception("Failed to run integration test")

        # Destroy the testing ASG
        self._disco_autoscale.delete_groups(group_name=new_group.name, force=True)
        if uses_elb:
            # Destroy the testing ELB
            self._disco_elb.delete_elb(hostclass, testing=True)
        if not smoke_tests:
            reason = "AMI smoke test failed."
        else:
            reason = "AMI integration test failed."
        raise RuntimeError(reason)

    def _create_scaling_schedule(self, pipeline_dict, group_name=None, hostclass=None):
        """ Create scaling schedules from the pipeline dictionary """
        desired_size = pipeline_dict.get("desired_size", 1)
        min_size = pipeline_dict.get("min_size", size_as_minimum_int_or_none(desired_size))
        max_size = pipeline_dict.get("max_size", 0) or size_as_maximum_int_or_none(desired_size)

        self._disco_aws.create_scaling_schedule(
            min_size,
            desired_size,
            max_size,
            group_name=group_name,
            hostclass=hostclass
        )

    def _generate_post_deploy_pipeline(self, pipeline_dict, old_group, ami):
        """Generate pipeline with sizing for post deployment"""
        new_config = copy.deepcopy(pipeline_dict)
        new_config["sequence"] = 1
        new_config["smoke_test"] = "no"
        new_config["ami"] = ami.id

        # If there is an already existing ASG, use its sizing. Otherwise, use the pipeline's sizing or a
        # reasonable default.
        if old_group:
            desired_size = old_group.desired_capacity
            max_size = old_group.max_size
            min_size = old_group.min_size
        else:
            # The 'or 1' is because some people set their desired size to 0 in their pipeline.
            desired_size = int(size_as_maximum_int_or_none(
                pipeline_dict.get("desired_size", 1) or 1
            ))
            min_size = int(size_as_minimum_int_or_none(
                pipeline_dict.get("min_size", 0)
            ))
            # The 'or' on max_size is here for the same reason. So if it's 0, just set it to desired_size so
            # its a valid entry...
            max_size = int(size_as_maximum_int_or_none(
                pipeline_dict.get("max_size", desired_size) or desired_size
            ))

        new_config["desired_size"] = desired_size
        new_config["min_size"] = min_size
        new_config["max_size"] = max_size

        return new_config

    def _generate_deploy_pipeline(self, deployment_strategy, pipeline_dict, old_group, ami):
        """Generate pipeline with sizing for deployment"""
        new_config = self._generate_post_deploy_pipeline(pipeline_dict, old_group, ami)

        # If we are using classic deployment and an older group exists, we need to double the sizing so that
        # new instances are actually deployed. These numbers will be later shrunk.
        if old_group and deployment_strategy == DEPLOYMENT_STRATEGY_CLASSIC:
            new_config["desired_size"] *= 2
            new_config["min_size"] = new_config["desired_size"] / 2
            new_config["max_size"] = new_config["desired_size"]

        return new_config

    def _set_maintenance_mode(self, hostclass, instances, mode_on):
        '''
        Takes instances into or out of maintenance mode.


        If we fail to put an instance into the desired mode we terminate that instance
        and raise a MaintenanceModeError exception.
        '''
        exit_code = 0
        bad_instances = []
        for inst in instances:
            _code, _stdout = self._disco_aws.remotecmd(
                inst, ["sudo", "/opt/wgen/bin/maintenance-mode.sh", "on" if mode_on else "off"],
                user=self.hostclass_option(hostclass, "test_user"), nothrow=True)
            sys.stdout.write(_stdout)
            if _code:
                exit_code = _code
                bad_instances.append(inst)
        if exit_code:
            self._disco_aws.terminate(bad_instances)
            raise MaintenanceModeError(
                "Failed to {} maintenance mode".format("enter" if mode_on else "exit"))

    def _set_testing_mode(self, hostclass, instances, mode_on):
        '''
        Takes instances into or out of testing mode.

        Returns False if any of the instances failed to enter or exit testing mode.
        '''
        exit_code = 0
        for inst in instances:
            _code, _stdout = self._disco_aws.remotecmd(
                inst, ["sudo", "/etc/asiaq/bin/testing_mode.sh", "on" if mode_on else "off"],
                user=self.hostclass_option(hostclass, "test_user"), nothrow=True)
            sys.stdout.write(_stdout)
            if _code:
                exit_code = _code
        return exit_code == 0

    def get_host(self, hostclass):
        '''Returns an instance to use for running integration tests'''
        instances = self._test_aws.instances_from_hostclasses([hostclass])
        for inst in instances:
            try:
                self._disco_aws.smoketest_once(inst)
            except TimeoutError:
                continue
            return inst
        raise IntegrationTestError("Unable to find test host")

    def run_integration_tests(self, ami, wait_for_elb=False):
        '''
        Runs integration tests for the hostclass belonging to the passed in AMI

        NOTE: This does not put any instances into maintenance mode.
        '''
        hostclass = DiscoBake.ami_hostclass(ami)
        test_hostclass = self.hostclass_option(hostclass, "test_hostclass")
        test_command = self.hostclass_option(hostclass, "test_command")
        test_user = self.hostclass_option(hostclass, "test_user")
        test_name = self.get_integration_test(hostclass)

        if wait_for_elb:
            try:
                self._disco_elb.wait_for_instance_health_state(hostclass=hostclass, testing=True)
            except TimeoutError:
                logger.exception("Waiting for health of instances attached to testing ELB timed out")
                return False

        logger.info("running integration test %s on %s", test_name, test_hostclass)
        exit_code, stdout = self._test_aws.remotecmd(
            self.get_host(test_hostclass), [test_command, test_name],
            user=test_user, nothrow=True)
        sys.stdout.write(stdout)
        return exit_code == 0

    def test_ami(self, ami, dry_run, deployment_strategy=None):
        '''Handles testing and promoting a new AMI for a hostclass'''
        logger.info("testing %s %s", ami.id, ami.name)
        hostclass = DiscoBake.ami_hostclass(ami)
        pipeline_hostclass_dict = self._hostclasses.get(hostclass)
        group = self._disco_autoscale.get_existing_group(hostclass)
        deployable = self.is_deployable(hostclass)
        testable = bool(self.get_integration_test(hostclass))

        if deployment_strategy is not None:
            desired_deployment_strategy = deployment_strategy
        else:
            desired_deployment_strategy = self.hostclass_option_default(hostclass, 'deployment_strategy',
                                                                        DEPLOYMENT_STRATEGY_BLUE_GREEN)

        if desired_deployment_strategy == DEPLOYMENT_STRATEGY_CLASSIC:
            logger.warning(
                "Classic deployment will be removed in a future version of Asiaq."
                "Please switch to Blue/Green deployment by setting `deployment_strategy=%s` under your "
                "hostclass' section in disco_aws.ini",
                DEPLOYMENT_STRATEGY_BLUE_GREEN
            )

        if desired_deployment_strategy == DEPLOYMENT_STRATEGY_BLUE_GREEN:
            # We are only deployable in testing if we are in the pipeline. Otherwise assume that we aren't
            # deployable. This must be done because we don't want to deploy things that shouldn't end up in
            # the testing environment and but do need to be tested. An example would be hostclasses that are
            # only expected to exist in the bakery_environment. This doesn't hold true for update, which
            # should just always deploy things unless explicitly told no.
            deployable = pipeline_hostclass_dict and deployable
            return self.handle_blue_green_ami(
                ami,
                pipeline_dict=pipeline_hostclass_dict,
                old_group=group,
                deployable=deployable,
                run_tests=testable,
                dry_run=dry_run
            )
        elif not deployable:
            self.handle_nodeploy_ami(
                ami,
                pipeline_dict=pipeline_hostclass_dict,
                dry_run=dry_run,
                old_group=group
            )
        elif testable:
            self.handle_tested_ami(
                ami,
                pipeline_dict=pipeline_hostclass_dict,
                run_tests=True,
                dry_run=dry_run,
                old_group=group
            )
        elif pipeline_hostclass_dict:
            self.handle_tested_ami(
                ami,
                pipeline_dict=pipeline_hostclass_dict,
                dry_run=dry_run,
                old_group=group
            )
        else:
            self.handle_nodeploy_ami(
                ami,
                pipeline_dict=None,
                dry_run=dry_run,
                old_group=group)

    def update_ami(self, ami, dry_run, deployment_strategy=None):
        '''Handles updating a hostclass to the latest tested AMI'''
        logger.info("updating %s %s", ami.id, ami.name)
        hostclass = DiscoBake.ami_hostclass(ami)
        pipeline_dict = self._hostclasses.get(hostclass)
        if not pipeline_dict:
            raise RuntimeError("Pipeline Dictionary is not defined.")

        group = self._disco_autoscale.get_existing_group(hostclass)
        deployable = self.is_deployable(hostclass)
        testable = bool(self.get_integration_test(hostclass))

        if deployment_strategy:
            desired_deployment_strategy = deployment_strategy
        else:
            desired_deployment_strategy = self.hostclass_option_default(hostclass, 'deployment_strategy',
                                                                        DEPLOYMENT_STRATEGY_BLUE_GREEN)

        if desired_deployment_strategy == DEPLOYMENT_STRATEGY_CLASSIC:
            logger.warning(
                "Classic deployment will be removed in a future version of Asiaq."
                "Please switch to Blue/Green deployment by setting `deployment_strategy=%s` under your "
                "hostclass' section in disco_aws.ini",
                DEPLOYMENT_STRATEGY_BLUE_GREEN
            )

        if desired_deployment_strategy == DEPLOYMENT_STRATEGY_BLUE_GREEN:
            self.handle_blue_green_ami(
                ami,
                pipeline_dict=pipeline_dict,
                old_group=group,
                deployable=deployable,
                run_tests=testable,
                dry_run=dry_run
            )
        elif not deployable:
            self.handle_nodeploy_ami(
                ami,
                pipeline_dict=pipeline_dict,
                dry_run=dry_run,
                old_group=group
            )
        else:
            self.handle_tested_ami(
                ami,
                pipeline_dict=pipeline_dict,
                dry_run=dry_run,
                old_group=group
            )

    def test(self, dry_run=False, deployment_strategy=None):
        '''
        Tests a single AMI and marks it as tested or failed.
        If the ami id is specified using the option --ami then run test on the specified ami
        independently of its stage,
        Otherwise use the most recent untested ami for the hostclass
        '''
        amis = self.all_stage_amis if self._restrict_amis else self.get_test_amis()
        if len(amis):
            self.test_ami(random.choice(amis), dry_run, deployment_strategy)
        else:
            reason = "Specified AMI not found:" + str(self._restrict_amis) if self._restrict_amis \
                else "No 'untested' AMIs found."
            logger.error(reason)

    def update(self, dry_run=False, deployment_strategy=None):
        '''
        Updates a single autoscaling group with a newer AMI or AMI specified in the --ami option
        If the ami id is specify using the option --ami then run update using the specified ami
        independently of its stage,
        Otherwise uses the most recent tested or un tagged ami
        '''
        amis = self.all_stage_amis if self._restrict_amis else self.get_update_amis()
        if len(amis):
            self.update_ami(random.choice(amis), dry_run, deployment_strategy)
        else:
            reason = "Specified AMI not found:" + str(self._restrict_amis) if self._restrict_amis \
                else "No 'untested' AMIs found."
            logger.error(reason)

    def hostclass_option(self, hostclass, key):
        '''
        Returns an option from the [hostclass] section of the disco_aws.ini config file if it is set,
        otherwise it returns that value from the [test] section if it is set,
        minus that prefix, otherwise it returns that value from the DEFAULT_CONFIG_SECTION if it is set.
        '''
        alt_key = key.split("test_").pop()
        if self._config.has_option(hostclass, key):
            return self._config.get(hostclass, key)
        elif self._config.has_option("test", key):
            return self._config.get("test", key)
        elif alt_key != key and self._config.has_option("test", alt_key):
            return self._config.get("test", alt_key)
        else:
            return self._config.get(DEFAULT_CONFIG_SECTION, "default_{0}".format(key))

    def hostclass_option_default(self, hostclass, key, default=None):
        """Fetch a hostclass configuration option if it exists, otherwise return value passed in as default"""
        try:
            return self.hostclass_option(hostclass, key)
        except (NoSectionError, NoOptionError):
            return default
