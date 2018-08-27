"""
This module has utility functions for working with aws resources
"""
import logging
import time
from random import randint

from botocore.exceptions import ClientError, WaiterError
from boto.exception import EC2ResponseError, BotoServerError

from .exceptions import (
    TimeoutError,
    ExpectedTimeoutError,
    S3WritingError
)

logger = logging.getLogger(__name__)

STATE_POLL_INTERVAL = 2  # seconds
INSTANCE_SSHABLE_POLL_INTERVAL = 15  # seconds
MAX_POLL_INTERVAL = 60  # seconds


def create_filters(filter_dict):
    """
    Converts a dict to a list of boto3 filters. The keys and value of the dict represent
    the Name and Values of a filter, respectively.
    """
    filters = []
    for key in filter_dict.keys():
        filters.append({'Name': key, 'Values': filter_dict[key]})

    return filters


def tag2dict(tags):
    """ Converts a list of AWS tag dicts to a single dict with corresponding keys and values """
    return {tag.get('Key'): tag.get('Value') for tag in tags or {}}


def key_values_to_tags(dicts):
    """
    Converts the list of key:value strings (example ["mykey:myValue", ...])
    into a list of AWS tag dicts (example: [{'Key': 'mykey', 'Value': 'myValue'}, ...]
    """
    return [{'Key': tag_key_value[0], 'Value': tag_key_value[1]}
            for tag_key_value in [key_value_option.split(":", 1) for key_value_option in dicts]]


def find_or_create(find, create):
    """Given a find and a create function, create a resource if it doesn't exist"""
    result = find()
    return result if result else create()


def keep_trying(max_time, fun, *args, **kwargs):
    """
    Execute function fun with args and kwargs until it does
    not throw exception or max time has passed.

    After each failed attempt a delay is introduced using Jitter.backoff() function.

    Note: If you are only concerned about throttling use throttled_call
    instead. Any irrecoverable exception within a keep_trying will
    cause a max_time delay.
    """

    jitter = Jitter()
    time_passed = 0
    while True:
        try:
            return fun(*args, **kwargs)
        except Exception:
            if logging.getLogger().level == logging.DEBUG:
                logger.exception("Failed to run %s.", fun)
            if time_passed > max_time:
                raise
            time_passed = jitter.backoff()


def throttled_call(fun, *args, **kwargs):
    """
    Execute function fun with args and kwargs until it does
    not throw a throttled exception or 5 minutes have passed.

    After each failed attempt a delay is introduced using Jitter.backoff() function.
    """
    max_time = 10 * 60
    jitter = Jitter()
    time_passed = 0

    while True:
        try:
            return fun(*args, **kwargs)
        except (BotoServerError, ClientError) as err:
            if logging.getLogger().level == logging.DEBUG:
                logger.exception("Failed to run %s.", fun)

            if isinstance(err, BotoServerError):
                error_code = err.error_code
            else:
                error_code = err.response['Error'].get('Code', 'Unknown')

            if (error_code not in ("Throttling", "RequestLimitExceeded")) or time_passed > max_time:
                raise

            time_passed = jitter.backoff()
        except WaiterError:
            if time_passed > max_time:
                raise

            time_passed = jitter.backoff()


def wait_for_state(resource, state, timeout=15 * 60, state_attr='state'):
    """Wait for an AWS resource to reach a specified state"""
    jitter = Jitter()
    time_passed = 0

    while True:
        try:
            resource.update()
            current_state = getattr(resource, state_attr)
            if current_state == state:
                return
            elif current_state in (u'failed', u'terminated'):
                raise ExpectedTimeoutError(
                    "{0} entered state {1} after {2}s waiting for state {3}"
                    .format(resource, current_state, time_passed, state))
        except (EC2ResponseError, BotoServerError):
            pass  # These are most likely transient, we will timeout if they are not

        if time_passed >= timeout:
            raise TimeoutError(
                "Timed out waiting for {0} to change state to {1} after {2}s."
                .format(resource, state, time_passed))

        time_passed = jitter.backoff()


def wait_for_state_boto3(describe_func, params_dict, resources_name,
                         expected_state, state_attr='state', timeout=15 * 60):
    """Wait for an AWS resource to reach a specified state using the boto3 library"""
    jitter = Jitter()
    time_passed = 0
    while True:
        try:
            resources = describe_func(**params_dict)[resources_name]
            if not isinstance(resources, list):
                resources = [resources]

            all_good = True
            failure = False
            for resource in resources:
                if resource[state_attr] in (u'failed', u'terminated'):
                    failure = True
                    all_good = False
                elif resource[state_attr] != expected_state:
                    all_good = False

            if all_good:
                return
            elif failure:
                raise ExpectedTimeoutError(
                    "At least some resources who meet the following description entered either "
                    "'failed' or 'terminated' state after {0}s waiting for state {1}:\n{2}"
                    .format(time_passed, expected_state, params_dict))
        except (EC2ResponseError, ClientError):
            pass  # These are most likely transient, we will timeout if they are not

        if time_passed >= timeout:
            raise TimeoutError(
                "Timed out waiting for resources who meet the following description to change "
                "state to {0} after {1}s:\n{2}"
                .format(expected_state, time_passed, params_dict))

        time_passed = jitter.backoff()


def wait_for_sshable(remotecmd, instance, timeout=15 * 60, quiet=False):
    """
    Returns True when host is up and sshable
    returns False on timeout
    """
    jitter = Jitter()
    time_passed = 0

    if not quiet:
        logger.info("Waiting for instance %s to be fully provisioned.", instance.id)
    wait_for_state(instance, u'running', timeout)
    if not quiet:
        logger.info("Instance %s running (booting up).", instance.id)

    while True:
        logger.debug(
            "Waiting for %s to become sshable.", instance.id)
        if remotecmd(instance, ['true'], nothrow=True)[0] == 0:
            logger.info("Instance %s now SSHable.", instance.id)
            logger.debug("Waited %s seconds for instance to boot", time_passed)
            return
        if time_passed >= timeout:
            break
        time_passed = jitter.backoff()

    raise TimeoutError(
        "Timed out waiting for instance {0} to become sshable after {1}s."
        .format(instance, timeout))


def get_boto3_paged_results(func, results_key, next_token_key='NextToken', *args, **kwargs):
    """
    Helper method for automatically making multiple boto requests for their listing functions
    :param function func: Boto3 function to call
    :param str results_key: Key of response dict that contains list items
    :param str next_token_key: Key of the response dict that contains the paging token
    :return list:
    """
    response = throttled_call(func, *args, **kwargs)
    response_items = response[results_key]

    while response.get(next_token_key):
        kwargs[next_token_key] = response[next_token_key]
        response = throttled_call(func, *args, **kwargs)
        response_items += response[results_key]

    return response_items


def check_written_s3(object_name, expected_written_length, written_length):
    """
    Check S3 object is written by checking the bytes_written from key.set_contents_from_* method
    Raise error if any problem happens so we can diagnose the causes
    """
    if expected_written_length != written_length:
        raise S3WritingError(
            "{0} is not written correctly to S3 bucket".format(object_name)
        )


class Jitter(object):
    """
    This class implements the logic to run an AWS command using Backoff with Decorrelated Jitter.
    The logic is based on the following article:
    https://www.awsarchitectureblog.com/2015/03/backoff.html
    """
    BASE = 3

    def __init__(self, min_wait=3):
        self._time_passed = 0
        self._min_wait = min_wait
        self._previous_interval = 0

    def backoff(self):
        """
        Uses a slightly modified version of the Decorrelated Jitter function as described in the AWS blog

        The main change is:
            A random value is chosen from 0 and min(max_poll_interval, prev_value * 3)
            This is different than min(max_poll_interval, rand(0, prev_value * 3) as defined in the blog
            We chose to do this to make sure we continue to get random backoff values instead of
            constantly returning the max value once enough time has passed
        """
        new_interval = randint(0, min(MAX_POLL_INTERVAL, self._previous_interval * 3))
        new_interval = max(self._min_wait, new_interval)

        time.sleep(new_interval)
        self._time_passed += new_interval
        self._previous_interval = new_interval
        return self._time_passed
