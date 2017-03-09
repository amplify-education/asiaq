"""Test disco_dynamodb"""
from unittest import TestCase
from boto.dynamodb2.fields import HashKey, RangeKey, GlobalAllIndex
from boto.dynamodb2.table import Table
from moto import mock_dynamodb2
from disco_aws_automation import DiscoDynamoDB
from disco_aws_automation.exceptions import DynamoDBEnvironmentError


ENVIRONMENT_NAME = "unittest"
MOCK_TABLE_NAME_1 = "mock_table_1"
MOCK_TABLE_NAME_2 = "mock_table_2"
MOCK_TABLE_HASH_KEY = '_id'
MOCK_TABLE_RANGE_KEY = 'mock_range_key'
MOCK_TABLE_GLOBAL_INDEX_NAME = 'mock_index'
MOCK_TABLE_GLOBAL_INDEX_ATTR_NAME = 'mock_index_attr'
MOCK_TABLE_READ_THROUGHPUT = 10
MOCK_TABLE_WRITE_THROUGHPUT = 20
MOCK_TABLE_CONFIG_UPDATE = "test_dynamodb_table_update.json"
MOCK_TABLE_READ_THROUGHPUT_UPDATE = 23
MOCK_TABLE_WRITE_THROUGHPUT_UPDATE = 19


class DiscoDynamoDBTests(TestCase):
    """Test disco_dynamodb"""

    @mock_dynamodb2
    def test_list_tables(self):
        """ Ensures DiscoDynamoDB returns names of all the tables in sorted order """

        self._mock_create_table(MOCK_TABLE_NAME_1)
        self._mock_create_table(MOCK_TABLE_NAME_2)

        dynamodb = DiscoDynamoDB(ENVIRONMENT_NAME)
        tables = dynamodb.get_all_tables()
        self.assertEqual(tables,
                         [MOCK_TABLE_NAME_1, MOCK_TABLE_NAME_2])

    def test_invalid_env_name(self):
        """ Ensures invalid environment name raises DynamoDBEnvironmentError exception """

        with self.assertRaises(DynamoDBEnvironmentError):
            DiscoDynamoDB(None)

        with self.assertRaises(DynamoDBEnvironmentError):
            DiscoDynamoDB('none')

        with self.assertRaises(DynamoDBEnvironmentError):
            DiscoDynamoDB('-')

    @mock_dynamodb2
    def test_describe_table(self):
        """ Ensures DiscoDynamoDB.describe_table() returns the right property values of a table """

        self._mock_create_table(MOCK_TABLE_NAME_1 + "_" + ENVIRONMENT_NAME)

        dynamodb = DiscoDynamoDB(ENVIRONMENT_NAME)
        table = dynamodb.describe_table(MOCK_TABLE_NAME_1)

        self.assertEqual(len(table["KeySchema"]), 2)
        for key in table["KeySchema"]:
            if key["KeyType"] == "HASH":
                self.assertEqual(key["AttributeName"], MOCK_TABLE_HASH_KEY)
            else:
                self.assertEqual(key["AttributeName"], MOCK_TABLE_RANGE_KEY)
        self.assertEqual(table["ProvisionedThroughput"]["ReadCapacityUnits"], MOCK_TABLE_READ_THROUGHPUT)
        self.assertEqual(table["ProvisionedThroughput"]["WriteCapacityUnits"], MOCK_TABLE_WRITE_THROUGHPUT)
        self.assertEqual(table["TableStatus"], "ACTIVE")
        self.assertEqual(len(table["GlobalSecondaryIndexes"]), 1)
        self.assertEqual(table["GlobalSecondaryIndexes"][0]["IndexName"], MOCK_TABLE_GLOBAL_INDEX_NAME)
        self.assertEqual(len(table["GlobalSecondaryIndexes"][0]["KeySchema"]), 1)
        self.assertEqual(table["GlobalSecondaryIndexes"][0]["KeySchema"][0]["AttributeName"],
                         MOCK_TABLE_GLOBAL_INDEX_ATTR_NAME)

    @mock_dynamodb2
    def test_update_table(self):
        """ Ensures DiscoDynamoDB.update_table() correctly updates a table """

        self._mock_create_table(MOCK_TABLE_NAME_2 + "_" + ENVIRONMENT_NAME)

        dynamodb = DiscoDynamoDB(ENVIRONMENT_NAME)
        dynamodb.update_table(MOCK_TABLE_NAME_2, MOCK_TABLE_CONFIG_UPDATE, True)

        table = dynamodb.describe_table(MOCK_TABLE_NAME_2)

        self.assertEqual(table["ProvisionedThroughput"]["ReadCapacityUnits"],
                         MOCK_TABLE_READ_THROUGHPUT_UPDATE)
        self.assertEqual(table["ProvisionedThroughput"]["WriteCapacityUnits"],
                         MOCK_TABLE_WRITE_THROUGHPUT_UPDATE)

    @mock_dynamodb2
    def test_get_real_table_identifiers(self):
        "DynamoDB get_real_table_identifiers gets region and real table name from environment and client."
        dynamodb = DiscoDynamoDB("arbitrary")
        region, real_name = dynamodb.get_real_table_identifiers("FunnyTable")
        self.assertEquals("ap-southeast-1", region)  # pass-through value from aws configuration
        self.assertEquals("FunnyTable_arbitrary", real_name)

    def _mock_create_table(self, name, hash_key=MOCK_TABLE_HASH_KEY,
                           range_key=MOCK_TABLE_RANGE_KEY,
                           read_throughput=MOCK_TABLE_READ_THROUGHPUT,
                           write_throughput=MOCK_TABLE_WRITE_THROUGHPUT,
                           global_index_name=MOCK_TABLE_GLOBAL_INDEX_NAME,
                           global_index_attr_name=MOCK_TABLE_GLOBAL_INDEX_ATTR_NAME):
        Table.create(
            name,
            schema=[
                HashKey(hash_key),
                RangeKey(range_key)],
            throughput={
                'read': read_throughput,
                'write': write_throughput},
            global_indexes=[GlobalAllIndex(global_index_name,
                                           parts=[HashKey(global_index_attr_name)])])
