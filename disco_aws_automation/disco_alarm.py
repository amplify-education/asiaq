'''Contains DiscoAlarm class for orchestrating CloudWatch alarms'''

import logging

from boto.ec2.cloudwatch import CloudWatchConnection

from .disco_sns import DiscoSNS
from .disco_alarm_config import DiscoAlarmConfig, DiscoAlarmsConfig
from .resource_helper import throttled_call

logger = logging.getLogger(__name__)

# Max batch size for alarm deletion http://goo.gl/vMQOrX
DELETE_BATCH_SIZE = 100
ENVIRONMENT_DELETE_SKIP_NAMESPACES = ['AWS/ES']


class DiscoAlarm(object):
    """
    Class orchestrating CloudWatch alarms
    """

    def __init__(self, environment, disco_sns=None, alarm_configs=None):
        self.cloudwatch = CloudWatchConnection()
        self.environment = environment
        self._disco_sns = disco_sns
        self._alarm_configs = alarm_configs

    @property
    def disco_sns(self):
        """
        Lazy sns connection
        """
        self._disco_sns = self._disco_sns or DiscoSNS()
        return self._disco_sns

    @property
    def alarm_configs(self):
        """Lazily creates alarm config object for the current environment"""
        if not self._alarm_configs:
            self._alarm_configs = DiscoAlarmsConfig(self.environment)
        return self._alarm_configs

    def _sns_topic(self, alarm):
        """
        Retrieve SNS topic corresponding to the alarm
        """
        return self.disco_sns.topic_arn_from_name(alarm.notification_topic)

    def _upsert_alarm(self, alarm):
        """
        Create an alarm, delete and re-create if it already exists
        """
        existing_alarms = self.cloudwatch.describe_alarms(alarm_names=[alarm.name])
        for existing_alarm in existing_alarms:
            throttled_call(
                existing_alarm.delete
            )
        throttled_call(
            self.cloudwatch.create_alarm,
            alarm
        )

    def create_alarms(self, hostclass, autoscaling_group_name=None):
        """
        Create alarms for a hostclass.

        Internally calls disco_alarms_config to create the alarm configuration objects.
        """
        alarms = self.alarm_configs.get_alarms(hostclass=hostclass,
                                               autoscaling_group_name=autoscaling_group_name)
        for alarm in alarms:
            self._upsert_alarm(
                alarm.to_metric_alarm(
                    self._sns_topic(alarm)
                )
            )

    def alarms(self):
        """
        Iterate alarms
        """
        next_token = None
        while True:
            alarms = throttled_call(
                self.cloudwatch.describe_alarms,
                next_token=next_token,
            )
            for alarm in alarms:
                yield alarm
            next_token = alarms.next_token
            if not next_token:
                break

    def get_alarms(self, desired=None):
        """
        Get all alarms for an environment filtered on the desired dictionary keys
        """
        desired = desired or {}
        keys = set(desired.keys())

        def _key_filter(dictionary, keys):
            return {key: value for key, value in dictionary.iteritems() if key in keys}

        return [alarm for alarm in self.alarms()
                if _key_filter(DiscoAlarmConfig.decode_alarm_name(alarm.name), keys) == desired]

    def _delete_alarms(self, alarms):
        alarm_names = [alarm.name for alarm in alarms]
        alarm_len = len(alarm_names)
        logger.debug("Deleting %s alarms.", alarm_len)
        for index in range(0, alarm_len, DELETE_BATCH_SIZE):
            throttled_call(
                self.cloudwatch.delete_alarms,
                alarm_names[index:min(index + DELETE_BATCH_SIZE, alarm_len)]
            )

    def delete_hostclass_environment_alarms(self, environment, hostclass):
        """
        Delete alarm in an environment by hostclass name
        """
        self._delete_alarms(self.get_alarms({"env": environment, "hostclass": hostclass}))

    def delete_environment_alarms(self, environment):
        """
        Delete all alarms for an environment
        """
        alarms = self.get_alarms({"env": environment})
        namespace_filtered_alarms = [
            alarm
            for alarm in alarms
            if alarm.namespace not in ENVIRONMENT_DELETE_SKIP_NAMESPACES
        ]
        self._delete_alarms(namespace_filtered_alarms)
