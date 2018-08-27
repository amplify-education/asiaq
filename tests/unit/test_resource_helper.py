"""
Tests for resource Helper
"""
import random
from unittest import TestCase

from boto.exception import BotoServerError, EC2ResponseError
import boto.ec2.instance
from botocore.exceptions import ClientError
from mock import patch, MagicMock, create_autospec

from disco_aws_automation.exceptions import ExpectedTimeoutError
from disco_aws_automation import TimeoutError
from disco_aws_automation.resource_helper import Jitter, keep_trying, throttled_call, wait_for_state, \
    wait_for_state_boto3, wait_for_sshable, MAX_POLL_INTERVAL


# time.sleep is being patched but not referenced.
# pylint: disable=W0613
class ResourceHelperTests(TestCase):
    """Test Resource Helper"""

    def mock_instance(self):
        '''Create a mock Instance'''
        inst = create_autospec(boto.ec2.instance.Instance)
        inst.id = 'i-' + ''.join(random.choice("0123456789abcdef") for _ in range(8))
        inst.instance_id = inst.id
        return inst

    @patch('time.sleep', return_value=None)
    def test_jitter(self, mock_sleep):
        """Test Jitter backoff """
        min_wait = 3
        jitter = Jitter(min_wait=min_wait)
        cycle = 0
        previous_time_passed = 0
        while True:
            cycle += 1
            time_passed = jitter.backoff()
            wait_time = time_passed - previous_time_passed
            self.assertTrue(wait_time >= min_wait)
            self.assertTrue(wait_time <= max(min_wait, previous_time_passed * 3))
            self.assertTrue(wait_time <= MAX_POLL_INTERVAL)
            previous_time_passed = time_passed
            if time_passed > 1000:
                break

    @patch('time.sleep', return_value=None)
    def test_keep_trying_noerr(self, mock_sleep):
        """Test keep_trying with no error"""
        mock_func = MagicMock()
        mock_func.side_effect = [StandardError, StandardError, True]
        keep_trying(10, mock_func)
        self.assertEqual(3, mock_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_keep_trying_timeout(self, mock_sleep):
        """Test keep_trying with timeout"""
        mock_func = MagicMock()
        mock_func.side_effect = StandardError
        self.assertRaises(StandardError, keep_trying, 10, mock_func)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_noerr(self, mock_sleep):
        """Test throttle_call with no error"""
        mock_func = MagicMock()
        boto_server_error = BotoServerError('error', None)
        boto_server_error.error_code = "Throttling"
        mock_func.side_effect = [boto_server_error, boto_server_error, True]
        throttled_call(mock_func)
        self.assertEqual(3, mock_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_timeout(self, mock_sleep):
        """Test throttle_call with timeout"""
        mock_func = MagicMock()
        boto_server_error = BotoServerError('error', None)
        boto_server_error.error_code = "Throttling"
        mock_func.side_effect = boto_server_error
        self.assertRaises(BotoServerError, throttled_call, mock_func)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_error(self, mock_sleep):
        """Test throttle_call with error"""
        mock_func = MagicMock()
        boto_server_error = BotoServerError('error', None)
        boto_server_error.error_code = "MyError"
        mock_func.side_effect = boto_server_error
        self.assertRaises(BotoServerError, throttled_call, mock_func)
        self.assertEqual(1, mock_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_clienterror_noerr(self, mock_sleep):
        """Test throttle_call using ClientError and no error"""
        mock_func = MagicMock()
        error_response = {"Error": {"Code": "Throttling"}}
        client_error = ClientError(error_response, "test")
        mock_func.side_effect = [client_error, client_error, True]
        throttled_call(mock_func)
        self.assertEqual(3, mock_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_clienterror_timeout(self, mock_sleep):
        """Test throttle_call using ClientError and timeout"""
        mock_func = MagicMock()
        error_response = {"Error": {"Code": "Throttling"}}
        client_error = ClientError(error_response, "test")
        mock_func.side_effect = client_error
        self.assertRaises(ClientError, throttled_call, mock_func)

    @patch('time.sleep', return_value=None)
    def test_throttled_call_clienterror_error(self, mock_sleep):
        """Test throttle_call using ClientError and error"""
        mock_func = MagicMock()
        error_response = {"Error": {"Code": "MyError"}}
        client_error = ClientError(error_response, "test")
        mock_func.side_effect = client_error
        self.assertRaises(ClientError, throttled_call, mock_func)
        self.assertEqual(1, mock_func.call_count)

    @patch('boto3.resource')
    @patch('time.sleep', return_value=None)
    def test_wait_for_state_noerr(self, mock_sleep, mock_resource):
        """Test wait_for_state with no error"""
        setattr(mock_resource, 'status', 'available')
        wait_for_state(mock_resource, 'available', state_attr='status', timeout=30)
        self.assertEqual(1, mock_resource.update.call_count)

    @patch('boto3.resource')
    @patch('time.sleep', return_value=None)
    def test_wait_for_state_timeout(self, mock_sleep, mock_resource):
        """Test wait_for_state with timeout"""
        setattr(mock_resource, 'status', 'mystatus')
        self.assertRaises(TimeoutError, wait_for_state, mock_resource, 'available',
                          state_attr='status', timeout=30)

    @patch('boto3.resource')
    @patch('time.sleep', return_value=None)
    def test_wait_for_state_expected_timeout(self, mock_sleep, mock_resource):
        """Test wait_for_state with expected timeout"""
        setattr(mock_resource, 'status', 'failed')
        self.assertRaises(ExpectedTimeoutError, wait_for_state, mock_resource, 'available',
                          state_attr='status', timeout=30)
        self.assertEqual(1, mock_resource.update.call_count)

        setattr(mock_resource, 'status', 'terminated')
        self.assertRaises(ExpectedTimeoutError, wait_for_state, mock_resource, 'available',
                          state_attr='status', timeout=30)
        self.assertEqual(2, mock_resource.update.call_count)

    @patch('boto3.resource')
    @patch('time.sleep', return_value=None)
    def test_wait_for_state_ec2error(self, mock_sleep, mock_resource):
        """Test wait_for_state using EC2ResponseError and timeout"""
        setattr(mock_resource, 'status', 'mystatus')
        mock_resource.update.side_effect = EC2ResponseError("mystatus", "test")
        self.assertRaises(TimeoutError, wait_for_state, mock_resource, 'available', state_attr='status',
                          timeout=30)

    @patch('boto3.resource')
    @patch('time.sleep', return_value=None)
    def test_wait_for_state_error(self, mock_sleep, mock_resource):
        """Test wait_for_state using RuntimeError and returned Exception"""
        setattr(mock_resource, 'status', 'mystatus')
        mock_resource.update.side_effect = RuntimeError
        self.assertRaises(RuntimeError, wait_for_state, mock_resource, 'available', state_attr='status',
                          timeout=30)
        self.assertEqual(1, mock_resource.update.call_count)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_noerr(self, mock_sleep):
        """Test wait_for_state_boto3 with no error"""
        mock_describe_func = MagicMock(return_value={"myresource": {"status": "available"}})
        wait_for_state_boto3(mock_describe_func, {"param1": "p1"}, "myresource", 'available',
                             state_attr='status', timeout=30)
        self.assertEqual(1, mock_describe_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_timeout(self, mock_sleep):
        """Test wait_for_state_boto3 with timeout"""
        mock_describe_func = MagicMock(return_value={"myresource": {"status": "mystatus"}})
        self.assertRaises(TimeoutError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_exp_timeout(self, mock_sleep):
        """Test wait_for_state_boto3 with ExpectedTimeout"""
        mock_describe_func = MagicMock(return_value={"myresource": {"status": "failed"}})
        self.assertRaises(ExpectedTimeoutError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)
        self.assertEqual(1, mock_describe_func.call_count)

        mock_describe_func = MagicMock(return_value={"myresource": {"status": "terminated"}})
        self.assertRaises(ExpectedTimeoutError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)
        self.assertEqual(1, mock_describe_func.call_count)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_clienterror(self, mock_sleep):
        """Test wait_for_state_boto3 with ClientError and returned Timeout"""
        mock_describe_func = MagicMock()
        error_response = {"Error": {"Code": "MyError"}}
        mock_describe_func.side_effect = ClientError(error_response, "test")
        self.assertRaises(TimeoutError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_ec2error(self, mock_sleep):
        """Test wait_for_state_boto3 with EC2ResponseError and returned Timeout"""
        mock_describe_func = MagicMock()
        mock_describe_func.side_effect = EC2ResponseError("mystatus", "test")
        self.assertRaises(TimeoutError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)

    @patch('time.sleep', return_value=None)
    def test_wait_for_state_boto3_error(self, mock_sleep):
        """Test wait_for_state_boto3 with RuntimeError and returned RuntimeError"""
        mock_describe_func = MagicMock()
        mock_describe_func.side_effect = RuntimeError
        self.assertRaises(RuntimeError, wait_for_state_boto3, mock_describe_func, {"param1": "p1"},
                          "myresource", 'available', state_attr='status', timeout=30)
        self.assertEqual(1, mock_describe_func.call_count)

    @patch('disco_aws_automation.resource_helper.wait_for_state')
    @patch('time.sleep', return_value=None)
    def test_wait_for_sshable_noerr(self, mock_sleep, mock_wait_for_state):
        """Test wait_for_sshable with no error"""
        mock_remote_cmd = MagicMock(return_value=[0])
        wait_for_sshable(mock_remote_cmd, self.mock_instance(), 30)
        self.assertEqual(1, mock_remote_cmd.call_count)

    @patch('disco_aws_automation.resource_helper.wait_for_state')
    @patch('time.sleep', return_value=None)
    def test_wait_for_sshable_timeout(self, mock_sleep, mock_wait_for_state):
        """Test wait_for_sshable with timeout"""
        mock_remote_cmd = MagicMock(return_value=[1])
        self.assertRaises(TimeoutError, wait_for_sshable, mock_remote_cmd, self.mock_instance(), 30)
