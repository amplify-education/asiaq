"""
DynamoDB Module. Can be used to perform various DynamoDB operations
"""

import json
from logging import getLogger

import boto3
from botocore.exceptions import ClientError

from .disco_config import normalize_path, read_config
from .disco_datapipeline import AsiaqDataPipelineManager
from .exceptions import DynamoDBEnvironmentError, EasyExit
from .resource_helper import throttled_call


class DiscoDynamoDB(object):
    """Class for doing DynamoDB operations"""

    def __init__(self, environment_name):
        """Initialize class"""
        if not environment_name:
            raise DynamoDBEnvironmentError("No environment name is specified.")

        if environment_name.upper() in ("NONE", "-"):
            raise DynamoDBEnvironmentError("Invalid environment name: {0}.".format(environment_name))

        self.environment_name = environment_name

        self.dynamodb = boto3.resource("dynamodb")

    def get_all_tables(self):
        """ Returns a list of existing DynamoDB table names."""
        tables = throttled_call(self.dynamodb.tables.all)
        return sorted([table.name for table in tables])

    def create_table(self, config_file, wait):
        """
            Creates a DynamoDB table using the definition in config_file.
            Returns the response from AWS DynamoDB service
        """
        table_def = DiscoDynamoDB._load_table_definition(config_file)
        if not table_def.get("TableName"):
            raise DynamoDBEnvironmentError("TableName is missing from table definition config file.")
        if table_def["TableName"].find("_") >= 0:
            raise DynamoDBEnvironmentError("TableName cannot contain '_'.")
        table_def["TableName"] = self._env_postfixed_table_name(table_def["TableName"])

        table = throttled_call(self.dynamodb.create_table, **table_def)

        if wait:
            table.meta.client.get_waiter('table_exists').wait(TableName=table_def["TableName"])
            table.reload()

        return DiscoDynamoDB._convert_table_to_dict(table)

    def update_table(self, table_name, config_file, wait):
        """
            Updates a DynamoDB table using the definition in config_file.
            Returns the response from AWS DynamoDB service
        """
        table = self._find_table(table_name)
        actual_table_name = table.name

        table_def = DiscoDynamoDB._load_table_definition(config_file)

        table = throttled_call(table.update, **table_def)

        if wait:
            table.meta.client.get_waiter('table_exists').wait(TableName=actual_table_name)
            table.reload()

        return DiscoDynamoDB._convert_table_to_dict(table)

    def describe_table(self, table_name):
        """ Returns the current definition of a DynamoDB table in a dict """
        table = self._find_table(table_name)

        return DiscoDynamoDB._convert_table_to_dict(table)

    def get_real_table_identifiers(self, table_name):
        """Find the region name and actual table name for a logical table in the current environment."""
        table = self._find_table(table_name)
        actual_table_name = table.name
        return (self.dynamodb.meta.client.meta.region_name, actual_table_name)

    def delete_table(self, table_name, wait):
        """ Deletes a DynamoDB table and returns the response from AWS DynamoDB service """
        table = self._find_table(table_name)
        actual_table_name = table.name

        response = throttled_call(table.delete)
        table_desc = DiscoDynamoDB._extract_field(response, "TableDescription")

        if wait:
            table.meta.client.get_waiter('table_not_exists').wait(TableName=actual_table_name)
            table_desc["TableStatus"] = "DELETED"

        return table_desc

    def _find_table(self, name):
        postfixed_name = self._env_postfixed_table_name(name)
        table = throttled_call(self.dynamodb.Table, postfixed_name)
        if not table:
            raise DynamoDBEnvironmentError("Table {0} couldn't be found.".format(name))

        return table

    def _env_postfixed_table_name(self, table_name):
        return table_name + "_" + self.environment_name

    @staticmethod
    def _load_table_definition(config_file):
        json_file_path = normalize_path(config_file)

        with open(json_file_path) as data_file:
            table_def = json.load(data_file)

        return table_def

    @staticmethod
    def _extract_field(response, field_to_return):
        if "ResponseMetadata" in response and response["ResponseMetadata"]["HTTPStatusCode"] != 200:
            raise DynamoDBEnvironmentError(response["ResponseMetadata"])
        else:
            return response[field_to_return] if field_to_return else response

    @staticmethod
    def _convert_table_to_dict(table):
        return {"AttributeDefinitions": table.attribute_definitions,
                "TableName": table.name,
                'KeySchema': table.key_schema,
                "TableStatus": table.table_status,
                "CreationDateTime": table.creation_date_time,
                "ProvisionedThroughput": table.provisioned_throughput,
                "TableSizeBytes": table.table_size_bytes,
                "ItemCount": table.item_count,
                "TableArn": table.table_arn,
                "LocalSecondaryIndexes": table.local_secondary_indexes,
                "GlobalSecondaryIndexes": table.global_secondary_indexes,
                "StreamSpecification": table.stream_specification,
                "LatestStreamLabel": table.latest_stream_label,
                "LatestStreamArn": table.latest_stream_arn}


class AsiaqDynamoDbBackupManager(object):
    """
    Management tool for creating, restoring from, and inspecting DynamoDB table backups.
    """
    S3_BUCKET_KEY = "dynamodb-backup"
    BACKUP_DESC_TEMPLATE = "Periodic backup for %s table in %s env"

    BACKUP_PIPELINE_TEMPLATE = 'dynamodb_backup'
    RESTORE_PIPELINE_TEMPLATE = 'dynamodb_restore'

    def __init__(self, environment=None, config=None):
        self._environment = environment
        self._aws_config = config
        self._mgr = AsiaqDataPipelineManager(config=self.config)
        self._s3_client = None
        self.logger = getLogger(type(self).__name__)
        self._s3_bucket_name = None

    @property
    def config(self):
        "Fetch the AWS config object, auto-populating if need be."
        if not self._aws_config:
            self._aws_config = read_config(environment=self._environment)
        return self._aws_config

    @property
    def s3_client(self):
        "Auto-create an s3 client if needed."
        if not self._s3_client:
            self._s3_client = boto3.client('s3')
        return self._s3_client

    @property
    def s3_bucket_name(self):
        """
        Find the correct s3 bucket name for DynamoDB buckets in this environment, check if
        the bucket actually exists, and return (and cache) the name of the bucket.
        """
        if not self._s3_bucket_name:
            bucket_name = self.config.get_asiaq_s3_bucket_name(self.S3_BUCKET_KEY)
            if not self._bucket_exists(bucket_name):
                raise EasyExit("Backup bucket %s does not exist" % bucket_name)
            self._s3_bucket_name = bucket_name
        return self._s3_bucket_name

    def create_backup(self, table_name, start=True, force_update=False, metanetwork=None):
        """
        Create a backup pipeline for the given table, and start it running (unless the
        'start' argument is False).
        """
        env = self.config.environment
        pipeline_name = "%s backup - %s" % (table_name, env)
        pipeline_description = self.BACKUP_DESC_TEMPLATE % (table_name, self.config.environment)
        tags = {'environment': env, 'template': self.BACKUP_PIPELINE_TEMPLATE, 'table_name': table_name}
        pipeline = self._mgr.fetch_or_create(self.BACKUP_PIPELINE_TEMPLATE, pipeline_name=pipeline_name,
                                             tags=tags,
                                             pipeline_description=pipeline_description,
                                             log_location=self._s3_url("logs"),
                                             metanetwork=metanetwork,
                                             force_update=force_update)
        if start:
            param_values = self._get_table_params(table_name)
            param_values['myOutputS3Loc'] = self._s3_url(env, table_name)
            start_resp = self._mgr.start(pipeline, param_values)
            self.logger.debug("Started pipeline, got response %s", start_resp)

    def restore_backup(self, table_name, backup_dir=None, force_update=False, metanetwork=None):
        """
        Create a backup-restore pipeline if needed, and activate it to restore a particular table from
        either a particular backup or the latest one.
        """
        env = self.config.environment
        pipeline_name = "%s-restore" % env
        pipeline_description = "DynamoDB backup restore pipeline for %s." % env
        tags = {'environment': env, 'template': self.RESTORE_PIPELINE_TEMPLATE}
        if not backup_dir:
            self.logger.debug("Looking in S3 for most recent backup of %s/%s", env, table_name)
            backup_dir = self._find_latest_backup(env, table_name)

        self.logger.info("Restoring from backup %s", backup_dir)
        pipeline = self._mgr.fetch_or_create(
            self.RESTORE_PIPELINE_TEMPLATE,
            pipeline_name=pipeline_name, tags=tags, pipeline_description=pipeline_description,
            log_location=self._s3_url("logs"), metanetwork=metanetwork, force_update=force_update)
        param_values = self._get_table_params(table_name)
        param_values['myInputS3Loc'] = self._s3_url(env, table_name, backup_dir)

        self.logger.debug("Starting restore pipeline with these parameter values: %s", param_values)
        start_resp = self._mgr.start(pipeline, param_values)
        self.logger.debug("Started pipeline, got response %s", start_resp)

    def list_backups(self, env, table_name):
        """
        List "sub-directories" starting with an env/table-name specified prefix in the backup bucket.
        Since the backup pipeline creates a datetime-tagged backup directory each time it runs, this
        will give us the list of available backups to restore from.
        """
        accumulated = []
        prefix = "/".join([env, table_name, ""])
        prefix_length = len(prefix)
        list_args = dict(Bucket=self.s3_bucket_name, Prefix=prefix, Delimiter="/")
        while True:
            resp = throttled_call(self.s3_client.list_objects_v2, **list_args)
            if resp['KeyCount'] > 0:
                # prefixes will be in the form "/$ENV/$TABLE/$DATETIME/": this extracts the $DATETIME part
                accumulated.extend([record['Prefix'][prefix_length:-1] for record in resp['CommonPrefixes']])
            if resp['IsTruncated']:
                list_args['ContinuationToken'] = resp['NextContinuationToken']
            else:
                break
        return accumulated

    def init_bucket(self):
        "Create the bucket, if needed."
        bucket_name = self.config.get_asiaq_s3_bucket_name(self.S3_BUCKET_KEY)
        if not self._bucket_exists(bucket_name):
            self.logger.info("Creating bucket %s", bucket_name)
            create_resp = self.s3_client.create_bucket(Bucket=bucket_name)
            self.logger.debug("Created: %s", create_resp)
            return create_resp
        else:
            return False

    def _find_latest_backup(self, env, table_name):
        """
        Find the latest backup, if one exists (backups are named with ISO timestamps, so this is really easy)
        """
        all_backups = self.list_backups(env, table_name)
        all_backups.sort()
        return all_backups[-1] if len(all_backups) > 0 else None

    def _get_table_params(self, table_name):
        "Get parameters for a pipeline activation for a specific table."
        dynamodb = DiscoDynamoDB(self.config.environment)
        region, real_table_name = dynamodb.get_real_table_identifiers(table_name)
        # we're leaving this defaulted, but this is how we would change it if we wanted to:
        # params['myDDBWriteThroughputRatio'] = str(0.25)
        return {'myDDBRegion': region, 'myDDBTableName': real_table_name}

    def _s3_url(self, *parts):
        return "s3://%s/%s/" % (self.s3_bucket_name, "/".join(parts))

    def _bucket_exists(self, bucket_name):
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as err:
            if err.response['ResponseMetadata']['HTTPStatusCode'] == 404:
                return False
            raise
