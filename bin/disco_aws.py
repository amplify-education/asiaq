#!/usr/bin/env python
"""
Command line tool for working with EC2 instances.
"""
from __future__ import print_function
import sys
import argparse
import csv
from datetime import datetime
from ConfigParser import NoOptionError

from dateutil import parser as dateutil_parser
from tabulate import tabulate
from collections import defaultdict

from disco_aws_automation import DiscoAWS, DiscoBake, read_config
from disco_aws_automation.resource_helper import TimeoutError
from disco_aws_automation.disco_logging import configure_logging
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.exceptions import SmokeTestError


# R0912 Allow more than 12 branches so we can parse a lot of commands..
# R0914 Allow more than 15 local variables so we can parse a lot of commands..
# R0915 Allow more than 50 statements so we can parse a lot of commands..
# pylint: disable=R0912,R0914,R0915
def get_parser():
    '''Returns command line parser'''
    parser = argparse.ArgumentParser(description='Disco AWS automation')
    parser.add_argument('--debug', dest='debug', action='store_const', const=True, default=False,
                        help='Log in debug level.')
    parser.add_argument('--truncate', dest='truncate', action='store_true',
                        help='Truncate extra long fields in output.')
    region_env_group = parser.add_mutually_exclusive_group()
    region_env_group.add_argument('--env', dest='env', type=str, default=None,
                                  help="Environment. Normally, the name of a VPC. " +
                                  "Default is taken from config file.")
    subparsers = parser.add_subparsers(help='Sub-command help')

    parser_provision = subparsers.add_parser(
        'provision', help='Set the Autoscaling configuration that manages a hostclass. Can be used to '
        'provision instances for a hostclass, scale a hostclass up or down, or change the AMI.')
    parser_provision.set_defaults(mode="provision")
    parser_provision.add_argument('--ami', dest='ami', required=False, help='AMI image id',
                                  type=str, default=None)
    parser_provision.add_argument('--hostclass', dest='hostclass', required=False, type=str, default=None)
    parser_provision.add_argument('--no-destroy', dest='no_destroy', action='store_const',
                                  const=True, default=False,
                                  help='If init fails do not terminate instance')
    parser_provision.add_argument('--instance-type', dest='instance_type', required=False,
                                  type=str, default=None)
    parser_provision.add_argument('--extra-space', dest='extra_space', required=False, type=int, default=None)
    parser_provision.add_argument('--extra-disk', dest='extra_disk', required=False, type=int, default=None)
    parser_provision.add_argument('--iops', dest='iops', required=False, type=int, default=None,
                                  help='Provisioned IOPs, only applies to the additional '
                                  'volume created by --extra-disk')
    parser_provision.add_argument('--no-smoke-test', dest='no_smoke', action='store_const',
                                  const=True, default=False, help='Disable smoke test')
    parser_provision.add_argument('--min-size', dest='min_size', required=False, type=str, default=None,
                                  help="Minimum number of instances in autoscaling group")
    parser_provision.add_argument('--max-size', dest='max_size', required=False, type=str, default=None,
                                  help="Maximum number of instances in autoscaling group")
    parser_provision.add_argument('--desired-size', dest='desired_size', required=False,
                                  type=str, default=None, help="Number of instances we want now")
    parser_provision.add_argument('--testing', dest='testing', action='store_const',
                                  const=True, default=False,
                                  help="Bring up host in testing mode (no fixed IP or special routing)")
    parser_provision.add_argument('--no-chaos', dest='no_chaos', action='store_const',
                                  const=True, default=None,
                                  help='Temporarily disable chaos')

    parser_listhosts = subparsers.add_parser('listhosts', help='List all hosts')
    parser_listhosts.set_defaults(mode="listhosts")
    parser_listhosts.add_argument('--hostclass', dest='hostclass', required=False, type=str,
                                  help='Filter by hostclass')
    parser_listhosts.add_argument('--state', dest='state', action='store_const', const=True, default=False,
                                  help='Current state of the instance: pending, running, shutting-down, etc')
    parser_listhosts.add_argument('--hostname', dest='hostname', action='store_const',
                                  const=True, default=False,
                                  help='Returns the hostname of this instance')
    parser_listhosts.add_argument('--owner', dest='owner', action='store_const', const=True, default=False,
                                  help='Who created the instance')
    parser_listhosts.add_argument('--productline', dest='productline', action='store_const', const=True,
                                  default=False, help='Which product line is the instance a part of')
    parser_listhosts.add_argument('--instance-type', dest='instance_type', action='store_const',
                                  const=True, default=False,
                                  help='What instance type is the instance?')
    parser_listhosts.add_argument('--private-ip', dest='private_ip', action='store_true',
                                  help="Display private IP")
    parser_listhosts.add_argument('--ami', dest='ami', action='store_true',
                                  help="Display AMI ID")
    parser_listhosts.add_argument('--smoke', dest='smoke', action='store_true',
                                  help="Display Smoketest status")
    parser_listhosts.add_argument('--ami-age', dest='ami_age', action='store_const',
                                  const=True, default=False,
                                  help='Display AMI age in hours')
    parser_listhosts.add_argument('--uptime', dest='uptime', action='store_const',
                                  const=True, default=False,
                                  help='Display instance age in hours')
    parser_listhosts.add_argument('--securitygroup', dest='securitygroup', action='store_const',
                                  const=True, default=False,
                                  help='Display instance security group')
    parser_listhosts.add_argument('--availability-zone', dest='availability_zone',
                                  action='store_const',
                                  const=True, default=False,
                                  help='Display availability zone')
    parser_listhosts.add_argument('--most', dest='most', action='store_const',
                                  const=True, default=False,
                                  help='Enables most extra info')
    parser_listhosts.add_argument('--all', dest='all', action='store_const',
                                  const=True, default=False,
                                  help='Enables all extra info')

    parser_terminate = subparsers.add_parser(
        'terminate', help='Terminate instance and discard EBS volume. Note that if the instance is managed '
        'by an Autoscaling group it will be automatically replaced by a new instance.')
    parser_terminate.set_defaults(mode="terminate")
    parser_terminate_group = parser_terminate.add_mutually_exclusive_group(required=True)
    parser_terminate_group.add_argument('--instance', dest='instances', default=[], action='append', type=str)
    parser_terminate_group.add_argument('--hostname', dest='hostnames', default=[], action='append', type=str)
    parser_terminate_group.add_argument('--hostclass', dest='hostclasses', default=[],
                                        action='append', type=str)
    parser_terminate_group.add_argument('--ami', dest='amis', default=[], action='append', type=str)

    parser_stop = subparsers.add_parser('stop', help='Stop (aka) shutdown instances')
    parser_stop.set_defaults(mode="stop")
    parser_stop_group = parser_stop.add_mutually_exclusive_group(required=True)
    parser_stop_group.add_argument('--instance', dest='instances', default=[], action='append', type=str)
    parser_stop_group.add_argument('--hostname', dest='hostnames', default=[], action='append', type=str)
    parser_stop_group.add_argument('--hostclass', dest='hostclasses', default=[], action='append', type=str)
    parser_stop_group.add_argument('--ami', dest='amis', default=[], action='append', type=str)

    parser_exec = subparsers.add_parser('exec', help='execute command on instance')
    parser_exec.set_defaults(mode="exec")
    parser_exec.add_argument('--command', dest='command', required=True)
    parser_exec.add_argument('--user', dest='user', required=True)
    parser_exec_group = parser_exec.add_mutually_exclusive_group(required=True)
    parser_exec_group.add_argument('--instance', dest='instances', default=[], action='append', type=str)
    parser_exec_group.add_argument('--hostname', dest='hostnames', default=[], action='append', type=str)
    parser_exec_group.add_argument('--hostclass', dest='hostclasses', default=[], action='append', type=str)
    parser_exec_group.add_argument('--ami', dest='amis', default=[], action='append', type=str)

    parser_isready = subparsers.add_parser(
        'isready', help="Checks if instances are ready (i.e instance is sshable and smoke tests passed)")
    parser_isready.set_defaults(mode="isready")
    parser_isready.add_argument('--hostname', dest='hostnames', default=[], action='append', type=str,
                                help="hostname of host to check")
    parser_isready.add_argument('--hostclass', dest='hostclasses', default=[], action='append', type=str,
                                help="hostclass of hosts to check")
    parser_isready.add_argument('--instance', dest='instances', default=[], action='append', type=str,
                                help="instance id of host to check")
    parser_isready.add_argument('--ami', dest='amis', default=[], action='append', type=str,
                                help="ami of hosts to check")

    parser_tag = subparsers.add_parser('tag', help='Tag a host')
    parser_tag.set_defaults(mode="tag")
    parser_tag.add_argument('--instance', dest='instances', required=True, default=[],
                            action='append', type=str)
    parser_tag.add_argument('--key', dest='key', required=True, type=str,
                            help='Name of the tag to set, update, or clear')
    parser_tag.add_argument('--value', dest='value', required=False, type=str,
                            help='Value to set. If not supplied, tag will be removed')

    parser_spinup = subparsers.add_parser(
        'spinup', help="Provision a set of hostclasses, as defined in a csv file")
    parser_spinup.set_defaults(mode="spinup")
    parser_spinup.add_argument('--pipeline', dest='pipeline_definition_file', required=True, type=str,
                               help="A csv file containing hostclass names, number of instances, "
                               "types of instances, extra-space requirements if any")
    parser_spinup.add_argument('--no-smoke-test', dest='no_smoke', action='store_const',
                               const=True, default=False, help='Disable smoke test')
    parser_spinup.add_argument('--stage', default=None,
                               help="which stage to use when searching for ami's, "
                               "overrides any env or env-type settings")
    parser_spinup.add_argument('--testing', dest='testing', action='store_const',
                               const=True, default=False,
                               help="Bring up host in testing mode (no fixed IP or special routing)")

    parser_spindown = subparsers.add_parser(
        'spindown', help="Spin down (terminate) a set of hostclasses, as defined in a csv file")
    parser_spindown.set_defaults(mode="spindown")
    parser_spindown.add_argument('--pipeline', dest='pipeline_definition_file', required=True, type=str,
                                 help="A csv file containing hostclasses")

    parser_spindownandup = subparsers.add_parser(
        'spindownandup', help="Spin down (terminate) a set of hostclasses, then spin them up again")
    parser_spindownandup.set_defaults(mode="spindownandup")
    parser_spindownandup.add_argument('--pipeline', dest='pipeline_definition_file', required=True, type=str,
                                      help="A csv file containing hostclasses")

    parser_get_hostclass_option = subparsers.add_parser(
        'gethostclassoption', help='Returns the value of an option for a particular hostclass')
    parser_get_hostclass_option.set_defaults(mode='gethostclassoption')
    parser_get_hostclass_option.add_argument('--hostclass', type=str, required=True, help='The hostclass')
    parser_get_hostclass_option.add_argument('--option', type=str, required=True, help='The option name')

    parser_promote = subparsers.add_parser(
        'promoterunning', help='Promote AMIs of instances that have been running for period of time.'
    )
    parser_promote.set_defaults(mode='promoterunning')
    parser_promote.add_argument('--hours', type=int, default=3)

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


def get_preferred_private_ip(instance):
    """
    The following preference is used:
     * the *second* network interface's private ip address (normally the static ip), if present
     * the first network interface's private ip
    """
    interfaces = instance.interfaces
    if len(interfaces) == 1:
        return interfaces[0].private_ip_address
    else:
        return interfaces[1].private_ip_address


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    parser = get_parser()
    args = parser.parse_args()
    configure_logging(args.debug)

    environment_name = args.env or config.get("disco_aws", "default_environment")

    aws = DiscoAWS(config, environment_name=environment_name)
    if args.mode == "provision":
        hostclass_dicts = [{
            "sequence": 1,
            "hostclass": args.hostclass,
            "instance_type": args.instance_type,
            "extra_space": args.extra_space,
            "extra_disk": args.extra_disk,
            "iops": args.iops,
            "smoke_test": "no" if args.no_smoke else "yes",
            "ami": args.ami,
            "min_size": args.min_size,
            "desired_size": args.desired_size,
            "max_size": args.max_size,
            "chaos": "no" if args.no_chaos else None
        }]
        aws.spinup(hostclass_dicts, testing=args.testing)
    elif args.mode == "listhosts":
        instances = aws.instances_from_hostclass(args.hostclass) if args.hostclass else aws.instances()
        instances_filtered = [i for i in instances if i.state != u"terminated"]
        instances_sorted = sorted(instances_filtered, key=lambda i: (i.state, i.tags.get("hostclass", "-"),
                                                                     i.tags.get("hostname", "-")))
        instance_to_private_ip = {i.id: get_preferred_private_ip(i) for i in instances_sorted}
        most = args.all or args.most

        if args.ami_age or args.uptime or most:
            bake = DiscoBake(config, aws.connection)
            ami_dict = bake.list_amis_by_instance(instances)
            now = datetime.utcnow()

        fields = (
            "id hostclass ip state hostname owner type ami smoke "
            "ami_age uptime private_ip az product sg"
        )

        instance_info = defaultdict(list)
        for instance in instances_sorted:
            instance_info["id"].append(instance.id)
            instance_info["hostclass"].append(instance.tags.get("hostclass", "-"))
            instance_info["ip"].append(instance.ip_address or instance_to_private_ip[instance.id])
            if args.state or most:
                instance_info["state"].append(instance.state)
            if args.hostname or most:
                instance_info["booted"].append("-" if instance.tags.get("hostname") is None else "y")
            if args.owner or most:
                instance_info["owner"].append(instance.tags.get("owner", u"-"))
            if args.instance_type or most:
                instance_info["type"].append(instance.instance_type)
            if args.ami or most:
                instance_info["ami"].append(instance.image_id)
            if args.smoke or most:
                instance_info["smoketest"].append("-" if instance.tags.get("smoketest") is None else "y")
            if args.ami_age or most:
                creation_time = bake.get_ami_creation_time(ami_dict.get(instance.id))
                instance_info["ami_age"].append(DiscoBake.time_diff_in_hours(now, creation_time))
            if args.uptime or most:
                launch_time = dateutil_parser.parse(instance.launch_time)
                now_with_tz = now.replace(tzinfo=launch_time.tzinfo)  # use a timezone-aware `now`
                instance_info["uptime"].append(DiscoBake.time_diff_in_hours(now_with_tz, launch_time))
            if args.private_ip or args.all:
                instance_info["private_ip"].append(instance_to_private_ip[instance.id])
            if args.availability_zone or args.all:
                instance_info["az"].append(instance.placement)
            if args.productline or args.all:
                productline = instance.tags.get("productline", u"unknown")
                instance_info["product"].append(productline if productline != u"unknown" else u"-")
            if args.securitygroup or args.all:
                instance_info["sg"].append(instance.groups[0].name)
        for key, data in instance_info.iteritems():
            data.insert(0, key)
        table = tabulate(instance_info, headers="firstrow", tablefmt="plain").split("\n", 1)
        print("\033[1m" + table[0] + "\033[0m", file=sys.stderr)
        print(table[1])

    elif args.mode == "terminate":
        instances = instances_from_args(aws, args)
        terminated_instances = aws.terminate(instances)
        print("Terminated: {0}".format(",".join([str(inst) for inst in terminated_instances])))
    elif args.mode == "stop":
        instances = instances_from_args(aws, args)
        stopped_instances = aws.stop(instances)
        print("Stopped: {0}".format(",".join([str(inst) for inst in stopped_instances])))
    elif args.mode == "exec":
        instances = instances_from_args(aws, args)
        exit_code = 0
        for instance in instances:
            _code, _stdout = aws.remotecmd(instance, [args.command], user=args.user, nothrow=True)
            sys.stdout.write(_stdout)
            exit_code = _code if _code else exit_code
        sys.exit(exit_code)
    elif args.mode == "isready":
        instances = instances_from_args(aws, args)
        if not instances:
            print("No instances found")
        ready_count = 0
        for instance in instances:
            name = "{0} {1}".format(instance.tags.get("hostname"), instance.id)
            print("Checking {0}...".format(name))
            try:
                aws.smoketest_once(instance)
                print("...{0} is ready".format(name))
                ready_count += 1
            except SmokeTestError:
                print("..{0} failed smoke test".format(name))
            except TimeoutError:
                print("...{0} is NOT ready".format(name))
        sys.exit(0 if ready_count == len(instances) else 1)
    elif args.mode == "tag":
        for instance in aws.instances(instance_ids=args.instances):
            instance.remove_tag(args.key)
            if args.value:
                instance.add_tag(args.key, args.value)
    elif args.mode == "spinup":
        with open(args.pipeline_definition_file, "r") as f:
            reader = csv.DictReader(f)
            hostclass_dicts = [line for line in reader]
        aws.spinup(hostclass_dicts, stage=args.stage, no_smoke=args.no_smoke, testing=args.testing)
    elif args.mode == "spindown":
        with open(args.pipeline_definition_file, "r") as f:
            reader = csv.DictReader(f)
            hostclasses = [line["hostclass"] for line in reader]
        aws.spindown(hostclasses)
    elif args.mode == "spindownandup":
        with open(args.pipeline_definition_file, "r") as f:
            reader = csv.DictReader(f)
            hostclass_dicts = [line for line in reader]
            hostclasses = [d["hostclass"] for d in hostclass_dicts]
        aws.spindown(hostclasses)
        aws.spinup(hostclass_dicts)
    elif args.mode == "gethostclassoption":
        try:
            print(aws.hostclass_option(args.hostclass, args.option))
        except NoOptionError:
            print("Hostclass %s doesn't have option %s." % (args.hostclass, args.option))
    elif args.mode == "promoterunning":
        aws.promote_running_instances_to_prod(args.hours * 60 * 60)

if __name__ == "__main__":
    run_gracefully(run)
