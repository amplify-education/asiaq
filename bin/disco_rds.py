#!/usr/bin/env python
"""
Command line tool for working with RDS instances.
"""

from __future__ import print_function
import argparse
import sys
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_vpc import DiscoVPC
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# R0914 Allow more than 15 local variables so we can parse a lot of commands..
# R0915 Allow more than 50 statements so we can parse a lot of commands..
# pylint: disable=R0912,R0914,R0915
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco RDS automation')
    parser.add_argument('--debug', dest='debug', action='store_const',
                        const=True, default=False, help='Log in debug level.')
    subparsers = parser.add_subparsers(help='Sub-command help')

    # Update Mode
    parser_update = subparsers.add_parser(
        "update", help="Update RDS cluster(s) to reflect what's in disco_rds.ini")
    parser_update.set_defaults(mode="update")
    parser_update.add_argument('--env', dest='env', required=False, default=None,
                               help='The environment containing the RDS cluster(s)')
    parser_update_group = parser_update.add_mutually_exclusive_group()
    parser_update_group.add_argument('--cluster', dest='cluster',
                                     help='Cluster name (RDS Database Instance Identifier)')
    parser_update_group.add_argument('--parallel', dest='parallel', action='store_const', const=True,
                                     default=False, help='Update clusters in parallel')

    # List Mode
    parser_list = subparsers.add_parser("list", help="List RDS clusters in an environment")
    parser_list.set_defaults(mode="list")
    parser_list.add_argument("--env", dest="env", required=False, default=None,
                             help="The environment containing the RDS clusters")
    parser_list.add_argument("--url", dest="url", action='store_const', const=True, default=False,
                             help="Show the associated url for each cluster")

    # Delete Mode
    parser_delete = subparsers.add_parser("delete", help="Delete RDS cluster")
    parser_delete.set_defaults(mode="delete")
    parser_delete.add_argument("--env", dest="env", required=False, default=None,
                               help="The environment containing the RDS cluster")
    parser_delete.add_argument("--cluster", dest="cluster", required=True,
                               help="Cluster name (RDS Database Instance Identifier) to delete")
    parser_delete.add_argument("--skip-final-snapshot", dest="skip_final_snapshot", action='store_const',
                               const=True, default=False, help="Do not take final snapshot. Drops all data!")

    # Cleanup_snapshots Mode
    parser_cleanup_snapshots = subparsers.add_parser("cleanup_snapshots",
                                                     help="All Snapshots older than 30 days will die")
    parser_cleanup_snapshots.set_defaults(mode="cleanup_snapshots")
    parser_cleanup_snapshots.add_argument('--age', dest='days', required=False,
                                          help='Minimum age of Snapshots to expire', type=int, default=30)

    # clone Mode
    parser_clone = subparsers.add_parser("clone", help="Create a new database from an existing database")
    parser_clone.set_defaults(mode="clone")
    parser_clone.add_argument('--env', dest='env', required=False, default=None,
                              help='The environment containing the RDS cluster(s)')
    parser_clone.add_argument('--source-db', dest='source_db', required=True,
                              help='Name of the database to clone', type=str)
    parser_clone.add_argument('--source-env', dest='source_env', required=True,
                              help='Name of environment of source database', type=str)
    return parser


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()
    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    environment_name = vars(args).get('env') or config.get("disco_aws", "default_environment")
    vpc = DiscoVPC.fetch_environment(environment_name=environment_name)
    if not vpc:
        print("Environment does not exist: {}".format(environment_name))
        sys.exit(1)

    rds = vpc.rds

    if args.mode == "list":
        instances = rds.get_db_instances()
        for instance in instances:
            line = "{:<20} {:>6}GB  {:<12}".format(
                instance["DBInstanceIdentifier"], instance["AllocatedStorage"], instance["DBInstanceStatus"])
            if args.url:
                endpoint = instance["Endpoint"]
                url = "{}:{}".format(endpoint["Address"], endpoint["Port"]) if endpoint else "-"
                line += "  {}".format(url)
            print(line)
    elif args.mode == "update":
        if args.cluster:
            rds.update_cluster_by_id(args.cluster)
        else:
            rds.update_all_clusters_in_vpc(parallel=args.parallel)
    elif args.mode == "delete":
        rds.delete_db_instance(args.cluster, skip_final_snapshot=args.skip_final_snapshot)
    elif args.mode == "cleanup_snapshots":
        rds.cleanup_snapshots(args.days)
    elif args.mode == "clone":
        rds.clone(args.source_env, args.source_db)


if __name__ == "__main__":
    run_gracefully(run)
