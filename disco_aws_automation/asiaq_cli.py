"""
Code for a conventional entry-point based command-line interface.
"""

import argparse
import os
from logging import getLogger

import boto3
import pytz

from . import DiscoVPC, DiscoAWS
from .disco_aws_util import read_pipeline_file, graceful
from .disco_logging import configure_logging
from .disco_config import read_config, normalize_path
from .disco_dynamodb import AsiaqDynamoDbBackupManager
from .disco_datapipeline import AsiaqDataPipelineManager
from .exceptions import AsiaqConfigError


class CliCommand(object):
    """
    Abstract(ish) base class for CLI subcommand drivers.  Common behaviors of all subcommands
    can be implemented here, but of course there's no real guarantee that they'll be honored.

    Required elements in subclasses:

    * a static field called DESCRIPTION, which will be used in arg parsers
    * a class or static method called "init_args", which takes an ArgumentParser as its
      input and adds to it whatever arguments and options this command needs.
    """

    DESCRIPTION = "This command has no description.  Perhaps you should add one?"

    def __init__(self, args):
        self.args = args
        self.logger = getLogger(type(self).__name__)
        self._aws_config = None

    @property
    def config(self):
        "Auto-populate and return an AsiaqConfig for the standard configuration file."
        if not self._aws_config:
            self._aws_config = read_config(environment=self.args.env)
        return self._aws_config

    def run(self):
        "Run the current command, based on the arguments passed in at initialization time."
        raise Exception("This is an abstract method. Override it so this command does something useful!")

    @classmethod
    def init_args(cls, parser):
        """
        Set up any arguments and options for this command/subcommand.
        """
        pass


class SandboxCommand(CliCommand):
    """
    Command to manage sandboxes (currently, just creates, but should get other functions later).
    """

    DESCRIPTION = "Create and populate a sandbox for local development and testing."

    @classmethod
    def init_args(cls, parser):
        parser.add_argument("sandbox_name", help="Name of the sandbox VPC to create or update.")

    def run(self):
        self.logger.debug("Updating sandbox %s", self.args.sandbox_name)
        sandbox_name = self.args.sandbox_name
        pipeline_file = os.path.join("sandboxes", sandbox_name, "pipeline.csv")

        hostclass_dicts = read_pipeline_file(pipeline_file)

        self._update_s3_configs(sandbox_name)

        self.logger.info("Checking if environment '%s' already exists", sandbox_name)
        vpc = DiscoVPC.fetch_environment(environment_name=sandbox_name)
        if vpc:
            self.logger.info("Sandbox %s already exists: updating it.", sandbox_name)
            vpc.update()
        else:
            vpc = DiscoVPC(environment_name=sandbox_name,
                           environment_type='sandbox',
                           defer_creation=True)
            vpc.create()

        self.logger.debug("Hostclass definitions for spin-up: %s", hostclass_dicts)
        DiscoAWS(self.config, vpc=vpc).spinup(hostclass_dicts)

    def _update_s3_configs(self, sandbox_name):
        config_sync_option = self.config.get_asiaq_option('sandbox_sync_config', required=False)
        bucket_name = self.config.get_asiaq_option('sandbox_config_bucket', required=False)
        if not config_sync_option:
            return
        elif not bucket_name:
            raise AsiaqConfigError("Sandbox configuration sync requested, but no bucket configured.")
        s3_bucket = boto3.resource("s3").Bucket(name=bucket_name)
        for sync_line in config_sync_option.split("\n"):
            local_name, remote_dir = sync_line.split()
            local_config_path = normalize_path("sandboxes", sandbox_name, local_name)
            remote_config_path = os.path.join(remote_dir, sandbox_name)
            self.logger.info("Uploading config file file %s to %s", local_config_path, remote_config_path)
            s3_bucket.upload_file(local_config_path, remote_config_path)


class DynamoDbBackupCommand(CliCommand):
    """
    CliCommand implementation for managing DynamoDB backups and backup pipelines.
    """
    DESCRIPTION = "Manage dynamodb backups and the pipelines that create them."

    @classmethod
    def init_args(cls, parser):
        subsub = parser.add_subparsers(title="data pipeline commands", dest="dp_command")
        subsub.add_parser("init", help="Set up bucket for backup and log data.")

        backup_parser = subsub.add_parser("backup",
                                          help="Configure backup to S3 for a dynamodb table")
        restore_parser = subsub.add_parser("restore", help="Restore a dynamodb table from an S3 backup")
        for parser in [backup_parser, restore_parser]:
            parser.add_argument("table_name")
            parser.add_argument("--force-reload", action='store_true',
                                help="Force recreation of the pipeline content")
            parser.add_argument("--metanetwork", metavar="NAME",
                                help="Metanetwork in which to launch pipeline assets")

        restore_parser.add_argument("--from", dest="backup_dir",
                                    help="Previous backup to restore from (default: latest)")
        list_parser = subsub.add_parser("list", help="List existing backups")
        list_parser.add_argument("table_name")

    def run(self):
        dispatch = {
            'init': self._create_bucket,
            'list': self._list,
            'backup': self._create_backup,
            'restore': self._restore_backup,
        }
        mgr = AsiaqDynamoDbBackupManager(config=self.config)
        dispatch[self.args.dp_command](mgr)

    def _create_bucket(self, mgr):
        mgr.init_bucket()

    def _restore_backup(self, mgr):
        mgr.restore_backup(self.args.table_name, self.args.backup_dir,
                           force_update=self.args.force_reload, metanetwork=self.args.metanetwork)

    def _create_backup(self, mgr):
        mgr.create_backup(self.args.table_name, force_update=self.args.force_reload,
                          metanetwork=self.args.metanetwork)

    def _list(self, mgr):
        backups = mgr.list_backups(self.config.environment, self.args.table_name)
        for backup in backups:
            print backup


class DataPipelineCommand(CliCommand):
    """
    CliCommand implementation for managing data pipelines.
    """
    DESCRIPTION = "Inspect and manage data pipelines."

    @classmethod
    def init_args(cls, parser):
        subsub = parser.add_subparsers(title="data pipeline commands", dest="dp_command")
        list_parser = subsub.add_parser("list", help="List available pipelines")
        list_parser.add_argument("--pipeline-name", dest="search_name", help="Find pipelines with this name.")
        list_parser.add_argument("--all-envs", dest="ignore_env", action='store_true',
                                 help="List pipelines in any (or no) environment.")
        list_parser.add_argument("--health", action='store_true', help="Print pipeline health status.")
        list_parser.add_argument("--state", action='store_true', help="Print pipeline readiness state.")
        list_parser.add_argument("--create-date", action='store_true',
                                 help="Print last creation date for this pipeline.")
        list_parser.add_argument("--last-run", action='store_true',
                                 help="Print last start date for this pipeline.")
        list_parser.add_argument("--desc", action='store_true', help="Print pipeline descriptions.")

        delete_parser = subsub.add_parser("delete", help="Delete an existing pipeline")
        delete_parser.add_argument("pipeline_id", help="AWS ID of the pipeline to delete")

    def run(self):
        mgr = AsiaqDataPipelineManager()
        dispatch = {
            'list': self._search,
            'delete': self._delete
        }
        dispatch[self.args.dp_command](mgr)

    def _search(self, mgr):
        tags = {}
        if not self.args.ignore_env:
            tags['environment'] = self.config.environment
        found = mgr.search_descriptions(name=self.args.search_name, tags=tags)
        tzname = self.config.get_asiaq_option("user_timezone", default="US/Eastern", required=False)
        user_tz = pytz.timezone(tzname)
        for record in found:
            output = [record._id, record._name]
            if self.args.health:
                output.append(record.health or "N/A")
            if self.args.state:
                output.append(record.pipeline_state)
            if self.args.create_date:
                output.append(record.create_date.astimezone(user_tz).isoformat())
            if self.args.last_run:
                last_run = record.last_run
                output.append(last_run.astimezone(user_tz).isoformat() if last_run else "NEVER")
            if self.args.desc:
                output.append(record._description or "")

            print "\t".join(output)

    def _delete(self, mgr):
        pipeline = mgr.fetch(self.args.pipeline_id)
        self.logger.info("Deleting pipeline %s", pipeline._name)
        mgr.delete(pipeline)


SUBCOMMANDS = {
    "dp": DataPipelineCommand,
    "ddb_backup": DynamoDbBackupCommand,
    "sandbox": SandboxCommand
}


@graceful
def super_command():
    """
    Driver function for the 'asiaq' command.
    """
    parser = argparse.ArgumentParser(description="All the Asiaq Things")
    _base_arg_init(parser)
    subcommands = parser.add_subparsers(title="subcommands", dest="command")
    for subcommand, driver in SUBCOMMANDS.items():
        sub_parser = subcommands.add_parser(subcommand,
                                            help=driver.DESCRIPTION, description=driver.DESCRIPTION)
        driver.init_args(sub_parser)
    args = parser.parse_args()
    configure_logging(debug=args.debug)

    if args.command:
        SUBCOMMANDS[args.command](args).run()


def _command_init(description, argparse_setup_func):
    parser = argparse.ArgumentParser(description=description)
    _base_arg_init(parser)
    argparse_setup_func(parser)
    args = parser.parse_args()
    configure_logging(debug=args.debug)
    return args


def _base_arg_init(parser):
    parser.add_argument("--debug", "-d", action='store_const', const=True,
                        help='Log at DEBUG level.')
    parser.add_argument("--env", "--environment",
                        help="Environment (VPC name, usually). Default: found in config.")


def _create_command(driver_class, func_name):
    @graceful
    def generic_command():
        "sacrificial docstring (overwritten below)"
        args = _command_init(driver_class.DESCRIPTION, driver_class.init_args)
        driver = driver_class(args)
        driver.run()
    generic_command.__name__ = func_name
    generic_command.__doc__ = "Driver function that runs the command in " + driver_class.__name__
    return generic_command

sandbox_command = _create_command(SandboxCommand, "sandbox_command")
