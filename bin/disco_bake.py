#!/usr/bin/env python
"""
Command line tool for baking AMI's and otherwise working with them.
"""

from __future__ import print_function
import sys
import argparse
from datetime import datetime

from disco_aws_automation import DiscoBake, HostclassTemplating
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# pylint: disable=R0912,R0915
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco AWS image creation & management')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level.')
    parser.add_argument('--truncate', dest='truncate', action='store_true',
                        help='Truncate extra long fields in output.')
    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_promote = subparsers.add_parser("promote", help="promote / demote an ami to an env.")
    parser_promote.set_defaults(mode="promote")
    parser_promote.add_argument("--ami", dest="ami", required=True,
                                help="AMI image id", type=str)
    parser_promote.add_argument("--stage", dest="stage", required=True,
                                help="Which stage to promote to", type=str)
    parser_promote.add_argument('--promote-to-prod', dest='promote_to_prod', action='store_const', const=True,
                                default=False, help='Make the AMI executable by prod account')

    parser_hostclass_promote = subparsers.add_parser(
        "hostclasspromote",
        help="Promote youngest AMI of latest stage to production.")
    parser_hostclass_promote.set_defaults(mode="hostclasspromote")
    parser_hostclass_promote.add_argument(
        "--hostclass", dest="hostclass", required=True,
        help="Name of hostclass to promote", type=str)

    parser_listamis = subparsers.add_parser('listamis', help='List all AMIs')
    parser_listamis.set_defaults(mode="listamis")
    parser_listamis.add_argument('--ami', dest='ami', required=False, help='AMI image id',
                                 type=str, default=None)
    parser_listamis.add_argument('--instance', dest='instance', required=False, help='Instance id',
                                 type=str, default=None)
    parser_listamis.add_argument('--stage', dest='stage', required=False,
                                 help='Only show amis available to this stage.', type=str, default=None)
    parser_listamis.add_argument('--productline', dest='product_line', required=False,
                                 help='Only show amis baked by this product line.', type=str, default=None)
    parser_listamis.add_argument('--state', dest='state', required=False,
                                 help='Only show amis in this state.', type=str, default=None)
    parser_listamis.add_argument('--hostclass', dest='hostclass', required=False,
                                 help='Only show amis for this hostclass.', type=str, default=None)
    parser_listamis.add_argument('--in-prod', dest='in_prod', action='store_const', const=True,
                                 help='Show whether AMI is executable in prod.', default=False)

    parser_liststragglers = subparsers.add_parser(
        'liststragglers', help='List hostclasses for which AMIs have not been recently promoted')
    parser_liststragglers.set_defaults(mode="liststragglers")
    parser_liststragglers.add_argument(
        '--days', dest='days', required=False,
        help='Set how recently AMI must have been promoted', type=int, default=3)

    parser_listlatestami = subparsers.add_parser(
        'listlatestami', help='Lists the latest ami for a given hostclass and stage')
    parser_listlatestami.set_defaults(mode="listlatestami")
    parser_listlatestami.add_argument('--stage', dest='stage', required=True,
                                      help='Display the latest ami for this stage or later', type=str)
    parser_listlatestami.add_argument('--hostclass', dest='hostclass', required=True,
                                      help='Display the latest ami for this hostclass.', type=str)

    parser_deleteami = subparsers.add_parser('deleteami', help='Delete AMI')
    parser_deleteami.set_defaults(mode="deleteami")
    parser_deleteami.add_argument('--ami', dest='ami', required=True, help='AMI image id',
                                  type=str, default=None)

    parser_cleanupamis = subparsers.add_parser('cleanupamis', help='Delete old (by date, or count) amis')
    parser_cleanupamis.set_defaults(mode="cleanupamis")
    parser_cleanupamis.add_argument('--stage', dest='stage', required=True,
                                    help='Restrict to environment type', type=str, default=None)
    parser_cleanupamis.add_argument('--keep', dest='count', required=False,
                                    help='Minimum number of images to keep', type=int, default=3)
    parser_cleanupamis.add_argument('--age', dest='days', required=False,
                                    help='Minimum age of images to expire', type=int, default=14)
    parser_cleanupamis.add_argument('--hostclass', type=str, required=False,
                                    help='Restrict to this hostclass', default=None)
    parser_cleanupamis.add_argument('--productline', dest="product_line", type=str, required=False,
                                    help='Restrict to this productline', default=None)
    parser_cleanupamis.add_argument('--dryrun', dest='dryrun', action='store_const', const=True,
                                    default=False, help='Print out amis to be deleted without deleting them')
    parser_cleanupamis.add_argument('--exclude-amis', dest='exclude_amis', type=str, default='',
                                    help="A comma seperated list of AMIs to not delete")

    parser_bake = subparsers.add_parser('bake', help="Create an ami",
                                        description="Phase1 AMI is created if hostclass is omited. "
                                        "Else latest AMI is used to generate phase2, "
                                        "hostclass specific image.")
    parser_bake.set_defaults(mode="bake")
    parser_bake.add_argument('--hostclass', type=str, default=None)
    parser_bake.add_argument('--no-destroy', dest='no_destroy', action='store_const', const=True,
                             default=False, help='If bake fails do not terminate instance')
    parser_bake.add_argument("--stage", dest="stage", default=None,
                             help="Which stage to tag baked ami with", type=str)
    parser_bake.add_argument('--source-ami', type=str, default=None,
                             help='The ami to be used as a base for baking')
    parser_bake.add_argument('--use-local-ip', dest='use_local_ip', action='store_const',
                             const=True, default=False,
                             help="Use instances' local ip address for operations. "
                             "Set this flag when baking from same subnet as where the baking is occuring.")

    parser_create = subparsers.add_parser(
        'create', help="Create a hostclass",
        description="Creates the necessary bits for a generic hostclass")
    parser_create.set_defaults(mode="create")
    parser_create.add_argument('--hostclass', type=str, default=None, required=True)

    parser_list_repo = subparsers.add_parser('listrepo', help="Print info about repo instance",
                                             description="")
    parser_list_repo.set_defaults(mode="listrepo")

    return parser


def run():
    """Parses command line and dispatches the commands"""
    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    if args.mode == "bake":
        bakery = DiscoBake(use_local_ip=args.use_local_ip)
        bakery.bake_ami(args.hostclass, args.no_destroy, args.source_ami, args.stage)
    elif args.mode == "create":
        HostclassTemplating.create_hostclass(args.hostclass)
    elif args.mode == "promote":
        bakery = DiscoBake()
        ami = bakery.get_image(args.ami)
        bakery.promote_ami(ami, args.stage)
        if args.promote_to_prod:
            bakery.promote_ami_to_production(ami)
    elif args.mode == "hostclasspromote":
        bakery = DiscoBake()
        bakery.promote_latest_ami_to_production(args.hostclass)
    elif args.mode == "listrepo":
        bakery = DiscoBake()
        repo = bakery.repo_instance()
        if repo:
            print(repo.ip_address)
    elif args.mode == "listamis":
        ami_ids = [args.ami] if args.ami else None
        instance_ids = [args.instance] if args.instance else None
        bakery = DiscoBake()
        amis = sorted(bakery.list_amis(ami_ids,
                                       instance_ids,
                                       args.stage,
                                       args.product_line,
                                       args.state,
                                       args.hostclass), key=bakery.ami_timestamp)
        now = datetime.utcnow()
        for ami in amis:
            bakery.pretty_print_ami(ami, now, in_prod=args.in_prod)
        if not amis:
            sys.exit(1)
    elif args.mode == "liststragglers":
        bakery = DiscoBake()
        for hostclass, image in bakery.list_stragglers(args.days).iteritems():
            print("{0}\t{1}".format(hostclass, image.id if image else '-'))
    elif args.mode == "listlatestami":
        bakery = DiscoBake()
        ami = bakery.find_ami(args.stage, args.hostclass)
        if ami:
            bakery.pretty_print_ami(ami)
        else:
            sys.exit(1)
    elif args.mode == "deleteami":
        bakery = DiscoBake()
        bakery.delete_ami(args.ami)
    elif args.mode == "cleanupamis":
        bakery = DiscoBake()
        exclude_amis = args.exclude_amis.split(',')
        bakery.cleanup_amis(
            args.hostclass,
            args.product_line,
            args.stage,
            args.days,
            args.count,
            args.dryrun,
            exclude_amis
        )

if __name__ == "__main__":
    run_gracefully(run)
