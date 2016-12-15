#!/usr/bin/env python
"""
Command line tool for killing instances
"""

from __future__ import print_function
import argparse

from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.disco_config import read_config
from disco_aws_automation import DiscoChaos


def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco Chaos, instance killer')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level.')
    parser.add_argument('--dry-run', dest='dryrun', action='store_const', const=True, default=False,
                        help="Don't actually terminate instaces")
    parser.add_argument('--level', dest='level', required=False,
                        help='Level of Chaos (percent)',
                        default=1.0, type=float)
    parser.add_argument('--retainage', dest='retainage', required=False,
                        help='Machines in each hostclass to retain (percent)',
                        default=0.0, type=float)
    region_env_group = parser.add_mutually_exclusive_group()
    region_env_group.add_argument('--env', dest='env', type=str, default=None,
                                  help="The name of a VPC to operate in. " +
                                  "Default is taken from config file.")
    return parser


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    env_name = args.env or config.get("disco_aws", "default_environment")

    chaos = DiscoChaos(config, env_name, args.level, args.retainage)
    instances = chaos.get_instances_to_terminate()
    for inst in instances:
        print("{0:20} {1}".format(inst.tags.get('hostclass'), inst.id))
    if not args.dryrun:
        chaos.terminate(instances)

if __name__ == "__main__":
    run_gracefully(run)
