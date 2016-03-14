#!/usr/bin/env python
"""
Manages Elastic Load Balancers

Usage:
    disco_elb.py [--debug] [--env ENV] list
    disco_elb.py [--debug] [--env ENV] update --hostclass HOSTCLASS
    disco_elb.py (-h | --help)

Commands:
    list                   List all load balancers for the current environment
    update                 Update a load balancer setup for a hostclass

Options:
    -h --help              Show this screen
    --debug                Log in debug level
    --env ENV              Environment name (VPC name)
    --hostclass HOSTCLASS  Hostclass to run command for
"""

from __future__ import print_function
import sys
import getpass
from docopt import docopt

from disco_aws_automation import DiscoELB, DiscoVPC
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation import DiscoAWS, read_config


def run():
    """Parses command line and dispatches the commands"""
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    config = read_config()

    env = args.get("--env") or config.get("disco_aws", "default_environment")
    vpc = DiscoVPC.fetch_environment(environment_name=env)
    if not vpc:
        print("Environment does not exist: {}".format(env))
        sys.exit(1)

    if args['list']:
        for elb in sorted(DiscoELB(vpc).list()):
            print("{0:<20} {1:25}".format(elb['LoadBalancerName'], ','.join(elb['AvailabilityZones'])))
    elif args['update']:
        DiscoAWS(config, env).update_elb(args['--hostclass'], owner=getpass.getuser())

if __name__ == "__main__":
    run_gracefully(run)
