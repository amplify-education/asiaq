#!/usr/bin/env python
"""
Command line tool for dealing with ELB snapshots
"""
from __future__ import print_function
import argparse
import logging

from disco_aws_automation import DiscoAWS, read_config
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# pylint: disable=R0912
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco EBS snapshot management')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level.')
    parser.add_argument('--truncate', dest='truncate', action='store_true',
                        help='Truncate extra long fields in output.')
    region_env_group = parser.add_mutually_exclusive_group()
    region_env_group.add_argument('--env', dest='env', type=str, default=None,
                                  help="Environment. Normally, the name of a VPC. " +
                                  "Default is taken from config file.")
    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_create = subparsers.add_parser(
        'create', help='Creates an unformated EBS volume snapshot in a random availibility zone')
    parser_create.set_defaults(mode='create')
    parser_create.add_argument('--size', dest='size', required=True, type=int, help='Volume size in GB')
    parser_create.add_argument('--hostclass', dest='hostclass', type=str,
                               help="hostclass that uses this snapshot")

    parser_list = subparsers.add_parser('list', help='List all EBS snapshots')
    parser_list.set_defaults(mode='list')
    parser_list.add_argument('--hostclass', dest='hostclasses', default=[], action='append', type=str)

    parser_cleanup = subparsers.add_parser(
        'cleanup', help='Delete all but the specified number of EBS snapshots per hostclass (default: 3)')
    parser_cleanup.set_defaults(mode='cleanup')
    parser_cleanup.add_argument('--keep', dest='keep', required=False, type=int, default=3,
                                help='A non-zero number of snapshots to keep per hostclass')
    parser_cleanup.add_argument('--keep-days', dest='keep_days', required=False, type=int,
                                help='Do not delete snapshots that are less than this number of days old')

    parser_delete = subparsers.add_parser(
        'delete', help='Delete a set of snapshots')
    parser_delete_group = parser_delete.add_mutually_exclusive_group(required=True)
    parser_delete.set_defaults(mode='cdelete')
    parser_delete_group.add_argument('--snapshot', dest='snapshots', default=[], action='append', type=str)

    parser_take = subparsers.add_parser(
        'capture', help="Captures a snapshot of a running instance's persistent volume")
    parser_take.set_defaults(mode='capture')
    parser_take_group = parser_take.add_mutually_exclusive_group(required=True)
    parser_take_group.add_argument('--instance', dest='instances', default=[], action='append', type=str)
    parser_take_group.add_argument('--hostname', dest='hostnames', default=[], action='append', type=str)
    parser_take_group.add_argument('--hostclass', dest='hostclasses', default=[], action='append', type=str)
    parser_take_group.add_argument('--ami', dest='amis', default=[], action='append', type=str)

    parser_update = subparsers.add_parser(
        'update', help='Update snapshot used by new instances in a hostclass')
    parser_update.set_defaults(mode="update")
    parser_update.add_argument('--hostclass', dest='hostclass', required=True, type=str, default=None)

    return parser


def instances_from_args(disco_aws, args):
    """
    Return list instances based on following arguments:
    hostclass, instance, amis, hostname
    """
    instances = (disco_aws.instances(instance_ids=args.instances) if args.instances else [])
    instances.extend(disco_aws.instances_from_hostclasses(args.hostclasses))
    instances.extend(disco_aws.instances_from_amis(args.amis))
    instances.extend([disco_aws.instance_from_hostname(h) for h in args.hostnames])
    return instances


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()
    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    environment_name = args.env or config.get("disco_aws", "default_environment")

    aws = DiscoAWS(config, environment_name=environment_name)
    if args.mode == "create":
        aws.disco_storage.create_ebs_snapshot(args.hostclass, args.size)
    elif args.mode == "list":
        for snapshot in aws.disco_storage.get_snapshots(args.hostclasses):
            print("{0:26} {1:13} {2:9} {3} {4:4}".format(
                snapshot.tags['hostclass'], snapshot.id, snapshot.status,
                snapshot.start_time, snapshot.volume_size))
    elif args.mode == "cleanup":
        aws.disco_storage.cleanup_ebs_snapshots(args.keep, keep_last_days=args.keep_days)
    elif args.mode == "capture":
        instances = instances_from_args(aws, args)
        if not instances:
            logging.warning("No instances found")
        for instance in instances:
            return_code, output = aws.remotecmd(
                instance, ["sudo /opt/wgen/bin/take_snapshot.sh"], user="snapshot")
            if return_code:
                raise Exception("Failed to snapshot instance {0}:\n {1}\n".format(instance, output))
            logging.info("Successfully snapshotted %s", instance)
    elif args.mode == "delete":
        for snapshot_id in args.snapshots:
            aws.disco_storage.delete_snapshot(snapshot_id)
    elif args.mode == "update":
        snapshot = aws.disco_storage.get_latest_snapshot(args.hostclass)
        aws.autoscale.update_snapshot(snapshot.id, snapshot.volume_size, hostclass=args.hostclass)

if __name__ == "__main__":
    run_gracefully(run)
