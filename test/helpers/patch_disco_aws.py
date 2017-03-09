"""
Some mocking to make writing unit tests for DiscoAWS easier.  To use decorate your
test methods with @patch_disco_aws and add **kwargs to your test signatures.
The mocks will be provided as keyword arguments starting with mock.  For example:

    >>> from unittest import TestCase
    >>> from disco_aws_automation import DiscoAWS
    >>> class YourTestClass(TestCase):
    ...     @patch_disco_aws
    ...     def test_your_stuff(self, mock_config, **kwargs):
    ...         aws = DiscoAWS(config=mock_config, environment_name="somename")
    ...         # test some stuff

    or you can replace the config with your own

    >>> from unittest import TestCase
    >>> class YourTestClass(TestCase):
    ...     @patch_disco_aws
    ...     def test_more_stuff(self, **kwargs):
    ...         config_dict = get_default_config_dict()
    ...         config_dict["section"]["key"] = "val"
    ...         aws = DiscoAWS(config=get_mock_config(config_dict), environment_name="somename")
    ...         # test more stuff

See PATCH_LIST and patch_disco_aws for available mocks and their names.
"""
from ConfigParser import NoSectionError, NoOptionError

from mock import patch
from moto import mock_ec2, mock_s3, mock_autoscaling, mock_route53, mock_elb

from test.helpers.patcher import patcher
from disco_aws_automation.disco_config import AsiaqConfig

TEST_ENV_NAME = "unittestenv"
PATCH_LIST = [patch("disco_aws_automation.disco_aws.wait_for_state",
                    kwargs_field="mock_wait"),
              patch("disco_aws_automation.disco_vpc.DiscoVPC.fetch_environment",
                    kwargs_field="mock_fetch_env")]


def get_default_config_dict():
    '''Starting Configuration for a hostclass'''
    return {"mhcunittest": {"subnet": "intranet",
                            "security_group": "intranet",
                            "ssh_key_name": "unittestkey",
                            "instance_profile_name": "unittestprofile",
                            "public_ip": "False",
                            "ip_address": None,
                            "eip": None},
            "disco_aws": {"default_meta_network": "intranet",
                          "project_name": "unittest",
                          "default_enable_proxy": "True",
                          "http_proxy_hostclass": "mhchttpproxy",
                          "zookeeper_hostclass": "mhczookeeper",
                          "logger_hostclass": "mhclogger",
                          "logforwarder_hostclass": "mhclogforwarder",
                          "default_smoketest_termination": "True",
                          "default_environment": "auto-vpc-type"},
            "mhczookeeper": {"ip_address": "10.0.0.1"}}


def get_mock_config(config_dict=None):
    '''
    Returns a config class which returns the contents of either the
    default dictionary or a dictionary passed in.
    The format of the dictionary is
    {"section": {"key" : "value"}
    '''
    return MockAsiaqConfig(config_dict)


class MockAsiaqConfig(AsiaqConfig):
    """
    A ConfigParser subclass which returns the contents of either the
    default dictionary or a dictionary passed in, rather than expecting to parse any actual files.
    The format of the dictionary is
    {"section": {"key" : "value"}
    """

    def __init__(self, config_dict=None, environment=None):
        AsiaqConfig.__init__(self, environment=environment)
        self.config_dict = config_dict or get_default_config_dict()

    def get(self, section, key, **_kwargs):
        if section not in self.config_dict:
            raise NoSectionError(section)
        if key not in self.config_dict[section]:
            raise NoOptionError(key, section)
        return self.config_dict[section][key]

    def sections(self):
        return self.config_dict.keys()

    def has_option(self, section, key):
        return (section in self.config_dict) and (key in self.config_dict[section])

    def has_section(self, section):
        return section in self.config_dict

    def items(self, section, **_kwargs):
        return self.config_dict[section].iteritems() if self.config_dict.get(section) else []


patch_disco_aws = patcher(patches=PATCH_LIST,
                          decorators=[mock_ec2, mock_s3, mock_autoscaling, mock_route53, mock_elb],
                          mock_config=get_mock_config())
