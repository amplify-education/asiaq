"""Tests for Spotinst Client"""
import json
from unittest import TestCase

import httpretty
from mock import patch

from disco_aws_automation.exceptions import SpotinstRateExceededException
from disco_aws_automation.spotinst_client import SpotinstClient


class DiscoSpotinstClientTests(TestCase):
    """Test SpotinstClient class"""

    def setUp(self):
        """Pre-test setup"""
        self.spotinst_client = SpotinstClient("fooabcd")
        httpretty.enable()

    def tearDown(self):
        httpretty.reset()

    def test_create_group(self):
        """Test sending create group request"""
        httpretty.register_uri(
            httpretty.POST,
            'https://api.spotinst.io/aws/ec2/group',
            content_type='text/json',
            body=json.dumps({
                'response': {
                    'items': [{
                        'name': 'foo'
                    }]
                }
            }))

        result = self.spotinst_client.create_group({
            'group': {
                'name': 'foo'
            }
        })

        self.assertEqual(result, {'name': 'foo'})
        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group')
        self.assertEqual(httpretty.last_request().method, 'POST')

    def test_update_group(self):
        """Test sending update group request"""
        httpretty.register_uri(
            httpretty.PUT,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785',
            body=json.dumps({})
        )

        self.spotinst_client.update_group('sig-5af12785', {
            'group': {
                'name': 'foo'
            }
        })

        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785')
        self.assertEqual(httpretty.last_request().method, 'PUT')

    def test_group_status(self):
        """Test sending group status request"""
        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785/status',
            body=json.dumps({
                "request": {
                    "id": "c090574f-2168-4a4c-b097-99be6d3d5dbc",
                    "url": "/aws/ec2/group/sig-afd179af/status",
                    "method": "GET",
                    "time": "2015-06-28T15:45:36.881Z"
                },
                "response": {
                    "status": {
                        "code": 200,
                        "message": "OK"
                    },
                    "kind": "spotinst:group",
                    "items": [{
                        "createdAt": "2015-06-28T15:45:31.000Z",
                        "instanceId": None,
                        "spotRequestId": "sir-02b5n3tx",
                        "instanceType": "r3.large",
                        "availabilityZone": "us-east-1e",
                        "product": "Linux/UNIX",
                        "status": "pending-evaluation"
                    }],
                    "count": 1
                }
            })
        )

        status = self.spotinst_client.get_group_status('sig-5af12785')

        self.assertEqual(status, [{
            "createdAt": "2015-06-28T15:45:31.000Z",
            "instanceId": None,
            "spotRequestId": "sir-02b5n3tx",
            "instanceType": "r3.large",
            "availabilityZone": "us-east-1e",
            "product": "Linux/UNIX",
            "status": "pending-evaluation"
        }])
        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785/status')
        self.assertEqual(httpretty.last_request().method, 'GET')

    def test_get_groups(self):
        """Test sending group list request"""
        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group',
            body=json.dumps({
                'response': {
                    'items': [{
                        'instanceId': 'i-abcd1234'
                    }]
                }
            })
        )

        groups = self.spotinst_client.get_groups()

        self.assertEqual(groups, [{
            'instanceId': 'i-abcd1234'
        }])
        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group')
        self.assertEqual(httpretty.last_request().method, 'GET')

    def test_delete_group(self):
        """Test sending delete group request"""
        httpretty.register_uri(
            httpretty.DELETE,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785',
            body=json.dumps({
                "request": {
                    "id": "4a0d5084-0b41-4255-82e5-d64a8232d7cc",
                    "url": "/aws/ec2/group/sig-5af12785",
                    "method": "DELETE",
                    "time": "2015-06-28T15:52:45.772Z"
                },
                "response": {
                    "status": {
                        "code": 200,
                        "message": "OK"
                    }
                }
            })
        )

        self.spotinst_client.delete_group('sig-5af12785')

        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785')
        self.assertEqual(httpretty.last_request().method, 'DELETE')

    def test_roll_group(self):
        """Test sending roll group request"""
        httpretty.register_uri(
            httpretty.PUT,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785/roll',
            body=json.dumps({
                "request": {
                    "id": "3213e42e-455e-4901-a185-cc3eb65fac5f",
                    "url": "/aws/ec2/group/sig-5af12785/roll",
                    "method": "PUT",
                    "time": "2016-02-10T15:49:11.911Z"
                },
                "response": {
                    "status": {
                        "code": 200,
                        "message": "OK"
                    },
                    "kind": "spotinst:aws:ec2:group:roll",
                }
            })
        )

        self.spotinst_client.roll_group('sig-5af12785', 100, 100, health_check_type='EC2')

        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785/roll')
        self.assertEqual(httpretty.last_request().method, 'PUT')

    def test_get_deployments(self):
        """Test getting a list of deployments for a group"""
        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785/roll',
            body=json.dumps({
                "request": {
                    "id": "3213e42e-455e-4901-a185-cc3eb65fac5f",
                    "url": "/aws/ec2/group/sig-5af12785/roll",
                    "method": "PUT",
                    "time": "2016-02-10T15:49:11.911Z"
                },
                "response": {
                    "items": [
                        {
                            "id": "sbgd-c47a527a",
                            "status": "finished",
                            "progress": {
                                "unit": "percent",
                                "value": 100
                            },
                            "createdAt": "2017-05-24T12:12:39.000+0000",
                            "updatedAt": "2017-05-24T12:19:17.000+0000"
                        },
                        {
                            "id": "sbgd-f789ec37",
                            "status": "in_progress",
                            "progress": {
                                "unit": "percent",
                                "value": 0
                            },
                            "createdAt": "2017-05-24T20:13:37.000+0000",
                            "updatedAt": "2017-05-24T20:15:17.000+0000"
                        }],
                    "count": 2
                }
            })
        )

        deployments = self.spotinst_client.get_deployments('sig-5af12785')

        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785/roll')
        self.assertEqual(httpretty.last_request().method, 'GET')
        self.assertEqual(len(deployments), 2)

    def test_get_roll_status(self):
        """Test getting the status of a deployment"""
        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group/sig-5af12785/roll/sbgd-c47a527a',
            body=json.dumps({
                "request": {
                    "id": "3213e42e-455e-4901-a185-cc3eb65fac5f",
                    "url": "/aws/ec2/group/sig-5af12785/roll",
                    "method": "PUT",
                    "time": "2016-02-10T15:49:11.911Z"
                },
                "response": {
                    "items": [
                        {
                            "id": "sbgd-c47a527a",
                            "status": "finished",
                            "progress": {
                                "unit": "percent",
                                "value": 100
                            },
                            "createdAt": "2017-05-24T12:12:39.000+0000",
                            "updatedAt": "2017-05-24T12:19:17.000+0000"
                        }],
                    "count": 1
                }
            })
        )

        status = self.spotinst_client.get_roll_status('sig-5af12785', 'sbgd-c47a527a')

        self.assertEqual(httpretty.last_request().path, '/aws/ec2/group/sig-5af12785/roll/sbgd-c47a527a')
        self.assertEqual(httpretty.last_request().method, 'GET')
        self.assertEqual(status['status'], 'finished')

    # pylint: disable=unused-argument
    @patch("time.sleep", return_value=None)
    def test_throttle_error(self, sleep_mock):
        """Test handling spotinst throttling"""
        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group',
            status=429
        )

        self.assertRaises(SpotinstRateExceededException, self.spotinst_client.get_groups)

    # pylint: disable=unused-argument
    @patch("time.sleep", return_value=None)
    def test_retry(self, sleep_mock):
        """Test request keeps retrying until successful"""
        responses = [
            httpretty.Response(body='', status=429),
            httpretty.Response(body='', status=429),
            httpretty.Response(body=json.dumps({
                'response': {
                    'items': [{
                        'name': 'foo'
                    }]
                }
            }))
        ]

        httpretty.register_uri(
            httpretty.GET,
            'https://api.spotinst.io/aws/ec2/group',
            responses=responses
        )

        groups = self.spotinst_client.get_groups()

        self.assertEqual([{'name': 'foo'}], groups)
