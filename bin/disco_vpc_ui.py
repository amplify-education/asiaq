#!/usr/bin/env python
"""
Command line tool for creating and destroying VPC's
"""

from __future__ import print_function
import argparse
import sys

from disco_aws_automation import (
    DiscoVPC,
    DiscoVPCPeerings
)
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.resource_helper import key_values_to_tags


def parse_arguments():
    """Read in options passed in over command line"""
    parser = argparse.ArgumentParser(description='AWS VPC automation')
    parser.add_argument('--debug', dest='debug', action='store_const',
                        const=True, default=False, help='Log in debug level.')
    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_create = subparsers.add_parser('create', help='Create new VPC based environmnet')
    parser_create.set_defaults(mode="create")
    parser_create.add_argument('--name', dest='vpc_name', required=True,
                               help='What to call the new environment.')
    parser_create.add_argument('--type', dest='vpc_type', required=True,
                               help='What type of environment to create (as defined in config).')
    parser_create.add_argument('--skip-enis', dest='skip_enis', action='store_const',
                               const=True, default=False,
                               help="Skip pre-allocating ENIs with static IPs used by hostclasses.")
    parser_create.add_argument('--tag', dest='tags', required=False, action='append', type=str,
                               help="The key:value pair used to tag the VPC"
                                    " (Example: --tag productline:astronauts).")

    parser_destroy = subparsers.add_parser(
        'destroy', help='Delete environment releasing all non-persistent resources.')
    parser_destroy.set_defaults(mode='destroy')
    parser_destroy_group = parser_destroy.add_mutually_exclusive_group(required=True)
    parser_destroy_group.add_argument('--name', dest='vpc_name', default=None,
                                      help="The name of the environment that ought to be destroyed.")
    parser_destroy_group.add_argument('--vpc-id', dest='vpc_id', default=None,
                                      help="The VPC ID of the environment that ought to be destroyed.")

    parser_list = subparsers.add_parser('list', help='List all current VPCs')
    parser_list.set_defaults(mode="list")
    parser_list.add_argument('--type', dest='env_type', action='store_const',
                             const=True, default=False, help='Print env type')

    parser_peerings = subparsers.add_parser('peerings', help='operation on vpc peerings')
    parser_peerings.set_defaults(mode="peerings")
    parser_peerings.add_argument(
        '--create', dest='create_peerings', action='store_const',
        const=True, default=False,
        help='Create peerings between the VPCs that currently exist, as configured in disco_vpc.ini')
    parser_peerings.add_argument('--delete', dest='delete_peerings', action='store_const',
                                 const=True, default=False,
                                 help='Delete all existing VPC peerings')
    parser_peerings.add_argument('--list', dest='list_peerings', action='store_const',
                                 const=True, default=False,
                                 help='List all VPC peerings')
    parser_peerings.add_argument('--name', dest='vpc_name', required=False, default=None,
                                 help='The VPC Name of the environment for VPC peering operation')
    parser_peerings.add_argument('--vpc-id', dest='vpc_id', required=False, default=None,
                                 help="The VPC ID of the environment for VPC peering operation")

    parser_update = subparsers.add_parser(
        'update', help='Update environment settings.')
    parser_update.set_defaults(mode='update')
    parser_update_group = parser_update.add_mutually_exclusive_group(required=True)
    parser_update_group.add_argument('--name', dest='vpc_name', default=None,
                                     help="The name of the environment that ought to be updated.")
    parser_update_group.add_argument('--vpc-id', dest='vpc_id', default=None,
                                     help="The VPC ID of the environment that ought to be updated.")
    parser_update.add_argument('--dry-run', dest='dry_run', action='store_const',
                               const=True, default=False,
                               help="Whether to test run the update before the actual run. No "
                               "changes would be made to the VPC if this is set to True.")

    return parser.parse_args()


def create_vpc_command(args):
    """ handle vpc create command actions"""
    if DiscoVPC.fetch_environment(environment_name=args.vpc_name):
        print("VPC with same name already exists.")
        sys.exit(1)
    else:
        tags = key_values_to_tags(args.tags) if args.tags else None
        vpc = DiscoVPC(args.vpc_name, args.vpc_type, skip_enis_pre_allocate=args.skip_enis,
                       vpc_tags=tags)
        print("VPC {0}({1}) has been created".format(args.vpc_name, vpc.get_vpc_id()))


def destroy_vpc_command(args):
    """ handle vpc destroy command actions"""
    if args.vpc_name:
        vpc = DiscoVPC.fetch_environment(environment_name=args.vpc_name)
    else:
        vpc = DiscoVPC.fetch_environment(vpc_id=args.vpc_id)

    if vpc:
        vpc.destroy()
    else:
        print("No matching VPC found")
        sys.exit(2)


def update_vpc_command(args):
    """ handle vpc update command actions"""
    if args.vpc_name:
        vpc = DiscoVPC.fetch_environment(environment_name=args.vpc_name)
    else:
        vpc = DiscoVPC.fetch_environment(vpc_id=args.vpc_id)

    if vpc:
        vpc.update(args.dry_run)
    else:
        print("No matching VPC found")
        sys.exit(2)


def list_vpc_command(args):
    """ handle list vpcs command actions """
    for vpc_env in DiscoVPC.list_vpcs():
        line = u"{0}\t{1:<15}".format(vpc_env['id'], vpc_env['tags'].get("Name", "-"))
        if args.env_type:
            line += u"\t{0}".format(vpc_env['tags'].get("type", "-"))
        print(line)


def proxy_peerings_command(args):
    """ handle peerings command actions"""
    if args.vpc_name and args.vpc_id:
        print("Don't use vpc_name and vpc_id at the same time.")
        sys.exit(2)

    vpc = None
    if args.vpc_name:
        vpc = DiscoVPC.fetch_environment(environment_name=args.vpc_name)
    elif args.vpc_id:
        vpc = DiscoVPC.fetch_environment(vpc_id=args.vpc_id)

    vpc_id = vpc.get_vpc_id() if vpc else None

    disco_peerings = DiscoVPCPeerings()

    if args.list_peerings:
        vpc_map = {vpc['id']: vpc for vpc in DiscoVPC.list_vpcs()}
        peerings = sorted(
            disco_peerings.list_peerings(vpc_id, include_failed=True),
            key=lambda p: vpc_map.get(p['AccepterVpcInfo']['VpcId'])['tags'].get("Name"))

        for peering in peerings:

            vpc1 = vpc_map.get(peering['AccepterVpcInfo']['VpcId'])
            vpc2 = vpc_map.get(peering['RequesterVpcInfo']['VpcId'])

            line = u"{0:<14} {1:<8} {2:<20} {3:<21}".format(
                peering['VpcPeeringConnectionId'], peering['Status']['Code'], "{}<->{}".format(
                    vpc1['tags'].get("Name") if vpc1 is not None else "",
                    vpc2['tags'].get("Name") if vpc2 is not None else ""),
                "{}<->{}".format(
                    peering['AccepterVpcInfo'].get('CidrBlock'),
                    peering['RequesterVpcInfo'].get('CidrBlock')))
            print(line)
    elif args.delete_peerings:
        disco_peerings.delete_peerings(vpc_id)
    elif args.create_peerings:
        disco_peerings.update_peering_connections(vpc)


def run():
    """Parses command line and dispatches the commands"""
    args = parse_arguments()
    configure_logging(args.debug)

    if args.mode == "create":
        create_vpc_command(args)
    elif args.mode == "destroy":
        destroy_vpc_command(args)
    elif args.mode == "list":
        list_vpc_command(args)
    elif args.mode == 'peerings':
        proxy_peerings_command(args)
    elif args.mode == "update":
        update_vpc_command(args)


if __name__ == "__main__":
    run_gracefully(run)
