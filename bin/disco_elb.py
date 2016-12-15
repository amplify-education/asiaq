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
from docopt import docopt

from disco_aws_automation import DiscoELB, DiscoVPC, DiscoAWS
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.disco_config import read_config


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
        format_string = "{0:<50} {1:33} {2}"
        print(format_string.format("ELB Name", "Availability Zones", "ELB Id"), file=sys.stderr)
        for elb_info in sorted(DiscoELB(vpc).list_for_display()):
            print(format_string.format(elb_info['elb_name'], elb_info['availability_zones'],
                                       elb_info["elb_id"]))
    elif args['update']:
        DiscoAWS(config, env).update_elb(args['--hostclass'])

if __name__ == "__main__":
    run_gracefully(run)
