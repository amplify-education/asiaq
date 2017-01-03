"""
Some code to manage cloudwatch logs
cloudwatch logs allow us to create metrics/alarms from log files
"""
import logging
from ConfigParser import ConfigParser

import boto3

from .disco_config import normalize_path
from .resource_helper import throttled_call

logger = logging.getLogger(__name__)


class DiscoLogMetrics(object):
    """
    A simple class to manage Log Metrics
    """

    def __init__(self, environment, config_file='disco_log_metrics.ini'):
        self.logs = boto3.client('logs')
        self.environment = environment
        self.config_file = config_file
        self._config = None  # lazily initialized

    @property
    def config(self):
        """
        Lazily reads config file.
        Returns None if config file is missing
        """
        if not self._config:
            try:
                config = ConfigParser()
                config.read(normalize_path(self.config_file))
                self._config = config
            except Exception:
                return None
        return self._config

    def list_metric_filters(self, hostclass):
        """List log metrics for a hostclass"""
        return sorted(list(self._get_metric_filters_generator(hostclass)),
                      key=lambda metric: (metric['filterName']))

    def _get_metric_filters_generator(self, hostclass):
        for log_group in self.list_log_groups(hostclass):
            for metric in self._get_metrics_for_log_group(log_group['logGroupName']):
                yield metric

    def update(self, hostclass):
        """Recreate log metrics for a hostclass from config"""
        if not self.config:
            logger.warning('DiscoLogMetrics config file is missing. Cannot update hostclass %s', hostclass)
            return

        self.delete_metrics(hostclass)

        existing_log_group_names = [log_group['logGroupName']
                                    for log_group in self.list_log_groups(hostclass)]

        hostclass_sections = [section for section in self.config.sections()
                              if section.startswith(hostclass + ".")]
        for section in hostclass_sections:
            metric_name = section.split('.')[1]

            log_group_name = self._get_log_group_name(hostclass, self.config.get(section, 'log_file'))
            metric_name = self._get_metric_name(hostclass, metric_name)

            # create the log group if it doesn't exist
            if log_group_name not in existing_log_group_names:
                throttled_call(self.logs.create_log_group, logGroupName=log_group_name)

            logger.info("Creating metric filter %s", metric_name)
            throttled_call(self.logs.put_metric_filter,
                           logGroupName=log_group_name,
                           filterName=metric_name,
                           filterPattern=self.config.get(section, 'filter_pattern'),
                           metricTransformations=[
                               {
                                   'metricName': metric_name,
                                   'metricNamespace': self._get_metric_namespace(),
                                   'metricValue': self.config.get(section, 'metric_value')
                               }
                           ])

    def delete_metrics(self, hostclass):
        """Delete log metrics for a hostclass"""
        for log_group in self.list_log_groups(hostclass):
            for metric in self._get_metrics_for_log_group(log_group['logGroupName']):
                logger.info("Deleting metric filter %s", metric['filterName'])
                throttled_call(self.logs.delete_metric_filter,
                               logGroupName=log_group['logGroupName'],
                               filterName=metric['filterName'])

    def list_log_groups(self, hostclass):
        """List log groups for a hostclass"""
        response = throttled_call(self.logs.describe_log_groups,
                                  logGroupNamePrefix=self.environment + "/" + hostclass)

        return sorted(response.get('logGroups', []), key=lambda group: group['logGroupName'])

    def delete_all_metrics(self):
        """Delete all metric filters in the current environment"""
        response = throttled_call(self.logs.describe_log_groups, logGroupNamePrefix=self.environment + "/")

        for log_group in response.get('logGroups', []):
            for metric in self._get_metrics_for_log_group(log_group['logGroupName']):
                throttled_call(self.logs.delete_metric_filter,
                               logGroupName=log_group['logGroupName'],
                               filterName=metric['filterName'])

    def delete_log_groups(self, hostclass):
        """Delete all log groups in the current environment"""
        for log_group in self.list_log_groups(hostclass):
            throttled_call(self.logs.delete_log_group, logGroupName=log_group['logGroupName'])

    def delete_all_log_groups(self):
        """Delete all log groups in the current environment"""
        response = throttled_call(self.logs.describe_log_groups, logGroupNamePrefix=self.environment + "/")

        for log_group in response.get('logGroups', []):
            throttled_call(self.logs.delete_log_group, logGroupName=log_group['logGroupName'])

    def _get_log_group_name(self, hostclass, log_file):
        return self.environment + "/" + hostclass + log_file

    def _get_metrics_for_log_group(self, log_group_name):
        response = throttled_call(self.logs.describe_metric_filters, logGroupName=log_group_name)
        return response.get('metricFilters', [])

    def _get_metric_namespace(self):
        return 'LogMetrics/' + self.environment

    def _get_metric_name(self, hostclass, metric_name):
        return hostclass + '-' + metric_name
