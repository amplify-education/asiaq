"""
RDS Module. Can be used to perform various RDS operations
"""

from __future__ import print_function
import os
import datetime
import logging
import time
import sys
import threading
from ConfigParser import ConfigParser, NoOptionError, NoSectionError

import boto3
import botocore
import pytz

from . import read_config, ASIAQ_CONFIG
from .disco_alarm import DiscoAlarm
from .disco_aws_util import is_truthy
from .disco_creds import DiscoS3Bucket
from .disco_route53 import DiscoRoute53
from .disco_vpc_sg_rules import DiscoVPCSecurityGroupRules
from .exceptions import TimeoutError, RDSEnvironmentError
from .resource_helper import keep_trying, tag2dict, throttled_call

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE_RDS = "disco_rds.ini"
RDS_STATE_POLL_INTERVAL = 30  # seconds
RDS_DELETE_TIMEOUT = 1800  # seconds. From observation, it takes about 15-20 mins to delete an RDS instance
RDS_SNAPSHOT_DELETE_TIMEOUT = 60  # seconds. From observation, this takes approximately 0 seconds
RDS_STARTUP_TIMEOUT = 600  # seconds. Time we allow RDS to get IP address before we give up
RDS_RESTORE_TIMEOUT = 1200  # seconds. Time we allow RDS to be restored and available to try and modify it
DEFAULT_LICENSE = {
    'oracle': 'bring-your-own-license',
    'postgres': 'postgresql-license'
}
DEFAULT_PORT = {
    'postgres': 5432,
    'oracle': 1521
}


class RDS(threading.Thread):
    """Class for spinning up a single rds instance"""

    def __init__(self, env_name, database_identifier, rds_security_group_id, subnet_ids, domain_name):
        """Initialize class"""
        threading.Thread.__init__(self, name=database_identifier)
        self.client = boto3.client('rds')
        self.vpc_name = env_name
        self.database_name = RDS.get_database_name(env_name, database_identifier)
        self.config_aws = read_config()
        self.config_rds = read_config(config_file=DEFAULT_CONFIG_FILE_RDS)
        self.rds_security_group_id = rds_security_group_id
        self.subnet_ids = subnet_ids
        self.domain_name = domain_name

    def spinup_alarms(self, database_name):
        """
        Configure alarms for this RDS instance. The alarms are configured in disco_alarms.ini
        """
        logger.debug("Configuring Cloudwatch alarms ")
        DiscoAlarm(self.vpc_name).create_alarms(database_name)

    def _get_instance_address(self, instance_identifier):
        """
        Obtains the instance end point for the given RDS instance
        """
        instance_info = self.client.describe_db_instances(DBInstanceIdentifier=instance_identifier)
        return instance_info['DBInstances'][0]['Endpoint']['Address']

    def setup_dns(self, instance_identifier):
        """
        Setup Domain Name Lookup using Route 53
        """
        start_time = time.time()
        instance_endpoint = keep_trying(RDS_STARTUP_TIMEOUT, self._get_instance_address, instance_identifier)
        logger.info("Waited %s seconds for RDS to get an address", time.time() - start_time)
        disco_route53 = DiscoRoute53()
        instance_record_name = '{0}.{1}.'.format(instance_identifier, self.domain_name)

        # Delete and recreate DNS record for this Instance
        disco_route53.delete_record(self.domain_name, instance_record_name, 'CNAME')
        disco_route53.create_record(self.domain_name, instance_record_name, 'CNAME', instance_endpoint)

    @staticmethod
    def get_database_name(env_name, instance_identifier):
        """ Extract the database name from the instance_identifier """
        identifier_prefix = len(env_name + '-')
        return instance_identifier[identifier_prefix:]

    @staticmethod
    def delete_keys(dictionary, keys):
        """Returns a copy of the given dict, with the given keys deleted"""
        copy = dictionary.copy()
        for key in keys:
            if key in copy:
                del copy[key]
        return copy

    @staticmethod
    def config_with_default(config, section, param, default=None):
        """Read the RDS config file and extract the parameter value, or return default if missing"""
        try:
            return config.get(section, param)
        except NoOptionError:
            return default

    @staticmethod
    def config_integer(config, section, param, default=None):
        """Read the config file and return the integer value else return default"""
        try:
            return int(config.get(section, param))
        except NoOptionError:
            if default is not None:
                return int(default)
            else:
                raise

    @staticmethod
    def config_truthy(config, section, param, default='True'):
        """Read the RDS config file and extract the boolean value, or return default if missing"""
        return is_truthy(RDS.config_with_default(config, section, param, default))

    @staticmethod
    def get_instance_identifier(env_name, database_name):
        """Returns the database identifier. e.g. ci-testdb"""
        return env_name + '-' + database_name

    @staticmethod
    def get_db_parameter_group_family(engine, engine_version):
        """
        Extract the DB Parameter Group Family from Engine and Engine Version

        Valid parameter group families are set based on the engine name (in lower case, if
        applicable) and the major and minor versions of the DB (patchlevel and further
        subdivisions of release version are ignored).
        The rules here are heuristic, and may need to be tweaked.

        Rules:
          * oracle/sqlserver (engines that contain dashes): {engine}-{major}.{minor}
          * others (no dashes): {engine}{major}.{minor}
        """
        engine_version_list = engine_version.split('.', 2)
        format_string = "{0}-{1}.{2}" if "-" in engine else "{0}{1}.{2}"
        return format_string.format(engine.lower(), engine_version_list[0], engine_version_list[1])

    def get_master_password(self, env_name, database_name):
        """
        Get the Master Password for instance stored in the S3 bucket
        """
        from .disco_vpc import DiscoVPC
        bucket_name = DiscoVPC.get_credential_buckets_from_env_name(self.config_aws, self.vpc_name)[0]
        bucket = DiscoS3Bucket(bucket_name)

        # for backwards compatibility check the old style keys containing the env name
        instance_identifier = RDS.get_instance_identifier(env_name, database_name)
        s3_password_old_key = 'rds/{0}/master_user_password'.format(instance_identifier)
        if bucket.key_exists(s3_password_old_key):
            return bucket.get_key(s3_password_old_key)

        s3_password_new_key = 'rds/{0}/master_user_password'.format(database_name)
        return bucket.get_key(s3_password_new_key)

    def get_latest_snapshot(self, db_instance_identifier):
        """
        Returns the information on the most recent DB Snapshot. This can be used to restore a deleted
        DB instance
        """
        try:
            response = throttled_call(
                self.client.describe_db_snapshots,
                DBInstanceIdentifier=db_instance_identifier
            )

            snapshots = sorted(
                response.get('DBSnapshots', []),
                key=lambda k: k['SnapshotCreateTime'],
                reverse=True
            )

            if snapshots:
                return snapshots[0]
            else:
                return None

        except botocore.exceptions.ClientError:
            return None

    def create_db_instance(self, instance_params, custom_snapshot=None):
        """Creates the Relational database instance
        If a snapshot is provided then we restore that snapshot
        If a final snapshot exists for the given DB Instance ID, We restore from the final snapshot
        If one doesn't exist, we create a new DB Instance
        """
        instance_identifier = instance_params['DBInstanceIdentifier']
        snapshot = custom_snapshot or self.get_latest_snapshot(instance_identifier)

        if not snapshot:
            # For Postgres, We dont need this parameter at creation
            if instance_params['Engine'] == 'postgres':
                instance_params = RDS.delete_keys(instance_params, ["CharacterSetName"])

            logger.info("Creating new RDS cluster %s", instance_identifier)
            throttled_call(self.client.create_db_instance, **instance_params)
        else:
            logger.info("Restoring RDS cluster from snapshot: %s", snapshot["DBSnapshotIdentifier"])
            params = RDS.delete_keys(instance_params, [
                "AllocatedStorage", "CharacterSetName", "DBParameterGroupName", "StorageEncrypted",
                "EngineVersion", "MasterUsername", "MasterUserPassword", "VpcSecurityGroupIds",
                "BackupRetentionPeriod", "PreferredMaintenanceWindow", "PreferredBackupWindow"])
            params["DBSnapshotIdentifier"] = snapshot["DBSnapshotIdentifier"]
            throttled_call(self.client.restore_db_instance_from_db_snapshot, **params)
            keep_trying(RDS_RESTORE_TIMEOUT, self.modify_db_instance, instance_params)

    def modify_db_instance(self, instance_params, apply_immediately=True):
        """
        Modify settings for a DB instance. You can change one or more database configuration parameters
        by specifying these parameters and the new values in the request.
        """
        logger.info("Updating RDS cluster %s", instance_params["DBInstanceIdentifier"])
        params = RDS.delete_keys(instance_params, [
            "Engine", "LicenseModel", "DBSubnetGroupName", "PubliclyAccessible",
            "MasterUsername", "Port", "CharacterSetName", "StorageEncrypted"])
        throttled_call(self.client.modify_db_instance, ApplyImmediately=apply_immediately, **params)
        logger.info("Rebooting cluster to apply Param group %s", instance_params["DBInstanceIdentifier"])
        keep_trying(RDS_STATE_POLL_INTERVAL,
                    self.client.reboot_db_instance,
                    DBInstanceIdentifier=instance_params["DBInstanceIdentifier"],
                    ForceFailover=False)

    def recreate_db_subnet_group(self, db_subnet_group_name):
        """
        Creates the DB Subnet Group. If it exists already, drops it and recreates it.
        DB subnet groups must contain at least one subnet in at least two AZs in the region.

        @db_subnet_group_name: String. The name for the DB subnet group.
                               This value is stored as a lowercase string.
        """
        try:
            throttled_call(self.client.delete_db_subnet_group, DBSubnetGroupName=db_subnet_group_name)
        except Exception as err:
            logger.debug("Not deleting subnet group '%s': %s", db_subnet_group_name, repr(err))

        db_subnet_group_description = 'Subnet Group for VPC {0}'.format(self.vpc_name)

        throttled_call(
            self.client.create_db_subnet_group,
            DBSubnetGroupName=db_subnet_group_name,
            DBSubnetGroupDescription=db_subnet_group_description,
            SubnetIds=self.subnet_ids
        )

    def get_instance_parameters(self, env_name, database_name):
        """Read the config file and extract the Instance related parameters"""
        section = instance_identifier = RDS.get_instance_identifier(env_name, database_name)
        db_engine = self.config_rds.get(section, 'engine')
        engine_family = db_engine.split('-')[0]
        default_license = DEFAULT_LICENSE.get(engine_family)
        default_port = DEFAULT_PORT.get(engine_family)
        preferred_backup_window = self.config_with_default(self.config_rds, section,
                                                           'preferred_backup_window',
                                                           None)
        preferred_maintenance_window = self.config_with_default(self.config_rds, section,
                                                                'preferred_maintenance_window',
                                                                None)

        instance_params = {
            'AllocatedStorage': self.config_integer(self.config_rds, section, 'allocated_storage'),
            'AutoMinorVersionUpgrade': self.config_truthy(self.config_rds,
                                                          section, 'auto_minor_version_upgrade'),
            'CharacterSetName': self.config_with_default(self.config_rds, section, 'character_set_name'),
            'DBInstanceClass': self.config_rds.get(section, 'db_instance_class'),
            'DBInstanceIdentifier': instance_identifier,
            'DBParameterGroupName': section,
            'DBSubnetGroupName': section,
            'Engine': db_engine,
            'EngineVersion': self.config_rds.get(section, 'engine_version'),
            'Iops': self.config_integer(self.config_rds, section, 'iops', 0),
            'LicenseModel': self.config_with_default(self.config_rds, section,
                                                     'license_model', default_license),
            'MasterUserPassword': self.get_master_password(env_name, database_name),
            'MasterUsername': self.config_rds.get(section, 'master_username'),
            'MultiAZ': self.config_truthy(self.config_rds, section, 'multi_az'),
            'Port': self.config_integer(self.config_rds, section, 'port', default_port),
            'PubliclyAccessible': self.config_truthy(self.config_rds, section,
                                                     'publicly_accessible', 'False'),
            'VpcSecurityGroupIds': [self.rds_security_group_id],
            'StorageEncrypted': self.config_truthy(self.config_rds, section, 'storage_encrypted'),
            'BackupRetentionPeriod': self.config_integer(self.config_rds, section,
                                                         'backup_retention_period', 1)
        }

        # If custom windows were set, use them. If windows are not specified, we will use the AWS defaults
        # instead.
        if preferred_backup_window:
            instance_params['PreferredBackupWindow'] = preferred_backup_window
        if preferred_maintenance_window:
            instance_params['PreferredMaintenanceWindow'] = preferred_maintenance_window

        return instance_params

    def _get_db_instance(self, instance_identifier):
        try:
            return throttled_call(self.client.describe_db_instances, DBInstanceIdentifier=instance_identifier)
        except botocore.exceptions.ClientError:
            return None

    def create_db_parameter_group(self, db_parameter_group_name, db_parameter_group_family):
        """
        Creates a new DB parameter group. Used to set customized parameters.

        A DB parameter group is initially created with the default parameters for the
        database engine used by the DB instance.
        To provide custom values for any of the parameters, you must modify the group after creating it.
        """
        throttled_call(
            self.client.create_db_parameter_group,
            DBParameterGroupName=db_parameter_group_name,
            DBParameterGroupFamily=db_parameter_group_family,
            Description='Custom params-{0}'.format(db_parameter_group_name)
        )

    def modify_db_parameter_group(self, db_parameter_group_name, parameters):
        """
        Modifies the parameters of a DB parameter group.

        Submit a list of the following:
        ('ParameterName', 'ParameterValue', 'Description', 'Source',
         'ApplyType', 'DataType', 'AllowedValues', 'IsModifiable',
         'MinimumEngineVersion', 'ApplyMethod')
        """
        for parameter in parameters:
            keep_trying(RDS_STATE_POLL_INTERVAL,
                        self.client.modify_db_parameter_group,
                        DBParameterGroupName=db_parameter_group_name,
                        Parameters=[{'ParameterName': parameter[0],
                                     'ParameterValue': parameter[1],
                                     'Description': 'Description',
                                     'Source': 'engine-default',
                                     'ApplyType': 'static',
                                     'DataType': 'string',
                                     'AllowedValues': 'somevalues',
                                     'IsModifiable': True,
                                     'MinimumEngineVersion': 'someversion',
                                     'ApplyMethod': 'pending-reboot'}])

    def recreate_db_parameter_group(self, env_name, database_name, db_parameter_group_name,
                                    db_parameter_group_family):
        """
        Check if there are any custom parameters for this instance
        Custom Parameters are set in ./rds/engine_specific/{instance_identifier}.ini
        If this file doesn't exist, we'll use default RDS parameters
        Args:
            env_name (str): The environment name to use when reading the config file.
                            This is only used in the section name of the config file.
                            The parameter group is always created for the current environment
            database_name (str): The database name such as 'txdb'
            db_parameter_group_name (str): Usually the same as the database identifier such as 'ci-txdb'
            db_parameter_group_family (str): Parameter group family such as 'oracle-se2-12.1'
        """
        try:
            throttled_call(
                self.client.delete_db_parameter_group,
                DBParameterGroupName=db_parameter_group_name
            )
        except Exception as err:
            logger.debug("Not deleting DB parameter group '%s': %s", db_parameter_group_name, repr(err))

        # DB Parameter Group Name must be created first, using RDS defaults
        self.create_db_parameter_group(db_parameter_group_name, db_parameter_group_family)

        # Extract the Custom Values from the config file
        custom_param_file = os.path.join(ASIAQ_CONFIG,
                                         'rds', 'engine_specific',
                                         '{0}.ini'.format(database_name))

        if os.path.isfile(custom_param_file):
            custom_config = ConfigParser()
            custom_config.read(custom_param_file)
            try:
                custom_db_params = custom_config.items(env_name)
                logger.info("Updating RDS db_parameter_group %s (family: %s, #params: %s)",
                            db_parameter_group_name, db_parameter_group_family, len(custom_db_params))
                self.modify_db_parameter_group(db_parameter_group_name, custom_db_params)
            except NoSectionError:
                logger.info("Using Default RDS param")

    def update_cluster(self):
        """
        Run the RDS Cluster update
        """
        instance_identifier = RDS.get_instance_identifier(self.vpc_name, self.database_name)
        instance_params = self.get_instance_parameters(self.vpc_name, self.database_name)

        if self._get_db_instance(instance_identifier):
            self.modify_db_instance(instance_params)
        else:
            self.recreate_db_subnet_group(instance_params["DBSubnetGroupName"])
            # Process the Engine-specific Parameters for the Instance
            group_name = instance_params["DBParameterGroupName"]
            group_family = self.get_db_parameter_group_family(
                instance_params["Engine"], instance_params["EngineVersion"])
            logger.debug("creating parameter group %s with family %s", group_name, group_family)
            self.recreate_db_parameter_group(self.vpc_name, self.database_name, group_name, group_family)
            self.create_db_instance(instance_params)

        # Create/Update CloudWatch Alarms for this instance
        self.spinup_alarms(self.database_name)

        # Create a DNS record for this instance
        self.setup_dns(instance_identifier)

    def clone(self, source_vpc, source_db):
        """
        Clone the database in source_vpc into the current vpc. The vpc name of the current
        database would be the same as the source database.
        """
        source_db_identifier = RDS.get_instance_identifier(source_vpc, source_db)
        clone_db_identifier = RDS.get_instance_identifier(self.vpc_name, source_db)

        instance_params = self.get_instance_parameters(source_vpc, source_db)

        if self._get_db_instance(clone_db_identifier):
            raise RDSEnvironmentError(
                'Cannot create clone instance {0} because a database already exists with that name'
                .format(clone_db_identifier)
            )

        # override some parameters to use the new instance id
        instance_params['DBInstanceIdentifier'] = clone_db_identifier
        instance_params['DBSubnetGroupName'] = clone_db_identifier
        instance_params['DBParameterGroupName'] = clone_db_identifier

        self.recreate_db_subnet_group(instance_params["DBSubnetGroupName"])

        group_name = instance_params["DBParameterGroupName"]
        group_family = RDS.get_db_parameter_group_family(
            instance_params["Engine"], instance_params["EngineVersion"])
        logger.debug("creating parameter group %s with family %s", group_name, group_family)

        # create a parameter group using the parameters of the source db
        self.recreate_db_parameter_group(source_vpc, source_db, group_name, group_family)

        self.create_db_instance(instance_params,
                                custom_snapshot=self.get_latest_snapshot(source_db_identifier))

        # Create/Update CloudWatch Alarms for this instance
        self.spinup_alarms(source_db)

        # Create a DNS record for this instance
        self.setup_dns(clone_db_identifier)

    def run(self):
        self.update_cluster()


class DiscoRDS(object):
    """Class for doing RDS operations on a given environment"""

    def __init__(self, vpc):
        """Initialize class"""
        self.config_aws = read_config()
        self.config_rds = read_config(config_file=DEFAULT_CONFIG_FILE_RDS)
        self.client = boto3.client('rds')
        self.vpc = vpc
        self.disco_vpc_sg_rules = DiscoVPCSecurityGroupRules(vpc, vpc.boto3_ec2)
        self.vpc_name = vpc.environment_name
        if self.vpc_name not in ['staging', 'production']:
            self.domain_name = self.config_aws.get('disco_aws', 'default_domain_name')
        else:
            self.domain_name = self.config_aws.get('disco_aws',
                                                   'default_domain_name@{0}'.format(self.vpc_name))

    def get_rds_security_group_id(self):
        """
        Returns the intranet security group id for the VPC for the current environment
        """
        security_groups = self.disco_vpc_sg_rules.get_all_security_groups_for_vpc()
        for security_group in security_groups:
            if security_group.get('Tags'):
                tags = tag2dict(security_group['Tags'])
                if tags.get("meta_network") == "intranet":
                    return security_group['GroupId']

        raise RuntimeError('Security group for intranet meta network is missing.')

    def update_cluster_by_id(self, database_identifier):
        """Update a RDS cluster by its database identifier"""
        rds_security_group_id = self.get_rds_security_group_id()
        subnet_ids = self.get_subnet_ids()
        rds = RDS(self.vpc_name, database_identifier,
                  rds_security_group_id, subnet_ids, self.domain_name)
        rds.update_cluster()

    def get_subnet_ids(self):
        """ Get a list of subnet ids for the vpc"""
        subnets = self.vpc.get_all_subnets()
        subnet_ids = []
        for subnet in subnets:
            tags = tag2dict(subnet['Tags'])
            if tags['meta_network'] == 'intranet':
                subnet_ids.append(str(subnet['SubnetId']))
        return subnet_ids

    def update_all_clusters_in_vpc(self, parallel=True):
        """
        Updates every RDS instance in the current VPC to match the configuration
        """
        vpc_prefix = self.vpc_name + '-'
        sections = [section for section in self.config_rds.sections()
                    if section.startswith(vpc_prefix)]
        logger.debug("The following RDS clusters will be updated: %s", ", ".join(sections))
        rds_security_group_id = self.get_rds_security_group_id()
        subnet_ids = self.get_subnet_ids()
        rds_list = []

        for section in sections:
            # the section names are database identifiers
            database_identifier = section
            rds = RDS(self.vpc_name,
                      database_identifier,
                      rds_security_group_id,
                      subnet_ids,
                      self.domain_name)
            rds_list.append(rds)

        # For Sequential execution
        if not parallel:
            for rds in rds_list:
                rds.update_cluster()
            return

        # Running in parallel
        for rds in rds_list:
            rds.start()

        # Wait for all threads to finish
        for rds in rds_list:
            rds.join()

    def get_db_instances(self, status=None):
        """
        Get all RDS clusters/instances in the current VPC.
        When status is not None, filter instances that are only in the specified status or list of states.
        """
        response = throttled_call(self.client.describe_db_instances)  # filters are not currently implemented
        instances = response["DBInstances"]
        states = None if not status else status if isinstance(status, list) else [status]
        vpc_instances = [
            instance
            for instance in instances
            if instance["DBSubnetGroup"]["VpcId"] == self.vpc.get_vpc_id() and (
                not states or instance["DBInstanceStatus"] in states)]
        return vpc_instances

    # TODO: When Filters in RDS.Client.describe_db_instances() is implemented,
    #       we could use wait_for_state_boto3() in resource_helper
    def _wait_for_db_instance_deletions(self, timeout=RDS_DELETE_TIMEOUT):
        instances_waiting_for = []
        time_passed = 0
        while True:
            instance_dicts = self.get_db_instances(status="deleting")
            instances = sorted([instance["DBInstanceIdentifier"] for instance in instance_dicts])
            if not instances:
                return

            if time_passed >= timeout:
                raise TimeoutError(
                    "Timed out waiting for RDS clusters to finish deleting after {}s.".format(time_passed))

            if instances != instances_waiting_for:
                logger.info("Waiting for deletion of RDS clusters: %s", ", ".join(instances))
                instances_waiting_for = instances

            time.sleep(RDS_STATE_POLL_INTERVAL)
            time_passed += RDS_STATE_POLL_INTERVAL

    def delete_db_instance(self, instance_identifier, skip_final_snapshot=False):
        """ Delete an RDS instance/cluster. Final snapshot is automatically taken. """
        logger.info("Deleting RDS cluster %s", instance_identifier)

        if skip_final_snapshot:
            allocated_storage = throttled_call(
                self.client.describe_db_instances,
                DBInstanceIdentifier=instance_identifier
            )["DBInstances"][0]["AllocatedStorage"]

            ansi_color_red = "\033[91m"
            ansi_color_none = "\033[0m"
            print(ansi_color_red + "CAREFUL! All tables in " + instance_identifier +
                  " will be dropped and no backup taken. Data will be irrecoverable." + ansi_color_none)
            response = raw_input("Confirm by typing the amount of allocated storage that will be dropped: ")
            if response == str(allocated_storage):
                throttled_call(
                    self.client.delete_db_instance,
                    DBInstanceIdentifier=instance_identifier,
                    SkipFinalSnapshot=True
                )
                print("Done")
            else:
                print(("User input did not match the AllocatedStorage value for {}. Chickening out.".format(
                    instance_identifier)))
                sys.exit(1)
        else:
            final_snapshot = "%s-final-snapshot" % instance_identifier
            try:
                throttled_call(self.client.delete_db_snapshot, DBSnapshotIdentifier=final_snapshot)
            except botocore.exceptions.ClientError:
                pass
            keep_trying(
                RDS_SNAPSHOT_DELETE_TIMEOUT,
                self.client.delete_db_instance,
                DBInstanceIdentifier=instance_identifier,
                FinalDBSnapshotIdentifier=final_snapshot)

    def delete_all_db_instances(self, wait=True):
        """
        Deletes all RDS instances/clusters in the VPC. After issuing the commands for all instances,
        optionally waits for instances to be deleted.
        """
        instances = [i for i in self.get_db_instances() if i["DBInstanceStatus"] != "deleting"]
        good_states = ["available", "backing-up", "creating"]
        instances_in_bad_state = [
            "{} ({})".format(instance["DBInstanceIdentifier"], instance["DBInstanceStatus"])
            for instance in instances
            if instance["DBInstanceStatus"] not in good_states]

        if instances_in_bad_state:
            raise RDSEnvironmentError("Cowardly refusing to delete the following RDS clusters because their"
                                      " state does not allow for a snapshot to be taken: {}".format(
                                          ", ".join(instances_in_bad_state)))

        for instance in instances:
            self.delete_db_instance(instance["DBInstanceIdentifier"])

        if wait:
            self._wait_for_db_instance_deletions()

    def cleanup_snapshots(self, days):
        """
        Cleanup all manual snapshots older than --age specified, by default its 30 days

        Automated Snapshots are managed by RDS
        """
        snapshots = throttled_call(self.client.describe_db_snapshots, SnapshotType='manual')
        for snapshot in snapshots['DBSnapshots']:
            snap_create_date = snapshot['SnapshotCreateTime']
            today = datetime.datetime.utcnow().replace(tzinfo=pytz.utc)
            snapshot_age = (today - snap_create_date).days
            if snapshot_age > days:
                snapshot_id = snapshot['DBSnapshotIdentifier']
                logger.info("Deleting Snapshot %s since its older than %d", snapshot_id, days)
                throttled_call(self.client.delete_db_snapshot, DBSnapshotIdentifier=snapshot_id)

    def _get_db_instance(self, instance_identifier):
        try:
            return throttled_call(self.client.describe_db_instances, DBInstanceIdentifier=instance_identifier)
        except botocore.exceptions.ClientError:
            return None

    def clone(self, source_vpc, source_db):
        """
        Spinup a copy of a given database into the current environment

        Args:
            source_vpc (str): the VPC where the source database is located
            source_db (str): the source database name
        """

        clone_db_identifier = RDS.get_instance_identifier(self.vpc_name, source_db)

        if self._get_db_instance(clone_db_identifier):
            raise RDSEnvironmentError(
                'Cannot create clone instance {0} because a database already exists with that name'
                .format(clone_db_identifier)
            )

        rds_security_group_id = self.get_rds_security_group_id()
        subnet_ids = self.get_subnet_ids()
        rds = RDS(self.vpc_name,
                  clone_db_identifier,
                  rds_security_group_id,
                  subnet_ids, self.domain_name)

        rds.clone(source_vpc, source_db)
