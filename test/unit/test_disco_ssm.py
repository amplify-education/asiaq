"""Tests of disco_ssm"""
import random
import copy
import json
from unittest import TestCase
from mock import MagicMock, patch, call, create_autospec

from botocore.exceptions import ClientError

from disco_aws_automation import DiscoSSM
from disco_aws_automation import disco_ssm
from disco_aws_automation.disco_ssm import SSM_DOCUMENTS_DIR, SSM_OUTPUT_ERROR_DELIMITER
from disco_aws_automation.disco_creds import DiscoS3Bucket

from test.helpers.patch_disco_aws import (get_mock_config,
                                          TEST_ENV_NAME)


TEST_SSM_S3_BUCKET = "foo-bucket"

MOCK_AWS_CONFIG_DEFINITION = {
    'disco_aws': {
        'default_environment': TEST_ENV_NAME,
        'default_ssm_s3_bucket': TEST_SSM_S3_BUCKET,
    }}
MOCK_AWS_DOCUMENTS = [
    {
        'Name': 'AWS-document_1',
        'Owner': 'mock_owner',
        'PlatformTypes': ['Linux']
    },
    {
        'Name': 'AWS-document_2',
        'Owner': 'mock_owner',
        'PlatformTypes': ['Linux']
    }
]
MOCK_ASIAQ_DOCUMENTS = [
    {
        'Name': 'asiaq-ssm_document_1',
        'Owner': 'mock_owner',
        'PlatformTypes': ['Linux']
    },
    {
        'Name': 'asiaq-ssm_document_2',
        'Owner': 'mock_owner',
        'PlatformTypes': ['Linux']
    }
]
MOCK_ASIAQ_DOCUMENT_CONTENTS = {
    'asiaq-ssm_document_1': '{"field1": "value1"}',
    'asiaq-ssm_document_2': '{"field2": "value2"}',
}
MOCK_ASIAQ_DOCUMENT_FILE_CONTENTS = {
    SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_1.ssm': MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_1'],
    SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_2.ssm': MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_2'],
}
MOCK_NEXT_TOKEN = "abcdefgABCDEFG"
MOCK_COMMANDS = []
MOCK_COMMAND_INVOCATIONS = []
MOCK_S3_BUCKETS = {}


# pylint: disable=invalid-name
def mock_boto3_client(arg):
    """ mock method for boto3.client() """
    if arg != "ssm":
        raise Exception("Mock %s client not implemented.", arg)

    mock_asiaq_documents = copy.copy(MOCK_ASIAQ_DOCUMENTS)
    mock_asiaq_document_contents = copy.copy(MOCK_ASIAQ_DOCUMENT_CONTENTS)
    wait_flags = {'delete': True, 'create': True}

    def _mock_list_documents(NextToken=''):
        all_documents = MOCK_AWS_DOCUMENTS + mock_asiaq_documents
        if NextToken == '':
            return {
                'DocumentIdentifiers': all_documents[:len(all_documents) / 2],
                'NextToken': MOCK_NEXT_TOKEN
            }
        elif NextToken == MOCK_NEXT_TOKEN:
            return {
                'DocumentIdentifiers': all_documents[len(all_documents) / 2:],
                'NextToken': ''
            }
        else:
            raise RuntimeError("Invalid NextToken: {0}".format(NextToken))

    def _mock_get_document(Name):
        if Name not in mock_asiaq_document_contents:
            raise ClientError({'Error': {'Code': 'Mock_code', 'Message': 'mock message'}},
                              'GetDocument')

        return {'Name': Name,
                'Content': mock_asiaq_document_contents[Name]}

    def _mock_create_document(Content, Name):
        mock_asiaq_documents.append({'Name': Name,
                                     'Owner': 'mock_owner',
                                     'PlatformTypes': ['Linux']})
        mock_asiaq_document_contents[Name] = Content

    def _mock_delete_document(Name):
        doc_to_delete = [document for document in mock_asiaq_documents
                         if document['Name'] == Name]
        if doc_to_delete:
            mock_asiaq_documents.remove(doc_to_delete[0])
        mock_asiaq_document_contents.pop(Name, None)

    def _mock_describe_document(Name):
        # Using two wait flags to simulate that AWS is taking time to delete and
        # create documents
        if Name not in mock_asiaq_document_contents:
            if wait_flags['delete']:
                wait_flags['delete'] = False
                return {'Document': {'Name': Name, 'Status': 'Active'}}
            else:
                wait_flags['delete'] = True
                raise ClientError({'Error': {'Code': 'Mock_code', 'Message': 'mock message'}},
                                  'DescribeDocument')
        else:
            if wait_flags['create']:
                wait_flags['create'] = False
                return {'Document': {'Name': Name, 'Status': 'Creating'}}
            else:
                wait_flags['create'] = True
                return {'Document': {'Name': Name, 'Status': 'Active'}}

    def _mock_send_command(InstanceIds, DocumentName, Comment=None, Parameters=None, OutputS3BucketName=None):
        mock_command = create_mock_command(InstanceIds, DocumentName, Comment, Parameters, OutputS3BucketName)
        return {"Command": mock_command}

    def _mock_list_commands(CommandId):
        filtered_mock_commands = []

        for command in MOCK_COMMANDS:
            if command['CommandId'] == CommandId:
                filtered_mock_commands.append(command)
                break

        return {"Commands": filtered_mock_commands}

    def _mock_list_command_invocations(CommandId, Details):
        filtered_mock_command_invocations = []

        for command_invocation in MOCK_COMMAND_INVOCATIONS:
            if command_invocation['CommandId'] == CommandId:
                filtered_mock_command_invocations.append(command_invocation)

        return {"CommandInvocations": filtered_mock_command_invocations}

    mock_ssm = MagicMock()
    mock_ssm.list_documents.side_effect = _mock_list_documents
    mock_ssm.get_document.side_effect = _mock_get_document
    mock_ssm.create_document.side_effect = _mock_create_document
    mock_ssm.delete_document.side_effect = _mock_delete_document
    mock_ssm.describe_document.side_effect = _mock_describe_document
    mock_ssm.send_command.side_effect = _mock_send_command
    mock_ssm.list_commands.side_effect = _mock_list_commands
    mock_ssm.list_command_invocations.side_effect = _mock_list_command_invocations

    return mock_ssm


def combine_stdout_and_stderr(stdout=None, stderr=None):
    if stdout is None and stderr is None:
        return ''
    elif stdout is not None and stderr is None:
        return stdout
    else:
        return SSM_OUTPUT_ERROR_DELIMITER.join([stdout or '', stderr or ''])


def create_mock_command(instance_ids, document_name, comment=None, parameters=None,
                        output_s3_bucket_name=None, status='Success', stdout='stdout', stderr='stderr'):
    command_id = ''.join(random.choice("0123456789abcdefghijklmnopqrstuvxyz-") for _ in range(40))

    mock_command = {
        "Status": status,
        "Parameters": parameters or {},
        "DocumentName": document_name,
        "InstanceIds": instance_ids,
        "CommandId": command_id
    }

    if output_s3_bucket_name is not None:
        mock_command["OutputS3BucketName"] = output_s3_bucket_name

    if comment is not None:
        mock_command["Comment"] = comment

    mock_invocations = []

    for instance_id in instance_ids:
        mock_invocation = {
            "Status": status,
            "CommandPlugins": [
                {
                    "Status": status,
                    "Name": 'foo-plugin',
                    "ResponseCode": 0,
                    "Output": combine_stdout_and_stderr(stdout, stderr)
                }
            ],
            "InstanceId": instance_id,
            "DocumentName": document_name,
            "CommandId": command_id
        }

        if output_s3_bucket_name is not None:
            mock_invocation["CommandPlugins"][0]["OutputS3BucketName"] = output_s3_bucket_name
            mock_invocation["CommandPlugins"][0]["OutputS3KeyPrefix"] = "{0}/{1}".format(
                command_id,
                instance_id
            )

        if comment is not None:
            mock_invocation["Comment"] = comment

        mock_invocations.append(mock_invocation)

    MOCK_COMMANDS.append(mock_command)
    MOCK_COMMAND_INVOCATIONS.extend(mock_invocations)

    if output_s3_bucket_name is not None:
        mock_s3_bucket = create_mock_s3_bucket(mock_invocations, stdout, stderr)
        MOCK_S3_BUCKETS[command_id] = mock_s3_bucket

    return mock_command


def create_mock_s3_bucket(invocations, stdout, stderr):
    """
    Creates a mock Asiaq S3 Bucket object that contains the output of the given command invocations
    """
    mock_s3_bucket = create_autospec(DiscoS3Bucket)

    keys = []
    key_to_data = {}

    for invocation in invocations:
        prefix = invocation["CommandPlugins"][0]["OutputS3KeyPrefix"]

        if stdout is not None:
            stdout_key = "{0}/{1}".format(prefix, 'stdout')
            key_to_data[stdout_key] = stdout
            keys.append(stdout_key)

        if stderr is not None:
            stderr_key = "{0}/{1}".format(prefix, 'stderr')
            key_to_data[stderr_key] = stderr
            keys.append(stderr_key)

    mock_s3_bucket.listkeys.side_effect = lambda prefix_keys: [key for key in keys
                                                               if key.startswith(prefix_keys)]
    mock_s3_bucket.get_key.side_effect = lambda key: key_to_data.get(key)

    return mock_s3_bucket


def create_mock_open(content_dict):
    """
    Creates a mock open method that returns the file content based
    on the dict being passed in
    """
    def _mock_open(file_name, mode):
        if file_name not in content_dict:
            raise RuntimeError("File name ({0}) not in content dict.".format(file_name))

        if mode != 'r':
            raise RuntimeError("SSM documents should only be opened by asiaq in read mode.")

        mock_file = MagicMock(spec=file)
        mock_file.__enter__.return_value = mock_file
        mock_file.read.return_value = content_dict[file_name]
        return mock_file

    return _mock_open


def _standardize_json_str(json_str):
    return json.dumps(json.loads(json_str), indent=4)


class DiscoSSMTests(TestCase):
    """Test DiscoSSM"""

    def setUp(self):
        config_aws = get_mock_config(MOCK_AWS_CONFIG_DEFINITION)
        self._ssm = DiscoSSM(environment_name=TEST_ENV_NAME,
                             config_aws=config_aws)

    @patch('boto3.client', mock_boto3_client)
    def test_get_all_documents(self):
        """Verify that get_all_documents() works"""

        # Calling the method under test
        documents = self._ssm.get_all_documents()

        # Begin verifications
        # Make sure the documents returned contain only the asiaq-managed ones
        self.assertEquals(documents, MOCK_ASIAQ_DOCUMENTS)

        expected_list_calls = [call(), call(NextToken=MOCK_NEXT_TOKEN)]
        self.assertEquals(expected_list_calls,
                          self._ssm.conn.list_documents.mock_calls)

    @patch('boto3.client', mock_boto3_client)
    def test_get_document_content(self):
        """Verify content of a document is correctly retrieved"""

        # Calling the method under test
        doc_content_1 = self._ssm.get_document_content('asiaq-ssm_document_1')
        doc_content_2 = self._ssm.get_document_content('asiaq-ssm_document_2')

        # Verify result
        self.assertEquals(doc_content_1, MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_1'])
        self.assertEquals(doc_content_2, MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_2'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_document_invalid_doc_name(self):
        """Verify correct error is thrown when doc_name is invalid"""

        # Calling the method under test
        self.assertRaises(Exception, self._ssm.get_document_content, doc_name='AWS-doc')

    @patch('boto3.client', mock_boto3_client)
    def test_get_document_doc_name_not_found(self):
        """Verify no content is returned when doc_name is not found"""

        # Calling the method under test
        doc_content = self._ssm.get_document_content('asiaq-random_doc')

        # Verifying results
        self.assertEquals(doc_content, None)

    @patch('boto3.client', mock_boto3_client)
    @patch('os.listdir')
    @patch('disco_aws_automation.disco_ssm.open')
    def test_update_create_docs(self, mock_open, mock_os_listdir):
        """Verify that creating new documents in the update() method works"""
        # Setting up test
        mock_os_listdir.return_value = ['asiaq-ssm_document_1.ssm', 'asiaq-ssm_document_2.ssm',
                                        'asiaq-ssm_document_3.ssm', 'asiaq-ssm_document_4.ssm',
                                        'random_file1.txt', 'random_file2.jpg']

        mock_doc_content_1 = '{"random_field_1": "random_value_1"}'
        mock_doc_content_2 = '{"random_field_2": "random_value_2"}'
        mock_file_contents = copy.copy(MOCK_ASIAQ_DOCUMENT_FILE_CONTENTS)
        mock_file_contents[SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_3.ssm'] = mock_doc_content_1
        mock_file_contents[SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_4.ssm'] = mock_doc_content_2
        mock_open.side_effect = create_mock_open(mock_file_contents)

        # Calling the method under test
        self._ssm.update(wait=False)

        # Verify documents are created successfully
        self.assertEquals(_standardize_json_str(mock_doc_content_1),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_3')))
        self.assertEquals(_standardize_json_str(mock_doc_content_2),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_4')))

    @patch('boto3.client', mock_boto3_client)
    @patch('os.listdir')
    @patch('disco_aws_automation.disco_ssm.open')
    def test_update_delete_docs(self, mock_open, mock_os_listdir):
        """Verify that deleting documents in the update() method works"""
        # Setting up test
        mock_os_listdir.return_value = ['asiaq-ssm_document_2.ssm', 'random_file1.txt',
                                        'random_file2.jpg']
        mock_file_contents = copy.copy(MOCK_ASIAQ_DOCUMENT_FILE_CONTENTS)
        mock_file_contents.pop(SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_1.ssm', None)
        mock_open.side_effect = create_mock_open(mock_file_contents)

        # Calling the method under test
        self._ssm.update(wait=False)

        # Verify only document_1 is deleted
        self.assertTrue(self._ssm.get_document_content('asiaq-ssm_document_1') is None)
        self.assertEquals(_standardize_json_str(MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_2']),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_2')))

    @patch('boto3.client', mock_boto3_client)
    @patch('os.listdir')
    @patch('disco_aws_automation.disco_ssm.open')
    def test_update_modify_docs(self, mock_open, mock_os_listdir):
        """Verify that modifying documents in the update() method works"""
        # Setting up test
        mock_os_listdir.return_value = ['asiaq-ssm_document_1.ssm', 'asiaq-ssm_document_2.ssm',
                                        'random_file1.txt', 'random_file2.jpg']

        new_doc_1_content = '{"random_field_1": "random_value_1"}'
        mock_file_contents = copy.copy(MOCK_ASIAQ_DOCUMENT_FILE_CONTENTS)
        mock_file_contents[SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_1.ssm'] = new_doc_1_content
        mock_open.side_effect = create_mock_open(mock_file_contents)

        # Calling the method under test
        self._ssm.update(wait=False)

        # Verify only document_1 is modified
        self.assertEquals(_standardize_json_str(new_doc_1_content),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_1')))
        self.assertEquals(_standardize_json_str(MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_2']),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_2')))

    @patch('boto3.client', mock_boto3_client)
    @patch('os.listdir')
    @patch('disco_aws_automation.disco_ssm.open')
    def test_update_modify_docs_wait(self, mock_open, mock_os_listdir):
        """Verify that modifying documents in the update() method works with wait set to true"""
        # Setting up test
        mock_os_listdir.return_value = ['asiaq-ssm_document_1.ssm', 'asiaq-ssm_document_2.ssm',
                                        'random_file1.txt', 'random_file2.jpg']

        new_doc_1_content = '{"random_field_1": "random_value_1"}'
        mock_file_contents = copy.copy(MOCK_ASIAQ_DOCUMENT_FILE_CONTENTS)
        mock_file_contents[SSM_DOCUMENTS_DIR + '/asiaq-ssm_document_1.ssm'] = new_doc_1_content
        mock_open.side_effect = create_mock_open(mock_file_contents)
        disco_ssm.SSM_WAIT_TIMEOUT = 1
        disco_ssm.SSM_WAIT_SLEEP_INTERVAL = 1

        # Calling the method under test
        self._ssm.update(wait=True)

        # Verify only document_1 is modified
        describe_call = call(Name='asiaq-ssm_document_1')
        # Expecting describe_document() to be called four times: two for delete, two for create
        expected_describe_calls = [describe_call, describe_call, describe_call, describe_call]
        self.assertEquals(expected_describe_calls,
                          self._ssm.conn.describe_document.mock_calls)

        self.assertEquals(_standardize_json_str(new_doc_1_content),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_1')))
        self.assertEquals(_standardize_json_str(MOCK_ASIAQ_DOCUMENT_CONTENTS['asiaq-ssm_document_2']),
                          _standardize_json_str(
                              self._ssm.get_document_content('asiaq-ssm_document_2')))

    @patch('boto3.client', mock_boto3_client)
    def test_get_s3_bucket(self):
        """Verify that we get correct S3 bucket"""

        self.assertEquals(
            TEST_SSM_S3_BUCKET,
            self._ssm.get_s3_bucket_name()
        )

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_s3_bucket(self):
        """Verify that we get the correct output from an S3 bucket"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc',
            output_s3_bucket_name='foo-bucket'
        )
        command_id = mock_command['CommandId']
        mock_s3_bucket = MOCK_S3_BUCKETS[command_id]

        self._ssm.get_s3_bucket = MagicMock(return_value=mock_s3_bucket)

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('stdout', output[0]['stdout'])
            self.assertEquals('stderr', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_s3_bucket_with_no_stdout(self):
        """Verify that we get the correct output from an S3 bucket with no stdout"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc',
            output_s3_bucket_name='foo-bucket',
            stdout=None
        )
        command_id = mock_command['CommandId']
        mock_s3_bucket = MOCK_S3_BUCKETS[command_id]

        self._ssm.get_s3_bucket = MagicMock(return_value=mock_s3_bucket)

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('-', output[0]['stdout'])
            self.assertEquals('stderr', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_s3_bucket_with_no_stderr(self):
        """Verify that we get the correct output from an S3 bucket with no stderr"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc',
            output_s3_bucket_name='foo-bucket',
            stderr=None
        )
        command_id = mock_command['CommandId']
        mock_s3_bucket = MOCK_S3_BUCKETS[command_id]

        self._ssm.get_s3_bucket = MagicMock(return_value=mock_s3_bucket)

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('stdout', output[0]['stdout'])
            self.assertEquals('-', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_ssm(self):
        """Verify that we get the correct output from the SSM service"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc'
        )
        command_id = mock_command['CommandId']

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('stdout', output[0]['stdout'])
            self.assertEquals('stderr', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_ssm_bucket_with_no_stdout(self):
        """Verify that we get the correct output from SSM with no stdout"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc',
            stdout=None
        )
        command_id = mock_command['CommandId']

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('-', output[0]['stdout'])
            self.assertEquals('stderr', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_get_output_from_ssm_bucket_with_no_stderr(self):
        """Verify that we get the correct output from SSM with no stderr"""
        instance_ids = ['i-1', 'i-2']
        mock_command = create_mock_command(
            instance_ids=instance_ids,
            document_name='foo-doc',
            stderr=None
        )
        command_id = mock_command['CommandId']

        command_output = self._ssm.get_ssm_command_output(command_id)

        self.assertEquals(instance_ids, command_output.keys())

        for output in command_output.values():
            self.assertEquals('stdout', output[0]['stdout'])
            self.assertEquals('-', output[0]['stderr'])

    @patch('boto3.client', mock_boto3_client)
    def test_execute_command(self):
        """Verify that we can execute a command"""
        self._ssm.get_s3_bucket_name = MagicMock(return_value=None)
        instance_ids = ['i-1', 'i-2']
        document_name = "foo-doc"
        comment = "foo-comment"
        parameters = "foo-parameters"

        self._ssm.get_s3_bucket = MagicMock(side_effect=lambda bucket_name: MOCK_S3_BUCKETS.values()[0])

        is_successful = self._ssm.execute(
            instance_ids,
            document_name,
            comment=comment,
            parameters=parameters
        )

        self.assertEquals(True, is_successful)
        self.assertEquals(False, self._ssm.get_s3_bucket.called)

    @patch('boto3.client', mock_boto3_client)
    def test_execute_command_with_s3(self):
        """Verify that we can execute a command with output in an S3 bucket"""
        instance_ids = ['i-1', 'i-2']
        document_name = "foo-doc"
        comment = "foo-comment"
        parameters = "foo-parameters"

        self._ssm.get_s3_bucket = MagicMock(side_effect=lambda bucket_name: MOCK_S3_BUCKETS.values()[0])

        is_successful = self._ssm.execute(
            instance_ids,
            document_name,
            comment=comment,
            parameters=parameters
        )

        self.assertEquals(True, is_successful)
        self.assertEquals(True, self._ssm.get_s3_bucket.called)

    @patch('boto3.client', mock_boto3_client)
    def test_execute_command_fails_with_other_status(self):
        """Verify that we fail if the desired status isn't met"""
        self._ssm.get_s3_bucket_name = MagicMock(return_value=None)
        instance_ids = ['i-1', 'i-2']
        document_name = "foo-doc"
        comment = "foo-comment"
        parameters = "foo-parameters"

        is_successful = self._ssm.execute(
            instance_ids,
            document_name,
            comment=comment,
            parameters=parameters,
            desired_status='Failure'
        )

        self.assertEquals(False, is_successful)

    @patch('boto3.client', mock_boto3_client)
    def test_read_env_from_config(self):
        """Verify that we read the env from config if none is provided"""
        config_aws = get_mock_config(MOCK_AWS_CONFIG_DEFINITION)
        self._ssm = DiscoSSM(config_aws=config_aws)

        self.assertEquals(TEST_ENV_NAME, self._ssm.environment_name)
