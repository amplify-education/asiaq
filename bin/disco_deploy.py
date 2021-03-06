#!/usr/bin/env python
"""
Deploys newly baked hostclasses

Usage:
    disco_deploy.py [options] test --pipeline PIPELINE
                    [--environment ENV] [--ami AMI] [--hostclass HOSTCLASS] [--allow-any-hostclass]
                    [--strategy STRATEGY] [--ticket TICKETID] [--deployable DEPLOYABLE]
    disco_deploy.py [options] update --pipeline PIPELINE --environment ENV
                    [--ami AMI] [--hostclass HOSTCLASS] [--allow-any-hostclass] [--strategy STRATEGY]
                    [--ticket TICKETID] [--deployable DEPLOYABLE]
    disco_deploy.py [options] list (--tested|--untested|--failed|--failures|--testable)
                    [--pipeline PIPELINE] [--environment ENV] [--ami AMI] [--hostclass HOSTCLASS]
                    [--allow-any-hostclass]
    disco_deploy.py [options] list --updatable --pipeline PIPELINE --environment ENV
                    [--ami AMI] [--hostclass HOSTCLASS] [--allow-any-hostclass]

Commands:
     test           For CI and Build env only! Provision, Test, and Promote one new untested AMI if one
                    exists. If the hostclass of the AMI is not in the supplied pipeline, the hostclass is
                    assumed to be non-deployable and will be removed after the tests finish running.
     update         For Production! Update one hostclass to a new passing AMI if one exists. It is assumed
                    that if the hostclass is not present in the pipeline, it should be considered deployable.
                    This is in contrast to the behavior of the 'test' command.
     list           Provides information about AMIs in a pipeline

Options:
     -h --help              Show this screen
     --debug                Log in debug level
     --dry-run              Does not make any modifications

     --pipeline PIPELINE    File name of the pipeline definition
     --ami AMI              Limit command to a specific AMI
     --hostclass HOSTCLASS  Limit command to a specific hostclass
     --environment ENV      Environment to operate in
     --allow-any-hostclass  Do not limit command to hostclasses defined in pipeline
     --strategy STRATEGY    The deployment strategy to use. Currently supported: 'blue_green'.
     --ticket TICKETID      The issue id associated to the deploy. The specified issue will be
                            updated based on the deploy status
    --deployable DEPLOYABLE Whether or not to force the instance to be deployed as deployable, or not. If an
                            instance is not deployable, the old ASG is retained and the new ASG is deleted.
                            Otherwise, if it is deployable, the new ASG is retained and the old ASG is
                            deleted.

     --tested               List of latest tested AMI for each hostclass
     --untested             List of latest untested AMI for each hostclass
     --failed               List of latest failed AMI for each hostclass

     --failures             List of AMIs where the latest AMI for the hostclass has failed testing
     --testable             List of AMIs where the latest AMI for the hostclass is untested
     --updatable            List of AMIs where the latest AMI for the hostclass is newer than the
                            currently running AMI and its stage is either tested or untagged
"""

from __future__ import print_function
import csv
import logging
import sys

from docopt import docopt

from disco_aws_automation import DiscoAWS, DiscoGroup, DiscoBake, DiscoDeploy, DiscoELB, DiscoVPC, DiscoSSM
from disco_aws_automation.disco_aws_util import run_gracefully, is_truthy
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


logger = logging.getLogger(__name__)


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# R0914 Allow more than 15 local variables so we can parse a lot of commands.
# R0915 Allow more than 50 statements
# pylint: disable=R0912,R0914,R0915
def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    args = docopt(__doc__)

    configure_logging(args["--debug"])

    env = args["--environment"] or config.get("disco_aws", "default_environment")

    force_deployable = None if args["--deployable"] is None else is_truthy(args["--deployable"])

    pipeline_definition = []
    if args["--pipeline"]:
        with open(args["--pipeline"], "r") as f:
            reader = csv.DictReader(f)
            pipeline_definition = [line for line in reader]

    aws = DiscoAWS(config, env)

    if config.has_option('test', 'env'):
        test_env = config.get('test', 'env')
        test_aws = DiscoAWS(config, test_env)
    else:
        test_aws = aws

    bake = DiscoBake(config, aws.connection)

    if args["--ami"] and args["--hostclass"]:
        image = bake.get_image(args["--ami"])
        if args["--hostclass"] != bake.ami_hostclass(image):
            logger.error('AMI %s does not belong to hostclass %s', args["--ami"], args["--hostclass"])
            sys.exit(1)

    vpc = DiscoVPC.fetch_environment(environment_name=env)

    deploy = DiscoDeploy(
        aws, test_aws, bake, DiscoGroup(env), DiscoELB(vpc), DiscoSSM(environment_name=env),
        pipeline_definition=pipeline_definition,
        ami=args.get("--ami"), hostclass=args.get("--hostclass"),
        allow_any_hostclass=args["--allow-any-hostclass"])

    if args["test"]:
        try:
            deploy.test(dry_run=args["--dry-run"], deployment_strategy=args["--strategy"],
                        ticket_id=args["--ticket"], force_deployable=force_deployable)
        except RuntimeError as err:
            logger.error(str(err))
            sys.exit(1)
    elif args["update"]:
        try:
            deploy.update(dry_run=args["--dry-run"], deployment_strategy=args["--strategy"],
                          ticket_id=args["--ticket"], force_deployable=force_deployable)
        except RuntimeError as err:
            logger.error(str(err))
            sys.exit(1)
    elif args["list"]:
        missing = "-" if pipeline_definition else ""
        if args["--tested"]:
            for (_hostclass, ami) in deploy.get_latest_tested_amis().iteritems():
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
        elif args["--untested"]:
            for (_hostclass, ami) in deploy.get_latest_untested_amis().iteritems():
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
        elif args["--failed"]:
            for (_hostclass, ami) in deploy.get_latest_failed_amis().iteritems():
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
        elif args["--testable"]:
            for ami in deploy.get_test_amis():
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
        elif args["--updatable"]:
            for ami in deploy.get_update_amis():
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
        elif args["--failures"]:
            failures = deploy.get_failed_amis()
            for ami in failures:
                print("{} {:40} {}".format(
                    ami.id, ami.name.split()[0], deploy.get_integration_test(ami.name.split()[0]) or missing))
            sys.exit(1 if failures else 0)


if __name__ == "__main__":
    run_gracefully(run)
