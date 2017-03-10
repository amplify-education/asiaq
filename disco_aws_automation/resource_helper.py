"""
This module has utility functions for working with aws resources
"""
import logging
import time

from botocore.exceptions import ClientError
from boto.exception import BotoServerError, EC2ResponseError

from .jitter import jitter
from .exceptions import (
    TimeoutError,
    ExpectedTimeoutError,
    EarlyExitException,
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
    if result:
        return result
    else:
        return create()


def keep_trying(max_time, fun, *args, **kwargs):
    """
    Execute function fun with args and kwargs until it does
    not throw exception or max time has passed.

    After each failed attempt a delay is introduced using Jitter.backoff() function.

    Note: If you are only concerned about throttling use throttled_call
    instead. Any irrecoverable exception within a keep_trying will
    cause a max_time delay.
    """
    @jitter(max_time=max_time, logger=logger)
    def run_funct(fun, *args, **kwargs):
        """
        :param fun:
        :param args:
        :param kwargs:
        :return:
        """
        return fun(*args, **kwargs)

    return run_funct(fun, *args, **kwargs)


@jitter((BotoServerError, ClientError), 300, error_codes=("Throttling", "RequestLimitExceeded"),
        logger=logger)
def throttled_call(fun, *args, **kwargs):
    """
    Execute function fun with args and kwargs until it does
    not throw a throttled exception or 5 minutes have passed.

    After each failed attempt a delay is introduced using Jitter.backoff() function.
    """
    return fun(*args, **kwargs)


def wait_for_state(resource, state, timeout=15 * 60, state_attr='state'):
    """Wait for an AWS resource to reach a specified state"""

    timeout_msg = "Timed out waiting for {0} to change state to {1} after ".format(resource, state) + "{0}s."

    @jitter((EC2ResponseError, EarlyExitException), max_time=timeout, timeout_exception=TimeoutError,
            timeout_msg=timeout_msg, logger=logger)
    def check_resource_state(resource, state, state_attr, start_time):
        """Check resource state using boto2"""
        resource.update()
        current_state = getattr(resource, state_attr)
        if current_state == state:
            return
        elif current_state in (u'failed', u'terminated'):
            raise ExpectedTimeoutError(
                "{0} entered state {1} after {2}s waiting for state {3}"
                .format(resource, current_state, time.time() - start_time, state))
        raise EarlyExitException({"Error": {"Code": "internal_error"}}, "update")

    return check_resource_state(resource, state, state_attr, time.time())


def wait_for_state_boto3(describe_func, params_dict, resources_name,
                         expected_state, state_attr='state', timeout=15 * 60):
    """Wait for an AWS resource to reach a specified state using the boto3 library"""

    timeout_msg = "Timed out waiting for resources who meet the following description to change state to " \
                  "state to {0} after :\n{2}".format(expected_state, params_dict).replace("after :",
                                                                                          "after {0}:", 1)

    @jitter((EarlyExitException, EC2ResponseError, ClientError), max_time=timeout,
            timeout_msg=timeout_msg, logger=logger)
    def check_resource_state(describe_func, params_dict, resources_name, expected_state, state_attr,
                             start_time):
        """Check resource state using boto3"""
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
                .format(time.time() - start_time, expected_state, params_dict))

        raise EarlyExitException({"Error": {"Code": "internal_error"}}, "update")

    return check_resource_state(describe_func, params_dict, resources_name, expected_state,
                                state_attr, time.time())


def wait_for_sshable(remotecmd, instance, timeout=15 * 60, quiet=False):
    """
    Returns True when host is up and sshable
    returns False on timeout
    """
    timeout_msg = "Timed out waiting for instance {0} to become sshable after ".format(instance) + "{0}s."

    @jitter(EarlyExitException, max_time=timeout, timeout_msg=timeout_msg, logger=logger)
    def run_remotecmd(remotecmd, instance, start_time):
        """Run remote command"""
        if remotecmd(instance, ['true'], nothrow=True)[0] == 0:
            logger.info("Instance %s now SSHable.", instance.id)
            logger.debug("Waited %s seconds for instance to boot", time.time() - start_time)
            return
        raise EarlyExitException({"Error": {"Code": "internal_error"}}, "remotecmd")

    if not quiet:
        logger.info("Waiting for instance %s to be fully provisioned.", instance.id)
    wait_for_state(instance, u'running', timeout)
    if not quiet:
        logger.info("Instance %s running (booting up).", instance.id)

    return run_remotecmd(remotecmd=remotecmd, instance=instance, start_time=time.time())


def check_written_s3(object_name, expected_written_length, written_length):
    """
    Check S3 object is written by checking the bytes_written from key.set_contents_from_* method
    Raise error if any problem happens so we can diagnose the causes
    """
    if expected_written_length != written_length:
        raise S3WritingError(
            "{0} is not written correctly to S3 bucket".format(object_name)
        )


# class Jitter(object):
#     """
#     This class implements the logic to run an AWS command using Backoff with Decorrelated Jitter.
#     The logic is based on the following article:
#     https://www.awsarchitectureblog.com/2015/03/backoff.html
#     """
#     def __init__(self, timeout):
#         self.time_passed = 0
#         self._base = 3
#         self._cycle = 0
#         self._timeout = timeout
#
#     def backoff(self):
#         """
#         This function use a cycle count,
#         calculates jitter and executes sleep for the calculated time.
#         The minimum value 'cycle' can take is 1
#         """
#         # Check if we exceed the max waiting time
#         if self._timeout < self.time_passed:
#             return self.time_passed
#         self._cycle += 1
#         new_interval = min(MAX_POLL_INTERVAL, randint(self._base, self._cycle * 3))
#         time.sleep(new_interval)
#         self.time_passed += new_interval
#         return self.time_passed
#
#     def do_backoff(self, exception, error_codes):
#         """default logic deciding if backoff should executed based on the raised exception"""
#         # pylint: disable=unused-argument
#         return True
#
#     def run_with_backoff(self, func, wait_msg, error_codes, *args, **kwargs):
#         """
#         Execute a given function with backoff
#         """
#         time_passed = 0
#         while True:
#             if wait_msg:
#                 logger.debug(wait_msg)
#             try:
#                 return func(*args, **kwargs)
#             except ExpectedTimeoutError:
#                 raise
#             except Exception as err:
#                 if logging.getLogger().level == logging.DEBUG:
#                     logger.exception("Failed to run %s.", func)
#
#                 # We need to filter on error code
#                 if not self.do_backoff(err, error_codes):
#                     raise
#
#                 if time_passed > self._timeout:
#                     raise err
#                 else:
#                     time_passed = self.backoff()
