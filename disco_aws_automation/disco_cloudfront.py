import botocore
import boto3
import random
from ConfigParser import ConfigParser
import uuid

from . import normalize_path

DEFAULT_CONFIG_FILE_CLOUDFRONT = "disco_cloudfront.ini"

class DiscoCloudfront(object):
    """ Class to create Cloudfront distribution  """

    def __init__(self, vpc, config_file=DEFAULT_CONFIG_FILE_CLOUDFRONT):
        self.vpc = vpc
        self.client = boto3.client('cloudfront')
        self.config_file = config_file
        self._config = None
        
    @property
    def config(self):
        """lazy load config"""
        if not self._config:
            try:
                config = ConfigParser()
                config.read(normalize_path(self.config_file))
                self._config = config
            except Exception:
                return None
        return self._config

    def create(self, origin_path):

        config = ConfigParser()
        config.read(normalize_path(self.config_file))
        self._config = config

        caller_reference = str(uuid.uuid4())
        origin_path = '/' + origin_path
        full_origin_path = self._get_option("s3_bucket_name") + origin_path
        origin_access_identity = 'origin-access-identity/cloudfront/' + self._get_option("origin_access_identity")

        response = self.client.create_distribution(DistributionConfig={
                'CallerReference': caller_reference,
                'Aliases': {
                    'Quantity': 0,
                },
                'DefaultRootObject': '',
                'Origins': {
                    'Quantity': 1,
                    'Items': [
                        {
                            'Id': full_origin_path,
                            'DomainName': self._get_option("s3_bucket_name"),
                            'OriginPath': origin_path,
                            'CustomHeaders': {
                                'Quantity': 0,
                            },
                            'S3OriginConfig': {
                                'OriginAccessIdentity': origin_access_identity
                            },
                        },
                    ]
                },
                'DefaultCacheBehavior': {
                    'TargetOriginId': full_origin_path,
                    'ForwardedValues': {
                        'QueryString': False,
                        'Cookies': {
                            'Forward': 'none',
                            'WhitelistedNames': {
                                'Quantity': 0,
                            }
                        },
                        'Headers': {
                            'Quantity': 0,
                        }
                    },
                    'TrustedSigners': {
                        'Enabled': False,
                        'Quantity': 0,
                    },
                    'ViewerProtocolPolicy': 'allow-all',
                    'MinTTL': 86400,
                    'AllowedMethods': {
                        'Quantity': 2,
                        'Items': [
                        'HEAD',
                        'GET',           
                        ],
                        'CachedMethods': {
                            'Quantity': 2,
                            'Items': [
                                'HEAD',
                                'GET'
                            ]
                        }
                    },
                    'SmoothStreaming': False,
                    'DefaultTTL': 86400,
                    'Compress': True
                },
                'CacheBehaviors': {
                    'Quantity': 1,
                    'Items': [
                        {
                            'PathPattern': '*',
                            'TargetOriginId': full_origin_path,
                            'ForwardedValues': {
                                'QueryString': True,
                                'Cookies': {
                                    'Forward': 'none',
                                    'WhitelistedNames': {
                                        'Quantity': 0,
                                    }
                                },
                                'Headers': {
                                    'Quantity': 0,
                                }
                            },
                            'TrustedSigners': {
                                'Enabled': False,
                                'Quantity': 0,
                            },
                            'ViewerProtocolPolicy': 'allow-all',
                            'MinTTL': 86400,
                            'AllowedMethods': {
                                'Quantity': 2,
                                'Items': [
                                    'HEAD',
                                    'GET',
                                ],
                                'CachedMethods': {
                                    'Quantity': 2,
                                    'Items': [
                                        'HEAD',
                                        'GET',
                                    ]
                                }
                            },
                            'SmoothStreaming': False,
                            'DefaultTTL': 86400,
                            'MaxTTL': 86400,
                            'Compress': True
                        },
                    ]
                },
                'CustomErrorResponses': {
                    'Quantity': 1,
                    'Items': [
                        {
                            'ErrorCode': 404,
                            'ResponsePagePath': '/Error.html',
                            'ResponseCode': '200',
                            'ErrorCachingMinTTL': 86400
                        },
                    ]
                },
                'Comment': 'Cloudfront Distribution',
                'Logging': {
                    'Enabled': True,
                    'IncludeCookies': True,
                    'Bucket': self._get_option("log_bucket_name"),
                    'Prefix': 'log'
                },
                'PriceClass': 'PriceClass_100',
                'Enabled': True,
                'ViewerCertificate': {
                    'CloudFrontDefaultCertificate': True,
                    'MinimumProtocolVersion': 'TLSv1',
                },
            })

    def _get_option(self, option_name, project_name='training_pages'):
        """Get a config option for a cluster"""
        if not self.config:
            raise CommandError('Cloudfront config file missing')

        section_name = self.vpc.environment_name + ':' + project_name

        if not self.config.has_section(section_name):
            raise CommandError('%s section missing in Cloudfront config' % section_name)

        if self.config.has_option(section_name, option_name):
            return self.config.get(section_name, option_name)
        else:
            return None
        return None

