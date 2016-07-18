#!/usr/bin/env python
"""
Applies monitoring and alerting configuration to live pipelines.

Usage:
    disco_alarms.py [--debug] [--dry-run] [--env ENV] update_notifications [--delete]
    disco_alarms.py [--debug] [--env ENV] update_metrics [--delete] --hostclass HOSTCLASS
    disco_alarms.py [--debug] [--env ENV] list [--hostclass HOSTCLASS]
    disco_alarms.py (-h | --help)

Commands:
     update_notifications     Updates SNS topics and subscriptions
     update_metrics           Updates CloudWatch metrics, triggers, and SNS links
     list                     List alarms

Options:
     -h --help              Show this screen
     --debug                Log in debug level
     --delete               Depending on command, deletes either SNS topics and
                            subscriptions or CloudWatch alarms.
                            Note: Upon re-subscription to SNS-topics each email
                            recipient will be required to re-confirm subscription.
     --dry-run              Only talks about changes, does not make them
     --env ENV              Environment name (VPC name)
     --hostclass HOSTCLASS  Restricts the update operation to a single hostclass
"""
from __future__ import print_function
import sys
import logging

from docopt import docopt

from disco_aws_automation import DiscoSNS, read_config
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.disco_alarm_config import DiscoAlarmsConfig
from disco_aws_automation.disco_alarm import DiscoAlarm
from disco_aws_automation.disco_elasticsearch import DiscoElasticsearch


def run():
    """Parses command line and dispatches the commands"""
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    config = read_config()

    dry_run = args.get("--dry-run")
    delete = args.get("--delete")
    hostclass = args.get("--hostclass")
    env = args.get("--env") or config.get("disco_aws", "default_environment")
    disco_elasticsearch = DiscoElasticsearch(env)
    alarms_config = DiscoAlarmsConfig(env, elasticsearch=disco_elasticsearch)
    disco_alarm = DiscoAlarm(env, alarm_configs=alarms_config)

    if args["update_notifications"]:
        notifications = alarms_config.get_notifications()
        DiscoSNS().update_sns_with_notifications(notifications, env, delete=delete, dry_run=dry_run)
    elif args["update_metrics"]:
        if delete:
            disco_alarm.delete_hostclass_environment_alarms(env, hostclass)
        disco_alarm.create_alarms(hostclass)
    elif args["list"]:
        alarms = disco_alarm.get_alarms(
            {"env": env, "hostclass": hostclass} if hostclass else {"env": env})
        for alarm in alarms:
            print(alarm)
    else:
        logging.error("No command specified. See --help")
        sys.exit(1)

if __name__ == "__main__":
    run_gracefully(run)
