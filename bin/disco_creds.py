#!/usr/bin/env python
"""
Command line parser for working with the credentials bucket
"""

from __future__ import print_function
import argparse
import sys
import getpass

from disco_aws_automation import DiscoS3Bucket, DiscoVPC
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco Credentialaterator')
    parser.add_argument('--debug', dest='debug', action='store_const',
                        const=True, default=False, help='Log in debug level.')
    where = parser.add_mutually_exclusive_group(required=False)
    where.add_argument('--bucket', dest='bucket', type=str, help='Bucket to use')
    where.add_argument('--env', dest='env', type=str,
                       help='Environment/VPC to use (first bucket in environment is used) ')
    subparsers = parser.add_subparsers()

    parser_list = subparsers.add_parser('list', help='List all credential keys')
    parser_list.set_defaults(mode="list")
    parser_list.add_argument('--prefix', dest='prefix', type=str, default=None, help='Prefix keys')

    parser_get = subparsers.add_parser('get', help='Get the contents of a key')
    parser_get.set_defaults(mode="get")
    parser_get.add_argument('--key', dest='key_name', type=str, default=None, help='Key name')

    parser_set = subparsers.add_parser('set', help='Set the contents of a key')
    parser_set.set_defaults(mode="set")
    parser_set.add_argument('--key', dest='key_name', type=str, default=None, help='Key name')
    group_set = parser_set.add_mutually_exclusive_group(required=True)
    group_set.add_argument('--value', dest='key_value', type=str, default=None,
                           help='Key contents. If value is -, then value is read from stdin')
    group_set.add_argument('--password', dest='key_password', action='store_const', const=True,
                           default=False, help='Value is read as password')

    parser_delete = subparsers.add_parser('delete', help='Delete a key')
    parser_delete.set_defaults(mode="delete")
    parser_delete.add_argument('--key', dest='key_name', type=str, default=None, help='Key name')

    parser_setfile = subparsers.add_parser('setfile', help='Set the contents to a file')
    parser_setfile.set_defaults(mode="setfile")
    parser_setfile.add_argument('--key', dest='key_name', type=str, default=None, help='Key name')
    parser_setfile.add_argument('--filename', dest='file_name', type=str, default=None, help='File name')

    return parser


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    bucket_name = args.bucket or DiscoVPC.get_credential_buckets_from_env_name(config, args.env)[0]
    s3_bucket = DiscoS3Bucket(bucket_name)

    if args.mode == "list":
        print("\n".join(s3_bucket.listkeys(args.prefix)))
    elif args.mode == "get":
        print(s3_bucket.get_key(args.key_name))
    elif args.mode == "set":
        use_password = args.key_password
        key_value = args.key_value
        if use_password:
            key_value = getpass.getpass()
        elif key_value == "-":
            key_value = sys.stdin.read()
        s3_bucket.set_key(args.key_name, key_value)
    elif args.mode == "delete":
        s3_bucket.delete_key(args.key_name)
    elif args.mode == "setfile":
        key_value = s3_bucket.get_key(args.key_name)
        s3_bucket.get_contents_to_file(args.key_name, args.file_name)

if __name__ == "__main__":
    run_gracefully(run)
