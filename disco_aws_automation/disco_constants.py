'''Various useful constants'''

DEFAULT_CONFIG_SECTION = "disco_aws"
HOSTCLASS_PREFIX = "mhc"
DEFAULT_INSTANCE_TYPE = "m3.large"
SMOKETEST_POLL_INTERVAL = 15  # seconds
# we shouldn't have to go higher than this; instead do a shorter interval and terminate/reprovision on timeout
SMOKETEST_TIMEOUT = 600
AUTOSCALE_POLL_INTERVAL = 15  # seconds
AUTOSCALE_TIMEOUT = 300
DEPLOYMENT_STRATEGY_BLUE_GREEN = "blue_green"
DEPLOYMENT_STRATEGY_CLASSIC = "classic"

YES_LIST = ['true', 'yes', 't', 'y', 'aye', '1']
NO_LIST = ['false', 'no', 'f', 'n', 'nay', '0']
CREDENTIAL_BUCKET_TEMPLATE = "{region}.{project}.credentials.{postfix}"

NETWORKS = {"intranet": "Inter host",
            "dmz": "Client facing",
            "tunnel": "internet http proxy",
            "maintenance": "Admin jump box"}

VPC_CONFIG_FILE = "disco_vpc.ini"
