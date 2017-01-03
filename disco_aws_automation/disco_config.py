"""
Package for reading configurations out of standard locations.
"""

import os
import os.path
from ConfigParser import ConfigParser, NoOptionError
from logging import getLogger

from .exceptions import AsiaqConfigError, ProgrammerError
from .disco_constants import DEFAULT_CONFIG_SECTION  # this should not be a shared constant, eventually

ASIAQ_CONFIG = os.getenv("ASIAQ_CONFIG", ".")
DEFAULT_CONFIG_FILE = "disco_aws.ini"
_LOG = getLogger(__name__)


def read_config(*path_components, **kwargs):
    """
    Normalize and read in a config file (defaulting to "disco_aws.ini").

    For call compatibility, we allow either an arglist or a named argument.  If you are sufficiently
    over-clever to use both the config_file keyword argument and an argument list, you should get
    a reasonable behavior (the keyword arg will be the file, the other parts will be the directory path),
    but seriously why?
    """
    all_components = list(path_components)  # copy the list to avoid aliasing
    if 'config_file' in kwargs:
        all_components.append(kwargs['config_file'])
    real_config_file = normalize_path(all_components or DEFAULT_CONFIG_FILE)
    _LOG.debug("Reading config file %s", real_config_file)
    config = AsiaqConfig(environment=kwargs.get('environment'))
    config.read(real_config_file)
    return config


def normalize_path(*path_components):
    """
    Prepend ASIAQ_CONFIG (or ".") to the list of path components, and join them into a path string.
    If the resulting path points to a nonexistent file, raise AsiaqConfigError, otherwise return the path.

    Takes either an arglist or a single list as an argument.

    NOTE: directories and symlinks "exist" for purposes of this function.
    """
    real_components = (
        path_components[0] if path_components and isinstance(path_components[0], (tuple, list))
        else path_components)
    normalized_path = os.path.join(ASIAQ_CONFIG, *real_components)
    if os.path.exists(normalized_path):
        return normalized_path
    else:
        raise AsiaqConfigError("Config path not found: %s" % normalized_path)


def open_normalized(*path_components, **kwargs):
    """
    Find a file in the configuration directory and open it.  Non-keyword arguments
    are treated as path segments; keyword arguments are passed through to the "open" function.
    """
    real_path = normalize_path(path_components)
    return open(real_path, **kwargs)


class AsiaqConfig(ConfigParser):
    """
    Wrap configuration-reading shortcuts around the ConfigParser object.

    All methods accept an "environment" parameter, but recommended usage is to pass in the
    desired environment name at construction time, rather than at call time.
    """

    S3_BUCKET_BASE_OPTION = 's3_bucket_base'
    S3_BUCKET_SUFFIX_OPTION = 's3_bucket_suffix'
    DEFAULT_ENVIRONMENT_OPTION = 'default_environment'

    def __init__(self, environment=None):
        ConfigParser.__init__(self)  # ConfigParser is an old-style class
        self._real_environment = environment

    @property
    def environment(self):
        """
        The environment name passed in at construction, or the default value for this config file.

        Lazily populated since the config file has not yet been read at initialization time.
        """
        if not self._real_environment:
            self._real_environment = self.get(DEFAULT_CONFIG_SECTION, self.DEFAULT_ENVIRONMENT_OPTION)
        return self._real_environment

    def get_asiaq_option(self, option, section=DEFAULT_CONFIG_SECTION, environment=None,
                         required=True, default=None):
        """
        Get a value from the config, checking first for an environment-specific value, then
        a generic value, then an env-specific default value in the default section, then a
        non-env-specific default value in the default section.

        In the case where none of these options has been set, if the "required" option is False,
        return the value of the "default" option; otherwise, raise NoOptionError.
        """
        if required and (default is not None):
            raise ProgrammerError("Using the 'default' option when 'required' is True makes no sense.")
        if not environment:
            environment = self.environment
        env_option = "{0}@{1}".format(option, environment)
        default_option = "default_{0}".format(option)
        default_env_option = "default_{0}".format(env_option)

        if self.has_option(section, env_option):
            return self.get(section, env_option)
        if self.has_option(section, option):
            return self.get(section, option)
        elif self.has_option(DEFAULT_CONFIG_SECTION, default_env_option):
            return self.get(DEFAULT_CONFIG_SECTION, default_env_option)
        elif self.has_option(DEFAULT_CONFIG_SECTION, default_option):
            return self.get(DEFAULT_CONFIG_SECTION, default_option)

        if required:
            raise NoOptionError(option, section)
        return default

    def get_asiaq_s3_bucket_name(self, bucket_tag, environment=None, separator='--'):
        """Construct a standardized bucket name based on configured prefix and suffix values."""
        bucket_base = self.get_asiaq_option(self.S3_BUCKET_BASE_OPTION, environment=environment)
        bucket_suffix = self.get_asiaq_option(self.S3_BUCKET_SUFFIX_OPTION, required=False,
                                              environment=environment)
        bucket_parts = [bucket_base, bucket_tag]
        if bucket_suffix:
            bucket_parts.append(bucket_suffix)
        return separator.join(bucket_parts)
