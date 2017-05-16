"""
Container for disco_aws_automation exceptions
"""


class TimeoutError(RuntimeError):
    """Error raised on timeout"""
    pass


class ExpectedTimeoutError(TimeoutError):
    """
    Error raised in situations where we decide to terminate
    the keep-trying loop early because we have learned that
    the chance of success is 0%
    """
    pass


class EarlyExitException(Exception):
    """
    Special no-op Exception class for non-error early exits from the program.

    Contrast with EasyExit, which is intended to terminate with a non-zero status (if the @graceful
    decoration is used).
    """
    pass


class EasyExit(Exception):
    """
    Raise this exception to exit your program with a log message and a non-zero status, but no stack trace
    (assuming you are running it with run_gracefully).

    Contrast with EarlyExitException, which is intended to short-circuit out with a non-error status.
    """
    pass


class ProgrammerError(Exception):
    "An exception state that resulted from a coding error, not an environment error."
    pass


class AsiaqConfigError(EasyExit):
    "An exception that results from a configuration problem (file or value not found; no stack trace needed)."
    pass


class AccountError(Exception):
    """ Account manipulation error """
    pass


class CommandError(RuntimeError):
    """ Error running SSH command """
    pass


class IntegrationTestError(RuntimeError):
    """ Error running integration tests """
    pass


class VPCEnvironmentError(RuntimeError):
    """ Error relating to accessing environment """
    pass


class SmokeTestError(RuntimeError):
    """ Error while performing smoketest """
    pass


class AMIError(RuntimeError):
    """ Amazon Machine Image Error """
    pass


class VolumeError(RuntimeError):
    """S3 Volume Error"""
    pass


class InstanceMetadataError(RuntimeError):
    """Instance Metadata Error"""
    pass


class IPRangeError(RuntimeError):
    """IP not in rage error"""
    pass


class WrongPathError(RuntimeError):
    """Not executed in the right path"""
    pass


class S3WritingError(RuntimeError):
    """S3 object is not written correctly"""
    pass


class MissingAppAuthError(RuntimeError):
    """Application Authorization files is not found"""
    pass


class AppAuthKeyNotFoundError(RuntimeError):
    """Application Authorization Key is not found"""
    pass


class VPCPeeringSyntaxError(RuntimeError):
    """VPC Peering syntax is incorrect"""
    pass


class VPCConfigError(RuntimeError):
    """VPC config is incorrect"""
    pass


class MultipleVPCsForVPCNameError(RuntimeError):
    """Found multiple VPCs with the same name"""
    pass


class VPCNameNotFound(RuntimeError):
    """Can't find VPC by the name"""
    pass


class DynamoDBEnvironmentError(RuntimeError):
    """DynamoDB Generic Error"""
    pass


class DataPipelineException(Exception):
    """Some error to do with data pipelines"""
    pass


class DataPipelineFormatException(DataPipelineException):
    """An error in data pipeline data formatting."""
    pass


class DataPipelineStateException(DataPipelineException):
    """An illegal or unexpected state was detected (either locally or on the server)."""
    pass


class AlarmConfigError(RuntimeError):
    """Error in Alarm Configuration"""
    pass


class RDSEnvironmentError(RuntimeError):
    """RDS Generic Error"""
    pass


class EIPConfigError(RuntimeError):
    """Error in Elastic IP Configuration"""
    pass


class RouteCreationError(RuntimeError):
    """Error trying to create a route"""
    pass


class TooManyAutoscalingGroups(RuntimeError):
    """Error trying to create more than the expected number of autoscaling groups"""
    pass


class SpotinstException(Exception):
    """Generic Spotinst exception"""
    pass


class UnknownDeploymentStrategyException(Exception):
    """Error trying to use an unknown deployment strategy"""
    pass


class SpotinstApiException(Exception):
    """Raised if Spotinst API problem encountered"""


class SpotinstRateExceededException(Exception):
    """Raised if Spotinst API throttled a request"""
