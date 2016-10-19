"""
Misc functions frequently used in disco_aws
"""

import csv
import sys
from logging import getLogger, DEBUG
from functools import wraps

from boto.exception import EC2ResponseError
from botocore.exceptions import ClientError

from .disco_constants import YES_LIST

logger = getLogger(__name__)


class EasyExit(Exception):
    """
    Raise this exception to exit your program with a log message and a non-zero status, but no stack trace
    (assuming you are running it with run_gracefully).
    """
    pass


def get_tag_value(tag_list, key):
    """
    Given a list of dictionaries representing tags, returns the value of the given key, or None if the key
    cannot be found.
    """
    for tag in tag_list:
        if tag["Key"] == key:
            return tag["Value"]
    return None


def is_truthy(value):
    """
    Return true if value resembles a affirmation
    """
    return value and value.lower() in YES_LIST


def chunker(sequence, size):
    """
    Creates a generator that yields chunks of sequence in the given size

    for group in chunker(range(0, 20), 5):
        print group
    # [0, 1, 2, 3, 4]
    # [5, 6, 7, 8, 9]
    # [10, 11, 12, 13, 14]
    # [15, 16, 17, 18, 19]
    """
    return (sequence[position:position + size] for position in xrange(0, len(sequence), size))


def graceful(func):
    """
    Decorator to apply run_gracefully to a main function.

    Since run_gracefully assumes a main function that takes no arguments, the
    wrapper is actually extremely simple.
    """
    @wraps(func)
    def run_func():
        run_gracefully(func)
    return run_func


def read_pipeline_file(pipeline_file):
    """
    Open a file with a CSV reader, check it for a couple of required headers, and return its contents
    as a list of dictionaries.
    """
    required = ['hostclass']  # fields that must be present in the headers for the file to be valid
    with open(pipeline_file, "r") as f:
        reader = csv.DictReader(f)
        logger.debug("pipeline headers: %s", reader.fieldnames)
        for required_field in required:
            if required_field not in reader.fieldnames:
                raise EasyExit("Pipeline file %s is missing required header %s (found: %s)" %
                               (pipeline_file, required_field, reader.fieldnames))
        hostclass_dicts = [line for line in reader]
    return hostclass_dicts


def run_gracefully(main_function):
    """
    Run a "main" function with standardized exception trapping, to make it easy
    to avoid certain unnecessary stack traces.

    If debug logging is switched on, stack traces will return.
    """
    try:
        main_function()
    except EasyExit as msg:
        logger.error(str(msg))
        sys.exit(1)
    except KeyboardInterrupt:
        # swallow the exception unless we turned on debugging, in which case
        # we might want to know what infinite loop we were stuck in
        if getLogger().isEnabledFor(DEBUG):
            raise
        sys.exit(1)
    except (EC2ResponseError, ClientError) as err:
        logger.error("EC2 Error response: %s", err.message)
        if getLogger().isEnabledFor(DEBUG):
            raise
        sys.exit(1)


def size_as_recurrence_map(size, sentinel=''):
    """
    :return: dict, size as "recurrence" map. For example:
             - size = no value, will return: {<sentinel>: None}
             - size = simple int value of 5, will return: {<sentinel>: 5}
             - size = timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: {'0 10 * * *': 24,
                                                                                          '0 22 * * *': 2}
    """
    if not size and size != 0:
        return {sentinel: None}
    else:
        return {sentinel: int(size)} if str(size).isdigit() else {
            part.split('@')[1]: int(part.split('@')[0])
            for part in str(size).split(':')}


def size_as_minimum_int_or_none(size):
    """
    :return: int, max_size as max int or None. For example:
             - size = no value, will return: None
             - size = simple int value of 5, will return: 5
             - size = timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: 2
    """
    return min(size_as_recurrence_map(size).values())


def size_as_maximum_int_or_none(size):
    """
    :return: int, max_size as max int or None. For example:
             - size = no value, will return: None
             - size = simple int value of 5, will return: 5
             - size = timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: 24
    """
    return max(size_as_recurrence_map(size).values())
