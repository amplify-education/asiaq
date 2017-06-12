#!/usr/bin/env python
"""
Manages Cloudfront
"""
from __future__ import print_function
import argparse
import sys
from disco_aws_automation import read_config
from disco_aws_automation import DiscoCloudfront, DiscoVPC
from disco_aws_automation.disco_aws_util import run_gracefully, is_truthy
from disco_aws_automation.disco_logging import configure_logging


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# pylint: disable=R0912
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Asiaq Cloudfront Creation and Management')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level')
    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_create = subparsers.add_parser("create",
                        help="Create or update a Cloudfront distributions.")
    parser_create.add_argument("--origin_path", type=str,
                                help="Name of the cloudfront distribution")
    parser_create.add_argument('--mode', default='create')
    return parser


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()
    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)
    origin_path = args.origin_path

    env = config.get("disco_aws", "default_environment")
    vpc = DiscoVPC.fetch_environment(environment_name=env)
    if not vpc:
        print("Environment does not exist: {}".format(env))
        sys.exit(1)

    disco_cf = DiscoCloudfront(vpc)

    if args.mode == "create":
        entries = disco_cf.create(origin_path)
    elif args.mode == "update":
        print ("TBD Pull in a story. ")
    elif args.mode == "delete":
        print ("TBD Lets do it. ")

if __name__ == "__main__":
    run_gracefully(run)
