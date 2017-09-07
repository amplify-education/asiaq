"""Contains SpotinstClient class for taking to the Spotinst REST API"""
import json
import logging
import urllib2

from disco_aws_automation.exceptions import SpotinstApiException, SpotinstRateExceededException
from disco_aws_automation.resource_helper import Jitter

SPOTINST_API_HOST = 'https://api.spotinst.io'
logger = logging.getLogger(__name__)


class SpotinstClient(object):
    """A client for the Spotinst REST API"""

    def __init__(self, token):
        self.token = token

    def create_group(self, group_config):
        """
        Create a new Elastigroup
        :param dict group_config: Config parameters for Elastigroup
        :return: Elastigroup
        :rtype: dict
        """
        response = self._make_throttled_request(path='aws/ec2/group', data=group_config, method='post')
        return response['response']['items'][0]

    def update_group(self, group_id, group_config):
        """
        Update an existing Elastigroup
        :param str group_id: Id of group to update
        :param dict group_config: New group config
        """
        self._make_throttled_request(path='aws/ec2/group/%s' % group_id, data=group_config, method='put')

    def get_group_status(self, group_id):
        """
        Get group status
        :param str group_id: Id of ELastigroup to get status info for
        :return: List of instances in a Elastigroup
        :rtype: list[dict]
        """
        response = self._make_throttled_request(path='aws/ec2/group/%s' % group_id + '/status', method='get')
        return response['response']['items']

    def get_groups(self):
        """
        Get a list of all Elastigroup
        :return: Lst of Elastigroups
        :rtype: list[dict]
        """
        return self._make_throttled_request(path='aws/ec2/group', method='get')['response']['items']

    def delete_group(self, group_id):
        """
        Delete an Elastigroup
        :param str group_id: Id of group to delete
        """
        self._make_throttled_request(path='aws/ec2/group/%s' % group_id, method='delete')

    def roll_group(self, group_id, batch_percentage, grace_period, health_check_type):
        """
        Spin up a new set of instances and then shutdown the old instances
        :param str group_id: Id of Elastigroup to perform roll operation on
        :param int batch_percentage: Percentage of the group to roll at a time
        :param int grace_period: Amount of time in seconds to wait for instances to pass health checks
        :param str health_check_type: Type of health check to use. Available options are ELB, TARGET_GROUP,
                                      MLB, HCS, EC2, NONE
        """
        request = {
            "batchSizePercentage": batch_percentage,
            "gracePeriod": grace_period,
            "healthCheckType": health_check_type,
            "strategy": {
                "action": "REPLACE_SERVER"
            }
        }
        self._make_throttled_request(path='aws/ec2/group/%s/roll' % group_id, data=request, method='put')

    def get_deployments(self, group_id):
        """
        Get list of current and past deployements for a Elastigroup
        :param str group_id:
        :return list[dict]:
        """
        response = self._make_throttled_request(path='aws/ec2/group/%s/roll' % group_id, method='get')
        deploys = response['response']['items']
        return sorted(deploys, key=lambda deploy: deploy['createdAt'])

    def get_roll_status(self, group_id, deploy_id):
        """
        Get info about a specific deployment (also called a roll) for a group
        :param str group_id:
        :param str deploy_id:
        :return dict:
        """
        response = self._make_throttled_request(
            path='aws/ec2/group/%s/roll/%s' % (group_id, deploy_id),
            method='get'
        )
        return response['response']['items'][0]

    def _make_throttled_request(self, method, path, params=None, data=None):
        return self._throttle_spotinst_call(self._make_request, method, path, params, data)

    def _make_request(self, method, path, params=None, data=None):
        """
        Convenience function for making requests to the Spotinst API.

        :param str method: What HTTP method to use.
        :param str path: The API endpoint to call. IE: aws/ec2/group
        :param dict params: Dictionary of query parameters.
        :param dict data: Body data.
        :return: The response from the Spotinst API.
        :rtype: dict
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer {}".format(self.token)
        }

        url = '{0}/{1}'.format(SPOTINST_API_HOST, path)
        req = MethodRequest(
            url + ('?' + urllib2.urlencode(params) if params else ''),
            json.dumps(data) if data else None,
            headers,
            method=method.upper()
        )

        try:
            response = urllib2.urlopen(req)
        except urllib2.HTTPError as err:
            response = err

        if response.getcode() == 401:
            raise SpotinstApiException("Provided Spotinst API token is not valid")

        if response.getcode() == 429:
            raise SpotinstRateExceededException("Rate exceeded while calling {0} {1}".format(method, path))

        try:
            ret = json.loads(response.read() or '{}')
        except ValueError:
            raise SpotinstApiException("Spotinst API did not return JSON response: {0}".format(response))

        if response.getcode() != 200:
            status = ret['response']['status']
            req_id = ret['request']['id']
            errors = ret['response'].get('errors') or []

            for error in errors:
                if error.get('code') in ("Throttling", "RequestLimitExceeded"):
                    raise SpotinstRateExceededException("Rate exceeded while calling {0} {1}"
                                                        .format(method, path))

            raise SpotinstApiException(
                "Unknown Spotinst API error encountered: {0} {1}. RequestId {2}"
                .format(status, errors, req_id)
            )

        return ret

    def _throttle_spotinst_call(self, fun, *args, **kwargs):
        max_time = 5 * 60
        jitter = Jitter(min_wait=60)  # wait atleast 60 seconds because our rate limit resets then
        time_passed = 0

        while True:
            try:
                return fun(*args, **kwargs)
            except SpotinstRateExceededException:
                if logging.getLogger().level == logging.DEBUG:
                    logger.exception("Failed to run %s.", fun)

                if time_passed > max_time:
                    raise

                time_passed = jitter.backoff()


class MethodRequest(urllib2.Request):
    """Adds ability to choose HTTP method to the regular urllib2 Request object"""
    def __init__(self, *args, **kwargs):
        if 'method' in kwargs:
            self._method = kwargs['method']
            del kwargs['method']
        else:
            self._method = None
        urllib2.Request.__init__(self, *args, **kwargs)

    def get_method(self):
        """Get the HTTP method of the request"""
        if self._method is not None:
            return self._method
        return urllib2.Request.get_method(self)
