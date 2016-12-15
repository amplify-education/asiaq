"""Tests for disco_config utilities."""

from unittest import TestCase
from copy import deepcopy
from ConfigParser import NoOptionError
from mock import patch, Mock

from disco_aws_automation import disco_config, exceptions
from test.helpers.patch_disco_aws import MockAsiaqConfig


@patch("disco_aws_automation.disco_config.ASIAQ_CONFIG", "FAKE_CONFIG_DIR")
class TestNormalizePath(TestCase):
    """Tests for the normalize_path utility function."""

    @patch('os.path.exists')
    def test__no_such_path__exception(self, path_exists):
        "Normalize path finds no such path - exception."
        path_exists.return_value = False
        self.assertRaises(exceptions.AsiaqConfigError, disco_config.normalize_path, "yabba", "dabba")
        path_exists.assert_called_once_with("FAKE_CONFIG_DIR/yabba/dabba")

    @patch('os.path.exists')
    def test__path_exists__path_returned(self, path_exists):
        "Normalize path thinks the path exists - path is returned"
        path_exists.return_value = True
        found = disco_config.normalize_path("yabba", "dabba")
        self.assertEquals(found, "FAKE_CONFIG_DIR/yabba/dabba")
        path_exists.assert_called_once_with(found)

    @patch('os.path.exists')
    def test__list_arg__correct_path_returned(self, path_exists):
        "Normalize path works with a single list-typed argument"
        path_exists.return_value = True
        found = disco_config.normalize_path(["yabba", "dabba"])
        self.assertEquals(found, "FAKE_CONFIG_DIR/yabba/dabba")
        path_exists.assert_called_once_with(found)

    @patch('os.path.exists')
    def test__tuple_arg__correct_path_returned(self, path_exists):
        "Normalize path with a single tuple-typed argument"
        path_exists.return_value = True
        found = disco_config.normalize_path(("yabba", "dabba"))
        self.assertEquals(found, "FAKE_CONFIG_DIR/yabba/dabba")
        path_exists.assert_called_once_with(found)


@patch("disco_aws_automation.disco_config.ASIAQ_CONFIG", "FAKE_CONFIG_DIR")
@patch('disco_aws_automation.disco_config.AsiaqConfig')
class TestReadConfig(TestCase):
    """Tests for the read_config utility function."""

    @patch('os.path.exists', Mock(return_value=True))
    def test__no_arg__default_behavior(self, configparser_constructor):
        "Default argument for read_config works"
        parser = Mock()
        configparser_constructor.return_value = parser
        parsed = disco_config.read_config()
        self.assertIs(parsed, parser)
        parser.read.assert_called_once_with("FAKE_CONFIG_DIR/disco_aws.ini")

    @patch('os.path.exists', Mock(return_value=True))
    def test__named_arg__expected_behavior(self, configparser_constructor):
        "Keyword argument for read_config works"
        parser = Mock()
        configparser_constructor.return_value = parser
        parsed = disco_config.read_config(config_file="Foobar")
        self.assertIs(parsed, parser)
        parser.read.assert_called_once_with("FAKE_CONFIG_DIR/Foobar")

    @patch('os.path.exists', Mock(return_value=True))
    def test__arglist__expected_behavior(self, configparser_constructor):
        "Unnamed argument list for read_config works"
        parser = Mock()
        configparser_constructor.return_value = parser
        parsed = disco_config.read_config("foo", "bar")
        self.assertIs(parsed, parser)
        parser.read.assert_called_once_with("FAKE_CONFIG_DIR/foo/bar")

    @patch('os.path.exists', Mock(return_value=True))
    def test__arg_combo__named_arg_last(self, configparser_constructor):
        "Combined keyword and listed args for read_config work"
        parser = Mock()
        configparser_constructor.return_value = parser
        parsed = disco_config.read_config("foo", "bar", config_file="baz.ini")
        self.assertIs(parsed, parser)
        parser.read.assert_called_once_with("FAKE_CONFIG_DIR/foo/bar/baz.ini")


@patch("disco_aws_automation.disco_config.ASIAQ_CONFIG", "FAKE_CONFIG_DIR")
class TestOpenNormalized(TestCase):
    """Tests for the open_normalized utility function."""

    @patch('os.path.exists', Mock(return_value=True))
    @patch('disco_aws_automation.disco_config.open')
    def test__path_exists__passthrough_successful(self, open_mock):
        "Valid path for open_normalized - 'open' called"
        expected = Mock()
        open_mock.return_value = expected
        found = disco_config.open_normalized("path", "to", "file", mode="moody")
        self.assertIs(expected, found)
        open_mock.assert_called_once_with("FAKE_CONFIG_DIR/path/to/file", mode="moody")


class TestAsiaqConfig(TestCase):
    """Tests for the AsiaqConfig object."""
    # allow long method names
    # pylint: disable=invalid-name
    BASE_CONFIG_DICT = {
        disco_config.DEFAULT_CONFIG_SECTION: {
            'default_environment': 'fake-build',
            'default_unused_option': 'fall-all-the-way-back',
        },
        'mhcfoobar': {
            'easy_option': 'easy_answer',
            'envy_option': 'fallback_answer',
            'envy_option@fake-build': 'default_env_answer',
            'envy_option@ci': 'ci_answer'
        }
    }

    S3_BUCKET_CONFIG = {
        's3_bucket_base': 'bucket-base',
        's3_bucket_suffix': 'blah',
        's3_bucket_suffix@production': 'danger'
    }

    def test__get_asiaq_option__no_env_options(self):
        "Option exists in desired section: found it"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals('easy_answer', config.get_asiaq_option(option='easy_option', section='mhcfoobar'))

    def test__get_asiaq_option__default_env(self):
        "Env-specific option with default environment"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals('default_env_answer',
                          config.get_asiaq_option(option='envy_option', section='mhcfoobar'))

    def test__get_asiaq_option__env_in_constructor(self):
        "Env-specific option with environment passed in at construction time"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT), environment='ci')
        self.assertEquals('ci_answer',
                          config.get_asiaq_option('envy_option', section='mhcfoobar'))

    def test__get_asiaq_option__env_in_call(self):
        "Env-specific option with environment passed in at call time"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals('ci_answer',
                          config.get_asiaq_option('envy_option', section='mhcfoobar', environment='ci'))

    def test__get_asiaq_option__bad_env_in_call(self):
        "Env-specific option with unused environment passed in at call time"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals('fallback_answer',
                          config.get_asiaq_option('envy_option', section='mhcfoobar', environment='nope'))

    def test__get_asiaq_option__default_section(self):
        "Option found in defaults as fallback"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals('fall-all-the-way-back', config.get_asiaq_option('unused_option'))
        self.assertEquals('fall-all-the-way-back',
                          config.get_asiaq_option('unused_option', section='mhcfoobar'))

    def test__get_asiaq_option__missing__exception(self):
        "Missing option with required=True"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertRaises(NoOptionError,
                          config.get_asiaq_option, 'nobody-cares-about-this', section='mhcfoobar')

    def test__get_asiaq_option__missing_not_required__default(self):
        "Missing option with required=False and default"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertEquals("passed-in-default",
                          config.get_asiaq_option('nobody-cares-about-this', section='mhcfoobar',
                                                  required=False, default="passed-in-default"))

    def test__get_asiaq_option__missing_not_required_no_default__none(self):
        "Missing option with required=False and default"
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertIsNone(config.get_asiaq_option('nobody-cares-about-this',
                                                  section='mhcfoobar', required=False))

    def test__get_asiaq_option__nonsense_args__error(self):
        "Invalid arguments to get_asiaq_option produce an error."
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertRaises(exceptions.ProgrammerError, config.get_asiaq_option, 'immaterial',
                          required=True, default=12345)

    def test__get_asiaq_s3_bucket_name__no_prefix__error(self):
        "Missing bucket prefix should make bucket-name method raise an exception."
        config = MockAsiaqConfig(deepcopy(self.BASE_CONFIG_DICT))
        self.assertRaises(NoOptionError, config.get_asiaq_s3_bucket_name, 'foobar')

    def test__get_asiaq_s3_bucket_name__no_suffix(self):
        "Missing suffix should not produce a problem for the bucket-name method."
        config_dict = deepcopy(self.BASE_CONFIG_DICT)
        config_dict[disco_config.DEFAULT_CONFIG_SECTION]['s3_bucket_base'] = 'bucket-base'
        config = MockAsiaqConfig(config_dict)
        self.assertEquals("bucket-base--foobar", config.get_asiaq_s3_bucket_name('foobar'))

    def test__get_asiaq_s3_bucket_name__defaults(self):
        "Base behavior of get_asiaq_s3_bucket_name works as expected."
        config_dict = deepcopy(self.BASE_CONFIG_DICT)
        config_dict[disco_config.DEFAULT_CONFIG_SECTION].update(self.S3_BUCKET_CONFIG)
        config = MockAsiaqConfig(config_dict)
        self.assertEquals("bucket-base--foobar--blah", config.get_asiaq_s3_bucket_name('foobar'))

    def test__get_asiaq_s3_bucket_name__real_env_specified(self):
        "Environment-specific behavior of get_asiaq_s3_bucket_name with a configured env works as expected"
        config_dict = deepcopy(self.BASE_CONFIG_DICT)
        config_dict[disco_config.DEFAULT_CONFIG_SECTION].update(self.S3_BUCKET_CONFIG)
        config = MockAsiaqConfig(config_dict, environment="production")
        self.assertEquals("bucket-base--foobar--danger", config.get_asiaq_s3_bucket_name('foobar'))

    def test__get_asiaq_s3_bucket_name__bad_env_specified(self):
        "Environment-specific behavior of get_asiaq_s3_bucket_name with a nonsense env works as expected"
        config_dict = deepcopy(self.BASE_CONFIG_DICT)
        config_dict[disco_config.DEFAULT_CONFIG_SECTION].update(self.S3_BUCKET_CONFIG)
        config = MockAsiaqConfig(config_dict, environment="nope")
        self.assertEquals("bucket-base--foobar--blah", config.get_asiaq_s3_bucket_name('foobar'))
