#!/usr/bin/env python
"""
Command line interface for updating application authorization tokens
"""

import argparse

from disco_aws_automation import DiscoAppAuth, DiscoVPC
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco App Auth Password')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True,
                        default=False, help='Log in debug level.')
    parser.add_argument('--directory', dest="dir", type=str, help='Directory to find app_auth files')
    where = parser.add_mutually_exclusive_group(required=False)
    where.add_argument('--bucket', dest='bucket', type=str, help='Bucket to use')
    where.add_argument('--env', dest='env', type=str, help='Environment/VPC to use')
    subparsers = parser.add_subparsers()

    parser_update = subparsers.add_parser('update', help='Update application authorization tokens')
    parser_update.set_defaults(mode="update")
    parser_update.add_argument('--force', dest='force', action='store_const', const=True, default=False,
                               help='Force update of GENERATE_WHEN_EMPTY tokens')
    return parser


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    s3_bucket_name = args.bucket or DiscoVPC.get_credential_buckets_from_env_name(config, args.env)[0]
    app_auth_dir = args.dir or None
    env = args.env or s3_bucket_name.split('.')[-1]

    if args.mode == "update":
        app_auth = DiscoAppAuth(env, s3_bucket_name, app_auth_dir)
        app_auth.update(args.force)


if __name__ == "__main__":
    run_gracefully(run)
