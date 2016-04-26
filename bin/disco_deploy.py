#!/usr/bin/env python
"""
Deploys newly baked hostclasses

Usage:
    disco_deploy.py [options] test --pipeline PIPELINE
                    [--environment ENV] [--ami AMI | --hostclass HOSTCLASS] [--allow-any-hostclass]
    disco_deploy.py [options] update --pipeline PIPELINE --environment ENV
                    [--ami AMI | --hostclass HOSTCLASS] [--allow-any-hostclass]
    disco_deploy.py [options] list (--tested|--untested|--failed|--failures|--testable)
                    [--pipeline PIPELINE] [--environment ENV] [--ami AMI | --hostclass HOSTCLASS]
                    [--allow-any-hostclass]
    disco_deploy.py [options] list --updatable --pipeline PIPELINE --environment ENV
                    [--ami AMI | --hostclass HOSTCLASS] [--allow-any-hostclass]

Commands:
     test           For CI and Build env only! Provision, Test, and Promote one new untested AMI if one exists
     update         For Production! Update one hostclass to a new passing AMI if one exists
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

     --tested               List of latest tested AMI for each hostclass
     --untested             List of latest untested AMI for each hostclass
     --failed               List of latest failed AMI for each hostclass

     --failures             List of AMIs where the latest AMI for the hostclass has failed testing
     --testable             List of AMIs where the latest AMI for the hostclass is untested
     --updatable            List of AMIs where the latest AMI for the hostclass is newer than the
                            currently running AMI and its stage is either tested or untagged
"""

from __future__ import print_function
import sys

from docopt import docopt

from disco_aws_automation import DiscoAWS, DiscoBake, DiscoDeploy, read_config
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.pipeline import pipelines_from_file


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# pylint: disable=R0912
def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    args = docopt(__doc__)

    configure_logging(args["--debug"])

    env = args["--environment"] or config.get("disco_aws", "default_environment")

    pipelines = []
    if args["--pipeline"]:
        pipelines = pipelines_from_file(args["--pipeline"])

    aws = DiscoAWS(config, env)

    if config.has_option('test', 'env'):
        test_env = config.get('test', 'env')
        test_aws = DiscoAWS(config, test_env)
    else:
        test_aws = aws

    deploy = DiscoDeploy(
        aws, test_aws, DiscoBake(config, aws.connection),
        pipelines=pipelines,
        ami=args.get("--ami"), hostclass=args.get("--hostclass"),
        allow_any_hostclass=args["--allow-any-hostclass"])

    if args["test"]:
        deploy.test(dry_run=args["--dry-run"])
    elif args["update"]:
        deploy.update(dry_run=args["--dry-run"])
    elif args["list"]:
        missing = "-" if len(pipelines) else ""
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
            sys.exit(1 if len(failures) else 0)


if __name__ == "__main__":
    run_gracefully(run)
