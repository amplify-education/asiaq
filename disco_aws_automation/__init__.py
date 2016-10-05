''' For package documentation, see README '''

from os import getenv
from os.path import join, exists
from ConfigParser import ConfigParser


ASIAQ_CONFIG = getenv("ASIAQ_CONFIG", ".")
DEFAULT_CONFIG_FILE = "disco_aws.ini"


def read_config(config_file=DEFAULT_CONFIG_FILE):
    """
    Normalize and read in a config file (defaulting to "disco_aws.ini").
    """
    real_config_file = normalize_path(config_file)
    config = ConfigParser()
    config.read(real_config_file)
    return config


def normalize_path(path):
    """
    If ASIAQ_CONFIG is set prepend it to path, otherwise just return path.
    """
    normalized_path = join(ASIAQ_CONFIG, path)
    if exists(normalized_path):
        return normalized_path
    else:
        raise RuntimeError("Config path not found: %s" % normalized_path)


# The following imports are at the bottom to avoid a circular import when importing read_config
# pylint: disable=wrong-import-position
from .disco_acm import DiscoACM
from .disco_autoscale import DiscoAutoscale
from .disco_aws import DiscoAWS
from .disco_bake import DiscoBake
from .disco_creds import DiscoS3Bucket
from .disco_dynamodb import DiscoDynamoDB
from .disco_accounts import S3AccountBackend
from .disco_iam import DiscoIAM
from .disco_eip import DiscoEIP
from .disco_elb import DiscoELB
from .disco_route53 import DiscoRoute53
from .disco_elasticache import DiscoElastiCache
from .disco_vpc import DiscoVPC
from .disco_vpc_peerings import DiscoVPCPeerings
from .disco_vpc_endpoints import DiscoVPCEndpoints
from .hostclass_templating import HostclassTemplating
from .disco_alarm import DiscoAlarm
from .disco_alarm_config import DiscoAlarmsConfig, DiscoAlarmConfig
from .disco_metrics import DiscoMetrics
from .disco_app_auth import DiscoAppAuth
from .disco_deploy import DiscoDeploy
from .disco_sns import DiscoSNS
from .disco_ssm import DiscoSSM
from .disco_chaos import DiscoChaos
from .disco_storage import DiscoStorage
from .disco_log_metrics import DiscoLogMetrics
from .disco_elasticsearch import DiscoElasticsearch
from .disco_elasticsearch_archive import DiscoESArchive
from .exceptions import TimeoutError, ExpectedTimeoutError, AccountError, CommandError, VPCEnvironmentError
from .exceptions import SmokeTestError, AMIError, VolumeError, InstanceMetadataError, S3WritingError
from .exceptions import MissingAppAuthError, AppAuthKeyNotFoundError, VPCConfigError, VPCPeeringSyntaxError
from .exceptions import MultipleVPCsForVPCNameError, VPCNameNotFound, AlarmConfigError
from .version import __version__, __rpm_version__, __git_hash__
