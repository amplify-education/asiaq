#!/usr/bin/env python
"""
Command line tool for working with DynamoDB tables.
"""

from __future__ import print_function
import argparse
import json
import decimal
import datetime
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation import DiscoDynamoDB
from disco_aws_automation.disco_config import read_config


class DecimalEncoder(json.JSONEncoder):
    """
        Helper class to convert a DynamoDB item to JSON. It's needed because
        some data types used in a DynamoDB response are not JSON serializable
    """

    # default method will not be hidden with the way we initialize the object
    # pylint: disable=E0202
    def default(self, value):
        if isinstance(value, decimal.Decimal) or isinstance(value, datetime.datetime):
            return str(value)
        return super(DecimalEncoder, self).default(value)


def get_parser():
    """Returns command line parser"""
    parser = argparse.ArgumentParser(description='Disco DynamoDB automation')
    subparsers = parser.add_subparsers(help='Sub-command help')

    # List Mode
    parser_list = subparsers.add_parser("list", help="List DynamoDB tables in all environments")
    parser_list.set_defaults(mode="list")
    parser_list.add_argument("--header", dest="header", required=False, default=False, action="store_true",
                             help="Option to show headers in the output")

    parser_create = subparsers.add_parser("create", help="Create a DynamoDB table in an environment")
    parser_create.set_defaults(mode="create")
    parser_create.add_argument("--env", dest="env", required=False, default=None,
                               help="The environment in which to create the DynamoDB table")
    parser_create.add_argument("--config", dest="config", required=True,
                               help="The config file in JSON format that defines DynamoDB table")
    parser_create.add_argument("--wait", dest="wait", default=False, action="store_true",
                               help="Whether to wait for the create operation to be completely done")

    parser_describe = subparsers.add_parser("describe", help="Describe a DynamoDB table in an environment")
    parser_describe.set_defaults(mode="describe")
    parser_describe.add_argument("--env", dest="env", required=False, default=None,
                                 help="The environment in which to create the DynamoDB table")
    parser_describe.add_argument("--table", dest="table", required=True,
                                 help="The name of the table")

    parser_update = subparsers.add_parser("update", help="Update a DynamoDB table in an environment")
    parser_update.set_defaults(mode="update")
    parser_update.add_argument("--env", dest="env", required=False, default=None,
                               help="The environment in which to update the DynamoDB table")
    parser_update.add_argument("--table", dest="table", required=True,
                               help="The name of the DynamoDB table")
    parser_update.add_argument("--config", dest="config", required=True,
                               help="The config file in JSON format that has the new definition"
                                    "of the DynamoDB table")
    parser_update.add_argument("--wait", dest="wait", default=False, action="store_true",
                               help="Whether to wait for the update operation to be completely done")

    parser_delete = subparsers.add_parser("delete", help="Delete a DynamoDB table in an environment")
    parser_delete.set_defaults(mode="delete")
    parser_delete.add_argument("--env", dest="env", required=False, default=None,
                               help="The environment in which to create the DynamoDB table")
    parser_delete.add_argument("--table", dest="table", required=True,
                               help="The name of the table to be deleted")
    parser_delete.add_argument("--wait", dest="wait", default=False, action="store_true",
                               help="Whether to wait for the delete operation to be completely done")

    return parser


def list_tables(dynamodb, header):
    """ Lists all the DynamoDB tables """
    tables = dynamodb.get_all_tables()
    if header:
        print(u"{0:<20} {1}".format("TABLE_NAME", "ENV_NAME"))

    for table in tables:
        split_index = table.find("_")
        if split_index > 0:
            table_name = table[:split_index]
            env_name = table[split_index + 1:]
        else:
            table_name = table
            env_name = ""
        line = u"{0:<20} {1}".format(table_name, env_name)
        print(line)


def create_table(dynamodb, config, wait):
    """ Creates a DynamoDB table based on the definition in config """
    print(_convert_response_to_json_str(dynamodb.create_table(config, wait)))


def update_table(dynamodb, name, config, wait):
    """ Updates a DynamoDB table based on the definition in config """
    print(_convert_response_to_json_str(dynamodb.update_table(name, config, wait)))


def delete_table(dynamodb, table, wait):
    """ Deletes a DynamoDB table with the specified name """
    print(_convert_response_to_json_str(dynamodb.delete_table(table, wait)))


def describe_table(dynamodb, table):
    """ Describes a DynamoDB table with the specified name """
    print(_convert_response_to_json_str(dynamodb.describe_table(table)))


def _convert_response_to_json_str(response):
    return json.dumps(response, sort_keys=True, indent=2, cls=DecimalEncoder)


def run():
    """ Parses command line and dispatches the commands disco_dynamodb.py list """
    config = read_config()
    parser = get_parser()
    args = parser.parse_args()

    environment_name = args.env \
        if (hasattr(args, "env") and args.env) else config.get("disco_aws", "default_environment")
    dynamodb = DiscoDynamoDB(environment_name=environment_name)

    if args.mode == "list":
        list_tables(dynamodb, args.header)
    elif args.mode == "create":
        create_table(dynamodb, args.config, args.wait)
    elif args.mode == "update":
        update_table(dynamodb, args.table, args.config, args.wait)
    elif args.mode == "delete":
        delete_table(dynamodb, args.table, args.wait)
    elif args.mode == "describe":
        describe_table(dynamodb, args.table)


if __name__ == "__main__":
    run_gracefully(run)
