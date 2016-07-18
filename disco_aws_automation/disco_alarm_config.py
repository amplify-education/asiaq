"""
Parse disco_aws alarm configuration.
"""

import logging
import copy
from ConfigParser import ConfigParser

from boto.ec2.cloudwatch import MetricAlarm

from . import read_config, DiscoAutoscale
from .exceptions import AlarmConfigError
from .disco_aws_util import is_truthy
from .disco_elb import DiscoELB


NOTIFICATION_LEVELS = ["critical", "info"]
NOTIFICATION_SECTION_NAME = "notifications"
DEFAULT_SECTION_NAME = 'defaults'
DEFAULT_CONFIG_FILE = "disco_alarms.ini"


class DiscoNotification(object):
    """
    Representation of a notification type, which is implemented by an SNS topic on the AWS side
    """
    def __init__(self, name, endpoints):
        self.name = name
        self.endpoints = endpoints

    def __repr__(self):
        return "Notification:{0}:{1}".format(self.name, self.endpoints)


class DiscoAlarmConfig(object):
    """
    Representation of a single alarm, and all its configuration options.
    """

    # We take in a lot of options for configuring the alarms.
    # pylint: disable=too-many-instance-attributes
    def __init__(self, options, maximum=True):
        self.namespace = options["namespace"]
        self.metric_name = options["metric_name"]
        self.hostclass = options["hostclass"]
        self.environment = options["environment"]
        self.team = options["team"]

        self.duration = int(options["duration"])
        self.period = int(options["period"])
        self.statistic = options["statistic"]
        self.custom_metric = options["custom_metric"].lower() == "true"
        self.log_pattern_metric = is_truthy(options.get("log_pattern_metric", ""))

        if "autoscaling_group_name" in options:
            self.autoscaling_group_name = options["autoscaling_group_name"]
        else:
            self.autoscaling_group_name = None

        if "es_domain_name" in options:
            self.es_domain_name = options["es_domain_name"]
        else:
            self.es_domain_name = None

        if "es_client_id" in options:
            self.es_client_id = options["es_client_id"]
        else:
            self.es_client_id = None

        if options["level"] not in NOTIFICATION_LEVELS:
            raise AlarmConfigError(
                "Level for {0} must be one of {1}"
                .format(self.metric_name, NOTIFICATION_LEVELS)
            )
        self._level = options["level"]

        if maximum:
            logging.debug("threshold_max: %s", options["threshold_max"])
            self.threshold = int(options["threshold_max"])
            self.threshold_type = "max"
        else:
            logging.debug("threshold_min: %s", options["threshold_min"])
            self.threshold = int(options["threshold_min"])
            self.threshold_type = "min"

    @property
    def dimensions(self):
        """
        Alarm Metric Dimensions
        """

        # metrics created from logs do not have dimensions
        if self.log_pattern_metric:
            return {}

        # RDS dimensions
        # hostclass name is the DBInstanceIdentifier
        if self.namespace == 'AWS/RDS':
            key = "DBInstanceIdentifier"
            # Format is {vpc}-{DbInstanceId} Underscore is not allowed in RDS instance name
            value = "-".join([self.environment, self.hostclass])
            return {key: value}

        if self.namespace == 'AWS/ELB':
            return {'LoadBalancerName': DiscoELB.get_elb_id(self.environment, self.hostclass)}

        if self.namespace == 'AWS/ES':
            return {
                'DomainName': self.es_domain_name,
                'ClientId': self.es_client_id
            }

        if self.custom_metric:
            key = "env_hostclass"
            value = "_".join([self.environment, self.hostclass])
        else:
            key = "AutoScalingGroupName"
            value = self.autoscaling_group_name
        return {key: value}

    @property
    def notification_topic(self):
        """
        The name of the topic that the alarm should route to.
        """
        return "_".join([self.team, self.environment, self._level])

    @property
    def name(self):
        """
        Alarm name
        """
        return "_".join([self.team, self.environment, self.hostclass, self.metric_name, self.threshold_type])

    @staticmethod
    def decode_alarm_name(name):
        """
        Decodes an alarm name into a dictionary with team, env, hostclass, metric_name and
        threshold_type.

        Raises AlarmConfigError if unable to parse the alarm name.
        """
        parts = name.split("_")
        if len(parts) >= 5:
            # alarm names that have extra underscores
            # Assume this means the metric name portion has underscores
            # FIXME this won't work if the alarm is missing a team name AND the metric name has underscores
            # FIXME consider using different delimiters in the future
            return {
                "team": parts[0],
                "env": parts[1],
                "hostclass": parts[2],
                # some metric names have '_'. Assume that any extra '_' in the alarm name are the metric name
                "metric_name": '_'.join(parts[3:-1]),
                "threshold_type": parts[-1],
            }
        elif len(parts) == 4:
            # alarm names that don't contain a team name and a metric name without underscores
            return {
                "team": None,
                "env": parts[0],
                "hostclass": parts[1],
                "metric_name": parts[2],
                "threshold_type": parts[3],
            }
        raise AlarmConfigError("Failed to decode alarm name {0}".format(name))

    def __repr__(self):
        return "Alarm:{0}.{1}".format(self.name, self.threshold)

    def to_metric_alarm(self, policy_arn):
        """
        Returns a MetricAlarm for a given policy.
        """
        return MetricAlarm(
            alarm_actions=[policy_arn],
            ok_actions=[policy_arn],
            comparison='>' if self.threshold_type == "max" else "<",
            dimensions=self.dimensions,
            evaluation_periods=self.duration,
            metric=self.metric_name,
            name=self.name,
            namespace=self.namespace,
            period=self.period,
            statistic=self.statistic,
            threshold=self.threshold,
        )


class DiscoAlarmsConfig(object):
    """
    Represenation of all alarms in config file.
    """

    def __init__(self, environment, config_file=None, autoscale=None, elasticsearch=None):
        if config_file:
            self.config = ConfigParser()
            self.config.read(config_file)
        else:
            self.config = read_config(DEFAULT_CONFIG_FILE)
        self.environment = environment
        self.elasticsearch = elasticsearch or None  # laziliy intialized
        self._autoscale = autoscale or None  # laziliy intialized
        self._defaults = None

    @property
    def defaults(self):
        """
        get default options for whole disco_alarm_config
        """
        self._defaults = self._defaults or self.get_defaults()
        return self._defaults

    @property
    def autoscale(self):
        """
        returns a lazily created disco autoscaling object
        """
        if not self._autoscale:
            self._autoscale = DiscoAutoscale(environment_name=self.environment)
        return self._autoscale

    @staticmethod
    def _decode_section_name(section):
        """
        Extract special alarm parameters which are stored in section name
        """
        section_segments = section.split(".")
        # If a section has four or more segments, treat the middle segments as the metric name and
        # the ending segment as the hostclass. This is to support some Elasticsearch metrics like
        # 'ClusterStatus.red'
        if len(section_segments) >= 4:
            team = section_segments[0]
            namespace = section_segments[1]
            metric_name = '.'.join(section_segments[2:-1])
            section_hostclass = section_segments[-1]
        elif len(section_segments) == 3:
            team, namespace, metric_name = section_segments
            section_hostclass = None
        else:
            raise AlarmConfigError("Skipping non-alarm like config section {0}.".format(section))
        return team, namespace, metric_name, section_hostclass

    # Our alarms get a little complicated, so theres more than a few variables involved...
    # pylint: disable=too-many-locals
    def _get_alarm_specification_dict(self, team, namespace, metric_name, hostclass, hostclass_specific,
                                      autoscaling_group_name=None):
        """
        Return options for alarm, inheriting values from parent.
        """
        options = copy.copy(self.defaults)
        parent_section_name = ".".join([team, namespace, metric_name])
        child_section_name = ".".join([team, namespace, metric_name, hostclass])

        if hostclass_specific:
            if self.config.has_section(parent_section_name):
                parent_options = dict(self.config.items(parent_section_name))
                options.update(parent_options)
            child_options = dict(self.config.items(child_section_name))
            options.update(child_options)
        else:
            if self.config.has_section(child_section_name):
                # The alarm will be created with the more specific hostclass config
                return None
            parent_options = dict(self.config.items(parent_section_name))
            options.update(parent_options)

        options["team"] = team
        options["namespace"] = namespace
        options["metric_name"] = metric_name
        options["hostclass"] = hostclass
        options["environment"] = self.environment

        # If an autoscaling group name was provided, include it
        if autoscaling_group_name:
            options["autoscaling_group_name"] = autoscaling_group_name
        # Otherwise, if using the EC2 namespace, we should lookup the autoscaling group name of the hostclass
        # and use that.
        elif namespace == "AWS/EC2":
            group = self.autoscale.get_existing_group(hostclass=hostclass)
            options["autoscaling_group_name"] = group.name

        if namespace == "AWS/ES" and self.elasticsearch:
            domain_name = self.elasticsearch.get_domain_name(hostclass)
            options["es_domain_name"] = domain_name
            options["es_client_id"] = self.elasticsearch.get_client_id(domain_name)

        # metrics for log files have special environment specific namespaces and hostclass specific names
        if is_truthy(options.get("log_pattern_metric")):
            options["namespace"] = namespace + '/' + self.environment
            options['metric_name'] = hostclass + '-' + metric_name

        return options

    def get_alarms(self, hostclass, autoscaling_group_name=None):
        """
        Returns list of DiscoAlarm objects for all the alarms associated with a hostclass.
        """
        alarms = []
        for section in self.config.sections():
            try:
                team, namespace, metric_name, section_hostclass = DiscoAlarmsConfig._decode_section_name(
                    section
                )
            except AlarmConfigError:
                if section in [NOTIFICATION_SECTION_NAME, DEFAULT_SECTION_NAME]:
                    continue
                else:
                    raise
            if section_hostclass and section_hostclass != hostclass:
                # Hostclass specific section for different hostclass
                continue

            if namespace == "AWS/ES" and not self.elasticsearch:
                logging.info("Skipping creation of %s.%s.%s.%s because no Elasticsearch object was provided",
                             team, namespace, metric_name, section_hostclass)
                continue

            options = self._get_alarm_specification_dict(
                team, namespace, metric_name, hostclass, section_hostclass == hostclass,
                autoscaling_group_name=autoscaling_group_name
            )

            if not options:
                # No alarms options means we don't need to make the alarm
                continue

            for threshold in ["threshold_max", "threshold_min"]:
                if threshold in options:
                    if options[threshold].isdigit():
                        alarms.append(
                            DiscoAlarmConfig(options, maximum=threshold == "threshold_max")
                        )
                    else:
                        raise AlarmConfigError(
                            "Not a valid threshold value for {0}: {1}".format(threshold, options))
        return alarms

    def get_notifications(self):
        """
        Returns list of DiscoNotification objects for all notifications in the current environment
        for the current pagerduty group.
        """
        notifications = []
        options = dict(self.config.items(NOTIFICATION_SECTION_NAME))

        for notification_name, endpoint_str in options.iteritems():
            notification_parts = notification_name.split("_")
            if len(notification_parts) < 3:
                raise AlarmConfigError("Underscores must separate team name, "
                                       "env name and notification level: %s" % notification_name)
            level = notification_parts[-1]
            env = notification_parts[1]

            if env != self.environment:
                continue
            if level not in NOTIFICATION_LEVELS:
                raise AlarmConfigError("Unsupported notification level: %s" % level)

            endpoints = endpoint_str.split(",")
            notifications.append(DiscoNotification(notification_name, endpoints))

        return notifications

    def get_defaults(self):
        """
        return dictionary of default values that will be used
        """
        return dict(self.config.items(DEFAULT_SECTION_NAME)) or {}
