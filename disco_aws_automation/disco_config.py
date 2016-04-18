import boto3

from . import read_config
from .disco_constants import DEFAULT_CONFIG_SECTION


class DiscoAWSConfigReader(object):
    """Class for reading all AWS configuration information"""

    def __init__(self, environment_name, environment_type, config_aws=None, config_vpc=None):
        self._config_aws = config_aws or None  # Lazily Initialized unless passed in
        self._config_vpc = config_aws or None  # Lazily Initialized unless passed in
        self.environment_name = environment_name
        self.environment_type = environment_type
        self.env_specific_suffix = "@{}".format(environment_name)
        self._session = None  # Lazily initialized
        self._region = None  # Lazily initialized
        self._account_id = None  # Lazily initialized

    @property
    def session(self):
        if not self._session:
            self._session = boto3.session.Session()
        return self._session

    @property
    def account_id(self):
        if not self._account_id:
            self._account_id = boto3.resource('iam').CurrentUser().arn.split(':')[4]
        return self._account_id

    @property
    def region(self):
        if not self._region:
            # Doing this requires boto3>=1.2.4
            # Could use undocumented and unsupported workaround for earlier versions:
            # session._session.get_config_variable('region')
            self._region = self.session.region_name
        return self._region

    @property
    def config_aws(self):
        if not self._config_aws:
            self._config_aws = read_config()
        return self._config_aws

    @property
    def config_vpc(self):
        if not self._config_vpc:
            self._config_vpc = read_config("disco_vpc.ini")
        return self._config_vpc

    @staticmethod
    def get_default_environment():
        return read_config().get("disco_aws", "default_environment")

    def get_es_option(self, option, default=None):
        '''Returns appropriate configuration for the current environment'''
        return self.get_vpc_option(option, default)

    def get_vpc_option(self, option, default=None):
        '''Returns appropriate configuration for the current environment'''
        env_section = "env:{0}".format(self.environment_name)
        envtype_section = "envtype:{0}".format(self.environment_type)
        peering_section = "peerings"

        value = None

        if self.config_vpc.has_option(env_section, option):
            value = self.config_vpc.get(env_section, option)
        elif self.config_vpc.has_option(envtype_section, option):
            value = self.config_vpc.get(envtype_section, option)
        elif self.config_vpc.has_option(peering_section, option):
            value = self.config_vpc.get(peering_section, option)

        return value or default

    def get_aws_option(self, option, section=DEFAULT_CONFIG_SECTION, default=None):
        """Get a value from the config"""
        env_option = "{0}@{1}".format(option, self.environment_name)
        default_option = "default_{0}".format(option)
        default_env_option = "default_{0}".format(env_option)

        value = None

        if self.config_aws.has_option(section, env_option):
            value = self.config_aws.get(section, env_option)
        if self.config_aws.has_option(section, option):
            value = self.config_aws.get(section, option)
        elif self.config_aws.has_option(DEFAULT_CONFIG_SECTION, default_env_option):
            value = self.config_aws.get(DEFAULT_CONFIG_SECTION, default_env_option)
        elif self.config_aws.has_option(DEFAULT_CONFIG_SECTION, default_option):
            value = self.config_aws.get(DEFAULT_CONFIG_SECTION, default_option)

        return value or default

    def get_hostclass_option(self, option, hostclass, default=None):
        """Fetch a hostclass configuration option, if it does not exist get the default"""
        return self.get_aws_option(option, hostclass, default)
