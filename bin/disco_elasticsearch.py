#!/usr/bin/env python
"""
Manages ElasticSearch
"""
from __future__ import print_function
import argparse
import sys

from disco_aws_automation import DiscoElasticsearch
from disco_aws_automation import DiscoESArchive
from disco_aws_automation.disco_aws_util import run_gracefully, is_truthy
from disco_aws_automation.disco_logging import configure_logging


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# pylint: disable=R0912
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Asiaq ElasticSearch Creation and Management')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level')
    parser.add_argument("--env", dest="env", help="Environment name", type=str)

    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_list = subparsers.add_parser("list", help="List all ElasticSearch domains")
    parser_list.set_defaults(mode="list")
    parser_list.add_argument("--endpoint", dest="endpoint", action='store_const', default=False, const=True,
                             help="Display AWS-provided endpoint")

    parser_update = subparsers.add_parser("update",
                                          help="Create or update an ElasticSearch domain. If no names are "
                                          "provided, default behavior is to create/update all ElasticSearch "
                                          "domains found in the config.")
    parser_update.set_defaults(mode="update")
    parser_update.add_argument("--name", dest="names", type=str, action="append",
                               help="Name of the ElasticSearch domain")

    parser_delete = subparsers.add_parser("delete",
                                          help="Delete an ElasticSearch domain. If no options are provided, "
                                          "default behavior is to delete all ElasticSearch domains found in "
                                          "the config.")
    parser_delete.set_defaults(mode="delete")
    parser_delete.add_argument("--name", dest="names", type=str, action="append",
                               help="Name of the ElasticSearch domain")
    parser_delete.add_argument("--all", dest="delete_all", action='store_const', default=False, const=True,
                               help="Delete *all* ElasticSearch domains")

    parser_archive = subparsers.add_parser("archive",
                                           help="Archive the indices that are older than yesterday's "
                                           "date to S3.")
    parser_archive.set_defaults(mode="archive")
    parser_archive.add_argument("--cluster", dest="cluster", type=str, required=True,
                                help="Name of the cluster to be archived.")
    parser_archive.add_argument('--groom', dest='groom', action='store_const',
                                const=True, default=False,
                                help="When set, the archive command would delete enough indices from the "
                                "cluster such that disk usage is below the archive threshold and the number "
                                "of shards is below the maximum shards allowed.")
    parser_archive.add_argument('--dry-run', dest='dry_run', action='store_const',
                                const=True, default=False,
                                help="Whether to test run the archive process. No indices would be archived "
                                "and no changes would be made to the cluster if this is set to True.")

    parser_restore = subparsers.add_parser("restore",
                                           help="Restore the indices within the specified date range "
                                           "from S3 to the cluster.")
    parser_restore.set_defaults(mode="restore")
    parser_restore.add_argument("--cluster", dest="cluster", type=str, required=True,
                                help="Name of the cluster to be restored to.")
    parser_restore.add_argument("--begin", dest="begin_date", type=str, required=True,
                                help="Begin date (yyyy.mm.dd) of the date range (inclusive) within which "
                                "the indices are restored.")
    parser_restore.add_argument("--end", dest="end_date", type=str, required=True,
                                help="End date (yyyy.mm.dd) of the date range (inclusive) within which the "
                                "indices are restored.")
    parser_restore.add_argument('--dry-run', dest='dry_run', action='store_const',
                                const=True, default=False,
                                help="Indicates whether to test run the restore process.")

    return parser


def run():
    """Parses command line and dispatches the commands"""
    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)
    env = args.env
    disco_es = DiscoElasticsearch(env)
    interactive_shell = sys.__stdin__.isatty()

    if args.mode == "list":
        entries = disco_es.list(include_endpoint=args.endpoint)
        headers = ["Elastic Search Domain Name", "Internal Name", "Route 53 Endpoint"]
        format_line = u"{0:<28} {1:<15} {2:<35}"
        if args.endpoint:
            format_line += u" {3:<80}"
            headers.append("Elastic Search Endpoint")
        print(format_line.format(*headers), file=sys.stderr)
        for entry in entries:
            values = [entry["elasticsearch_domain_name"], entry["internal_name"], entry["route_53_endpoint"]]
            if args.endpoint:
                values.append(entry["elasticsearch_endpoint"] or u"-")
            print(format_line.format(*values))
    elif args.mode == "update":
        if args.names:
            for name in args.names:
                disco_es.update(name)
        else:
            disco_es.update()
    elif args.mode == "delete":
        prompt = "Deleting an ElasticSearch domain destroys all of its automated snapshots. Be careful!\n"
        if args.names:
            prompt += "Are you sure you want to delete ElasticSearch domains {}? (y/N)".format(args.names)
            if not interactive_shell or is_truthy(raw_input(prompt)):
                for name in args.names:
                    disco_es.delete(name)
        else:
            scope = "all configured" if not args.delete_all else "*all*"
            prompt += "Are you sure you want to delete {} ElasticSearch domains? (y/N)".format(scope)
            if not interactive_shell or is_truthy(raw_input(prompt)):
                disco_es.delete(delete_all=args.delete_all)
    elif args.mode in ['archive', 'restore']:
        disco_es_archive = DiscoESArchive(env, args.cluster)
        if args.mode == 'archive':
            snap_states = disco_es_archive.archive(dry_run=args.dry_run)
            if args.dry_run:
                print("Snapshots to be taken: {0}".format(snap_states['SUCCESS']))
            else:
                print("Snapshots state: {0}".format(snap_states))

            if args.groom:
                disco_es_archive.groom(dry_run=args.dry_run)
        else:
            disco_es_archive.restore(args.begin_date, args.end_date, args.dry_run)


if __name__ == "__main__":
    run_gracefully(run)
