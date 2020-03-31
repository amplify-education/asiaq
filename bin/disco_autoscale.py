#!/usr/bin/env python
"""
Command line tool for working with autoscaling groups and launch configurations.
"""

from __future__ import print_function

import argparse
import sys

from bin import print_table
from disco_aws_automation import DiscoGroup
from disco_aws_automation.disco_aws_util import run_gracefully, is_truthy
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

    discogroup = DiscoGroup(environment_name)

    # Autoscaling group commands
    if args.mode == "listgroups":
        format_str = "{0} {1:21} {2:3} {3:3} {4:3} {5:3} {6:4} {7:10}"
        groups = discogroup.list_groups()
        if args.debug:
            print(
                format_str.format(
                    "Name".ljust(35 + len(environment_name)),
                    "AMI",
                    "min",
                    "des",
                    "max",
                    "cnt",
                    "type",
                    "is_testing"
                )
            )
        for group in groups:
            print(
                format_str.format(
                    group['name'].ljust(40 + len(environment_name)),
                    group['image_id'],
                    group['min_size'],
                    group['desired_capacity'],
                    group['max_size'],
                    group['group_cnt'],
                    group['type'],
                    'y' if is_truthy(group['tags'].get('is_testing')) else 'n'
                )
            )

    elif args.mode == "cleangroups":
        discogroup.delete_groups()
    elif args.mode == "deletegroup":
        discogroup.delete_groups(hostclass=args.hostclass, group_name=args.name, force=args.force)

    # Launch Configuration commands
    elif args.mode == "listconfigs":
        for config in discogroup.get_configs():
            print("{0:24} {1}".format(config['LaunchConfigurationName'], config['ImageId']))
    elif args.mode == "cleanconfigs":
        discogroup.clean_configs()
    elif args.mode == "deleteconfig":
        discogroup.delete_config(args.config)

    # Scaling policy commands
    elif args.mode == "listpolicies":
        policies = discogroup.list_policies(
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

        discogroup.create_policy(
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
        discogroup.delete_policy(args.policy_name, args.group_name)

    sys.exit(0)


if __name__ == "__main__":
    run_gracefully(run)
