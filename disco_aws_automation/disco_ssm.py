# -*- coding: utf-8 -*-
"""
Manage AWS SSM document creation and execution
"""
from __future__ import print_function
import os
import logging
import time
import json

import boto3
from botocore.exceptions import ClientError
from boto.exception import BotoServerError

from .disco_config import read_config
from .resource_helper import throttled_call, wait_for_state_boto3
from .exceptions import TimeoutError

logger = logging.getLogger(__name__)


SSM_DOCUMENTS_DIR = "ssm/documents"
SSM_EXT = ".ssm"
SSM_WAIT_TIMEOUT = 5 * 60
SSM_WAIT_SLEEP_INTERVAL = 15
AWS_DOCUMENT_PREFIX = "AWS-"
SSM_OUTPUT_ERROR_DELIMITER = "----------ERROR-------"


class DiscoSSM(object):
    """
    A simple class to manage SSM documents
    """

    S3_BUCKET_TAG = "ssm"

    def __init__(self, environment_name=None, config_aws=None):
        self.config_aws = config_aws or read_config(environment=environment_name)

        if environment_name:
            self.environment_name = environment_name.lower()
        else:
            self.environment_name = self.config_aws.environment

        self._conn = None  # Lazily initialized
        self._s3 = None  # Lazily initialized

    @property
    def conn(self):
        """The boto3 ssm connection object"""
        if not self._conn:
            self._conn = boto3.client('ssm')
        return self._conn

    # Pylint thinks 's3' isn't long enough, but it's actually a good descriptor for s3 conn...
    # pylint: disable=invalid-name
    @property
    def s3(self):
        """The boto3 s3 connection object"""
        if not self._s3:
            self._s3 = boto3.client('s3')
        return self._s3

    def get_s3_bucket_name(self):
        """Convenience method for returning the configured s3 bucket for SSM"""
        return self.config_aws.get_asiaq_s3_bucket_name(self.S3_BUCKET_TAG)

    def execute(self, instance_ids, document_name, parameters=None, comment=None, desired_status='Success'):
        """
        Executes the given SSM document against a given list of instance ids.

        Optionally takes parameters to pass to the SSM document and an audit comment to indicate why this
        command was run.

        The parameters object is expected to be in the form of:
        {
            "key": ["values"...],
        }
        """
        bucket_name = self.get_s3_bucket_name()

        arguments = {
            "InstanceIds": instance_ids,
            "DocumentName": document_name
        }

        if parameters is not None:
            arguments["Parameters"] = parameters

        if comment is not None:
            arguments["Comment"] = comment

        if bucket_name is not None:
            try:
                # Head bucket checks if a bucket exists and throws an exception if it doesn't
                self.s3.head_bucket(Bucket=bucket_name)
                arguments["OutputS3BucketName"] = bucket_name
            except ClientError:
                logger.warning(
                    "Unable to access S3 bucket '%s', output limited to 2500 characters",
                    bucket_name
                )

        logger.info(
            "Executing document '%s' against instances %s",
            document_name,
            instance_ids
        )

        try:
            command = self._send_command(**arguments)
            command_id = command["Command"]["CommandId"]

            is_successful = self._wait_for_ssm_command(command_id=command_id, desired_status=desired_status)

            output = self.get_ssm_command_output(command_id=command_id)

            self._print_ssm_output(output)

            return is_successful
        except (ClientError, BotoServerError):
            logger.exception(
                "Unable to execute document '%s' against instances %s",
                document_name,
                instance_ids
            )
            return False

    def _print_ssm_output(self, output):
        """Convenience method for printing output from an SSM command"""
        for instance, instance_output in output.iteritems():
            print("Output for instance: {}".format(instance))
            for plugin in instance_output:
                try:
                    print(u"Plugin: {}\n\n".format(plugin.get('name', '-')))
                    print(u"STDOUT:\n{}\n\n".format(plugin.get('stdout', '-')))
                    print(u"STDERR:\n{}\n\n".format(plugin.get('stderr', '-')))
                    print(u"Exit Code: {}".format(plugin.get('exit_code', 1)))
                except UnicodeEncodeError:
                    logger.exception("Encountered error while printing SSM output")

    def _wait_for_ssm_command(self, command_id, desired_status='Success'):
        """
        Method for waiting for the completion of a given command. Requires the command_id as well as an
        optional desired_status.

        Defaults to a desired status of 'Success'.

        See http://docs.aws.amazon.com/ssm/latest/APIReference/API_Command.html#EC2-Type-Command-Status
        for the valid values of desired_status.

        Note that this method only waits for the desired status to NOT be 'Pending' or 'InProgress'. In other
        words, once the command terminates this method will either return True if the status of the command
        equals the desired status, or False otherwise. For example, the command could be cancelled before it
        completes, or it could return a non-zero exit code.
        """
        while True:
            command = self._list_commands(
                CommandId=command_id
            )

            # Apparently, SSM is eventually consistent. So we can kick off a command and it might not be
            # immediately listed as having been invoked. So if our commands call is empty (as we filter for
            # the specific command id), wait a few seconds and try again.
            if not command["Commands"]:
                logger.warning(
                    "Could not find command id '%s', waiting a few seconds before looking again",
                    command_id
                )
                time.sleep(5)
                # Right now this is an infinite loop, but we'd never call this without a real command_id as
                # its an internal function for DiscoSSM. If this loops forever with a proper command_id, that
                # probably means AWS is having a bad day, and we've got bigger worries than a hanging command.
                continue

            status = command["Commands"][0]["Status"]
            document_name = command["Commands"][0]["DocumentName"]
            instance_ids = command["Commands"][0]["InstanceIds"]
            # If the command is not waiting to execute or executing, let's see if we got the status we wanted
            if status not in ['Pending', 'InProgress']:
                logger.info(
                    "Execution of document '%s' against instances %s completed as '%s'",
                    document_name,
                    instance_ids,
                    status
                )
                return status == desired_status
            logger.info(
                "Waiting for execution of document '%s' against instances %s",
                document_name,
                instance_ids
            )
            time.sleep(5)

    def get_ssm_command_output(self, command_id):
        """
        Method for getting the output of a given command. Requires the command_id of the desired command.

        Returns a dictionary object, in the form of:

        {
            "i-c3dfed1e": [
                {
                    "name": <plugin name>,
                    "stdout": <stdout>,
                    "stderr": <stderr>,
                    "exit_code": <exit code>
                },
                ...
            ],
            ...
        }

        """
        command_invocations = self._list_command_invocations(
            CommandId=command_id,
            Details=True
        )

        response = {}

        for command_invocation in command_invocations["CommandInvocations"]:
            instance_id = command_invocation['InstanceId']
            instance_output = []

            for command_plugin in command_invocation['CommandPlugins']:
                if command_plugin.get('OutputS3BucketName'):
                    plugin_output = self._get_output_from_s3(command_plugin)
                else:
                    plugin_output = self._get_output_from_ssm(command_plugin)

                instance_output.append(plugin_output)

            response[instance_id] = instance_output

        return response

    def _get_output_from_ssm(self, command_plugin):
        """Helper method for extracting command output directly from SSM"""
        output = command_plugin['Output'].split(SSM_OUTPUT_ERROR_DELIMITER)
        stdout = output[0].strip() or '-'

        if len(output) == 2:
            stderr = output[1].strip()
        else:
            stderr = '-'

        plugin_output = {
            'name': command_plugin['Name'],
            'stdout': stdout,
            'stderr': stderr,
            'exit_code': command_plugin['ResponseCode']
        }

        return plugin_output

    def _get_output_from_s3(self, command_plugin):
        """Helper method for extracting command output from S3"""
        bucket_name = command_plugin['OutputS3BucketName']
        key = command_plugin['OutputS3KeyPrefix']

        response = self.s3.list_objects_v2(
            Bucket=bucket_name,
            Prefix=key
        )

        keys_from_command = [entry['Key'] for entry in response.get('Contents', [])]

        stdout_keys = [key for key in keys_from_command if key.endswith('stdout')]
        stderr_keys = [key for key in keys_from_command if key.endswith('stderr')]

        if stdout_keys:
            stdout = self.s3.get_object(
                Bucket=bucket_name,
                Key=stdout_keys[0]
            )['Body'].read().decode('utf-8').strip()
        else:
            stdout = u'-'

        if stderr_keys:
            stderr = self.s3.get_object(
                Bucket=bucket_name,
                Key=stderr_keys[0]
            )['Body'].read().decode('utf-8').strip()
        else:
            stderr = u'-'

        plugin_output = {
            'name': command_plugin['Name'],
            'stdout': stdout,
            'stderr': stderr,
            'exit_code': command_plugin['ResponseCode']
        }

        return plugin_output

    def get_all_documents(self):
        """ Returns a list of existing SSM documents."""
        next_token = ''
        documents = []
        while True:
            if next_token:
                response = throttled_call(self.conn.list_documents, NextToken=next_token)
            else:
                response = throttled_call(self.conn.list_documents)

            documents.extend(response.get("DocumentIdentifiers"))
            next_token = response.get("NextToken")

            if not next_token:
                break

        result = [doc for doc in documents
                  if self._check_valid_doc_prefix(doc["Name"])]
        return result

    def get_document_content(self, doc_name):
        """ Returns the content of the document."""
        if not self._check_valid_doc_prefix(doc_name):
            raise Exception("Document name ({0}) has an invalid prefix.".format(doc_name))

        try:
            response = throttled_call(self.conn.get_document, Name=doc_name)
        except ClientError:
            logger.info("Document name (%s) is not found.", doc_name)
            return None

        return response.get("Content")

    def update(self, wait=True, dry_run=False):
        """ Updates SSM documents from configuration """
        desired_docs = set(self._list_docs_in_config())
        existing_docs = set([doc["Name"] for doc in self.get_all_documents()])

        docs_to_create = desired_docs - existing_docs
        docs_to_delete = existing_docs - desired_docs
        docs_to_update = self._check_for_update(desired_docs & existing_docs)
        unchanged_docs = existing_docs - docs_to_update - docs_to_delete

        logger.info("New documents to be added: %s", docs_to_create)
        logger.info("Documents to be deleted: %s", docs_to_delete)
        logger.info("Existing documents to be updated: %s", docs_to_update)
        logger.info("Unchanged documents: %s", unchanged_docs)

        if not dry_run:
            # Include docs_to_update in docs_to_delete so that they can be recreated later
            docs_to_delete |= docs_to_update
            self._delete_docs(docs_to_delete)
            if wait:
                self._wait_for_docs_deleted(docs_to_delete)

            docs_to_create |= docs_to_update
            self._create_docs(docs_to_create)
            if wait:
                self._wait_for_docs_active(docs_to_create)

    def _create_docs(self, docs_to_create):
        for doc_name in docs_to_create:
            ssm_json = self._read_ssm_file(doc_name)
            logger.debug("Creating document: %s", doc_name)
            throttled_call(self.conn.create_document, Content=ssm_json, Name=doc_name)

    def _delete_docs(self, docs_to_delete):
        for doc_name in docs_to_delete:
            logger.debug("Deleting document: %s", doc_name)
            throttled_call(self.conn.delete_document, Name=doc_name)

    def _check_for_update(self, docs_to_check):
        """
        Returns the documents whose content in the configuration is different from
        the one currently in AWS
        """
        docs_to_update = set()
        for doc_name in docs_to_check:
            desired_json = self._read_ssm_file(doc_name)
            existing_json = self._standardize_json_str(self.get_document_content(doc_name))

            if desired_json != existing_json:
                docs_to_update.add(doc_name)

        return docs_to_update

    def _wait_for_docs_deleted(self, docs_to_delete):
        for doc_name in docs_to_delete:
            time_passed = 0

            while True:
                try:
                    self.conn.describe_document(Name=doc_name)
                except ClientError:
                    # When the document is deleted, calling the describe method would
                    # result in a ClientError being thrown, that's when we know the document
                    # has been deleted.
                    break

                if time_passed >= SSM_WAIT_TIMEOUT:
                    raise TimeoutError(
                        "Timed out waiting for document ({0}) to be deleted after {1}s"
                        .format(doc_name, time_passed))

                time.sleep(SSM_WAIT_SLEEP_INTERVAL)
                time_passed += SSM_WAIT_SLEEP_INTERVAL

    def _wait_for_docs_active(self, docs_to_wait):
        for doc_name in docs_to_wait:
            wait_for_state_boto3(describe_func=self.conn.describe_document,
                                 params_dict={"Name": doc_name},
                                 resources_name="Document",
                                 expected_state="Active",
                                 state_attr="Status",
                                 timeout=SSM_WAIT_TIMEOUT)

    def _read_ssm_file(self, doc_name):
        file_path = "{0}/{1}{2}".format(SSM_DOCUMENTS_DIR, doc_name, SSM_EXT)
        with open(file_path, 'r') as infile:
            ssm_content = infile.read()

        try:
            return self._standardize_json_str(ssm_content)
        except ValueError:
            raise RuntimeError("Invalid SSM document file: {0}".format(file_path))

    def _standardize_json_str(self, json_str):
        return json.dumps(json.loads(json_str), indent=4)

    def _list_docs_in_config(self):
        document_files = os.listdir(SSM_DOCUMENTS_DIR)
        return [document[:-len(SSM_EXT)]
                for document in document_files
                if document.endswith(SSM_EXT) and self._check_valid_doc_prefix(document)]

    def _check_valid_doc_prefix(self, doc_name):
        return not doc_name.startswith(AWS_DOCUMENT_PREFIX)

    def _send_command(self, **arguments):
        """Convenience method for sending SSM commands"""
        return throttled_call(self.conn.send_command, **arguments)

    def _list_commands(self, **arguments):
        """Convenience method for listing SSM commands"""
        return throttled_call(self.conn.list_commands, **arguments)

    def _list_command_invocations(self, **arguments):
        """Convenience method for listing invocations of SSM commands"""
        return throttled_call(self.conn.list_command_invocations, **arguments)
