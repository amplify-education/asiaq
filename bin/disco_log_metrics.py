#!/usr/bin/env python
"""
Create metrics using CloudWatch Logs

Usage:
    disco_log_metrics.py [--debug] [--env ENV] list-metrics --hostclass HOSTCLASS
    disco_log_metrics.py [--debug] [--env ENV] list-groups --hostclass HOSTCLASS
    disco_log_metrics.py [--debug] [--env ENV] update --hostclass HOSTCLASS
    disco_log_metrics.py [--debug] [--env ENV] delete --hostclass HOSTCLASS
    disco_log_metrics.py (-h | --help)

Options:
    -h --help              Show this screen
    --debug                Log in debug level.
    --env ENV              Environment name (VPC name)
    --hostclass HOSTCLASS  Hostclass to run command for

Commands:
    update                 Update the log metrics for a hostclass from config
    delete                 Delete log metrics for a hostclass
    list-groups            List log groups for a hostclass
    list-metrics           List log metrics for a hostclass


"""

from __future__ import print_function
from docopt import docopt

from disco_aws_automation import DiscoLogMetrics
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


def run():
    """Parses command line and dispatches the commands"""
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    config = read_config()

    env = args.get("--env") or config.get("disco_aws", "default_environment")

    disco_log_metrics = DiscoLogMetrics(env)

    if args["update"]:
        disco_log_metrics.update(args['--hostclass'])
    elif args["delete"]:
        disco_log_metrics.delete_metrics(args['--hostclass'])
    elif args["list-metrics"]:
        for metric_filter in disco_log_metrics.list_metric_filters(args['--hostclass']):
            for metric in metric_filter['metricTransformations']:
                print("{0:<40} {1:10}".format(metric['metricNamespace'], metric['metricName']))
    elif args["list-groups"]:
        for group in disco_log_metrics.list_log_groups(args['--hostclass']):
            print("{0:<40} {1:10}".format(group['logGroupName'], format_bytes_to_mb(group['storedBytes'])))


def format_bytes_to_mb(num_bytes):
    """Format a size in bytes to a string with the number of megabytes"""
    return str(round(num_bytes / (1024.0 * 1024.0), 2)) + 'M'

if __name__ == "__main__":
    run_gracefully(run)
