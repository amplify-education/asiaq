"""
DynamoDB Module. Can be used to perform various DynamoDB operations
"""

import json

import boto3

from .disco_config import normalize_path
from .exceptions import DynamoDBEnvironmentError
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
