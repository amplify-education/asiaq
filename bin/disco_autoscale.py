#!/usr/bin/env python
"""
Command line tool for working with autoscaling groups and launch configurations.
"""

from __future__ import print_function
import argparse
import sys

from collections import defaultdict

from disco_aws_automation import DiscoAutoscale
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


def parse_arguments():
    """Read in options passed in over command line"""
    parser = argparse.ArgumentParser(description='Disco autoscaling automation')
    parser.add_argument('--debug', dest='debug', action='store_const',
                        const=True, default=False, help='Log in debug level.')
    parser.add_argument('--env', dest='env', type=str, default=None,
                        help="Environment. Normally, the name of a VPC. Default is taken from config file.")
    subparsers = parser.add_subparsers(help='Sub-command help')

    # Autoscaling group commands

    parser_list_groups = subparsers.add_parser('listgroups', help='List all autoscaling groups')
    parser_list_groups.set_defaults(mode="listgroups")

    parser_clean_groups = subparsers.add_parser('cleangroups', help='Delete unused autoscaling groups')
    parser_clean_groups.set_defaults(mode="cleangroups")

    parser_delete_group = subparsers.add_parser('deletegroup', help='Delete autoscaling group')
    parser_delete_group.set_defaults(mode="deletegroup")
    parser_delete_group.add_argument("--force", action='store_true',
                                     required=False, default=False, help='Force deletion')
    parser_delete_specifier_group = parser_delete_group.add_mutually_exclusive_group(required=True)
    parser_delete_specifier_group.add_argument("--hostclass", default=None, help='Name of the hostclass')
    parser_delete_specifier_group.add_argument("--name", default=None,
                                               help='Name of the autoscaling group')

    # Launch Configuration commands

    parser_list_configs = subparsers.add_parser('listconfigs', help='List all launch configurations')
    parser_list_configs.set_defaults(mode="listconfigs")

    parser_clean_configs = subparsers.add_parser('cleanconfigs', help='Delete unused launch configurations')
    parser_clean_configs.set_defaults(mode="cleanconfigs")

    parser_delete_config = subparsers.add_parser('deleteconfig', help='Delete launch configuration')
    parser_delete_config.set_defaults(mode="deleteconfig")
    parser_delete_config.add_argument("--config", required=True, help='Name of launch configuration')

    # Autoscaling policy commands

    parser_list_policies = subparsers.add_parser('listpolicies', help='List all autoscaling policies')
    parser_list_policies.set_defaults(mode="listpolicies")
    parser_list_policies.add_argument(
        "--group-name",
        help='Name of the autoscaling group'
    )
    parser_list_policies.add_argument(
        "--policy-names",
        action='append',
        help='Name of the autoscaling policy, or it\'s ARN'
    )
    parser_list_policies.add_argument(
        "--policy-types",
        choices=['SimpleScaling', 'StepScaling'],
        action='append',
        help='Type of scaling policies to list.'
    )

    parser_create_policy = subparsers.add_parser('createpolicy', help='Create autoscaling policy')
    parser_create_policy.set_defaults(mode="createpolicy")
    parser_create_policy.add_argument(
        "--group-name",
        required=True,
        help='Name of autoscaling group'
    )
    parser_create_policy.add_argument(
        "--policy-name",
        required=True,
        help='Name of autoscaling policy'
    )
    parser_create_policy.add_argument(
        "--policy-type",
        required=True,
        choices=['SimpleScaling', 'StepScaling'],
        default='SimpleScaling',
        help='Which scaling type to use?'
    )
    parser_create_policy.add_argument(
        "--adjustment-type",
        required=True,
        choices=['ChangeInCapacity', 'ExactCapacity', 'PercentChangeInCapacity'],
        help='How should the scaling adjustment be interpreted?'
    )
    parser_create_policy.add_argument(
        "--scaling-adjustment",
        type=int,
        help='By how much should the ASG be modified? Can be a positive or negative integer. Used with '
        'SimpleScaling.'
    )
    parser_create_policy.add_argument(
        "--min-adjustment-magnitude",
        type=int,
        help='When `--adjustment-type` is "PercentChangeInCapacity", what is the minimum number of instances '
        'that should be modified?'
    )
    parser_create_policy.add_argument(
        "--metric-aggregation-type",
        choices=['Average', 'Minimum', 'Maximum'],
        help='What is the aggregation type of the metric feeding this policy? Used with StepScaling.'
    )
    parser_create_policy.add_argument(
        "--step-adjustments",
        action='append',
        help='The steps by which to adjust the autoscaling group. Used with StepScaling. Repeatable. '
        'Format: MetricIntervalLowerBound=<float>,MetricIntervalUpperBound=<float>,ScalingAdjustment=<int> '
        'Example: MetricIntervalLowerBound=34.0,MetricIntervalUpperBound=45.8,ScalingAdjustment=5'
    )
    parser_create_policy.add_argument(
        "--cooldown",
        default=600,
        type=int,
        help='Cooldown (sec) before policy can trigger again. Used with SimpleScaling (default: %(default)s)'
    )
    parser_create_policy.add_argument(
        "--estimated-instance-warmup",
        default=300,
        type=int,
        help='Estimated time (sec) for an instance to boot and begin serving traffic. Used with StepScaling. '
        '(default: %(default)s)'
    )

    parser_delete_policy = subparsers.add_parser('deletepolicy', help='Delete autoscaling policy')
    parser_delete_policy.set_defaults(mode="deletepolicy")
    parser_delete_policy.add_argument("--policy_name", required=True, help='Name of autoscaling policy')
    parser_delete_policy.add_argument("--group_name", required=True, help='Name of autoscaling group')

    return parser.parse_args()


# R0912 Allow more than 12 branches and more than 15 local variables so we can have shoddily structured code
# pylint: disable=R0912, R0914
def run():
    """Parses command line and dispatches the commands"""
    config = read_config()
    args = parse_arguments()
    configure_logging(args.debug)

    environment_name = args.env or config.get("disco_aws", "default_environment")

    autoscale = DiscoAutoscale(environment_name)

    # Autoscaling group commands
    if args.mode == "listgroups":
        format_str = "{0} {1:12} {2:3} {3:3} {4:3} {5:3}"
        groups = autoscale.get_existing_groups()
        instances = autoscale.get_instances()
        if args.debug:
            print(format_str.format(
                "Name".ljust(35 + len(environment_name)), "AMI", "min", "des", "max", "cnt"))
        for group in groups:
            launch_cfg = list(autoscale.get_configs(names=[group.launch_config_name]))
            image_id = launch_cfg[0].image_id if len(launch_cfg) else ""
            group_str = group.name.ljust(35 + len(environment_name))
            group_cnt = len([instance for instance in instances if instance.group_name == group.name])
            print(format_str.format(group_str, image_id,
                                    group.min_size, group.desired_capacity, group.max_size,
                                    group_cnt))
    elif args.mode == "cleangroups":
        autoscale.clean_groups()
    elif args.mode == "deletegroup":
        autoscale.delete_groups(hostclass=args.hostclass, group_name=args.name, force=args.force)

    # Launch Configuration commands
    elif args.mode == "listconfigs":
        for config in autoscale.get_configs():
            print("{0:24} {1}".format(config.name, config.image_id))
    elif args.mode == "cleanconfigs":
        autoscale.clean_configs()
    elif args.mode == "deleteconfig":
        autoscale.delete_config(args.config)

    # Scaling policy commands
    elif args.mode == "listpolicies":
        policies = autoscale.list_policies(
            group_name=args.group_name,
            policy_types=args.policy_types,
            policy_names=args.policy_names
        )
        print_table(
            policies,
            headers=[
                'ASG',
                'Name',
                'Type',
                'Adjustment Type',
                'Scaling Adjustment',
                'Min Adjustment',
                'Cooldown',
                'Step Adjustments',
                'Warmup',
                'Alarms'
            ]
        )
    elif args.mode == "createpolicy":
        # Parse out the step adjustments, if provided.
        if args.step_adjustments:
            allowed_keys = ['MetricIntervalLowerBound', 'MetricIntervalUpperBound', 'ScalingAdjustment']
            parsed_steps = []
            for step in args.step_adjustments:
                parsed_step = {}
                for entry in step.split(','):
                    key, value = entry.split('=', 1)
                    if key not in allowed_keys:
                        raise Exception(
                            'Unable to parse step {0}, key {1} not in {2}'.format(step, key, allowed_keys)
                        )
                    parsed_step[key] = value
                parsed_steps.append(parsed_step)
        else:
            parsed_steps = []

        autoscale.create_policy(
            group_name=args.group_name,
            policy_name=args.policy_name,
            policy_type=args.policy_type,
            adjustment_type=args.adjustment_type,
            min_adjustment_magnitude=args.min_adjustment_magnitude,
            scaling_adjustment=args.scaling_adjustment,
            cooldown=args.cooldown,
            metric_aggregation_type=args.metric_aggregation_type,
            step_adjustments=parsed_steps,
            estimated_instance_warmup=args.estimated_instance_warmup
        )
    elif args.mode == "deletepolicy":
        autoscale.delete_policy(args.policy_name, args.group_name)

    sys.exit(0)


def print_table(rows, headers=None, space_between_columns=4):
    """
    Convenience method for printing a list of dictionary objects into a table. Automatically sizes the
    columns to be the maximum size of any entry in the dictionary, and adds additional buffer whitespace.

    Params:
        rows -                  A list of dictionaries representing a table of information, where keys are the
                                headers of the table. Ex. { 'Name': 'John', 'Age': 23 }

        headers -               A list of the headers to print for the table. Must be a subset of the keys of
                                the dictionaries that compose the row. If a header isn't present or it's value
                                has a falsey value, the value printed is '-'.

        space_between_columns - The amount of space between the columns of text. Defaults to 4.
    """
    columns_to_sizing = defaultdict(int)
    format_string = ''

    headers = headers or rows[0].keys()

    for row in rows:
        for header in headers:
            value = row.get(header, '-')
            columns_to_sizing[header] = max(len(str(value)), columns_to_sizing[header])

    for header in headers:
        column_size = max(columns_to_sizing[header], len(header)) + space_between_columns
        format_string += '{' + header + ':<' + str(column_size) + '}'

    print(format_string.format(**{key: key for key in headers}), file=sys.stderr)

    for row in rows:
        defaulted_row = {header: row.get(header) or '-' for header in headers}
        print(format_string.format(**defaulted_row))


if __name__ == "__main__":
    run_gracefully(run)
