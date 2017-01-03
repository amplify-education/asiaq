"""
Uploads Machine metrics to AWS CloudWatch.
"""

import datetime
import logging
import re
from subprocess import check_output, CalledProcessError

import boto.utils
from boto.ec2 import cloudwatch

from disco_aws_automation.resource_helper import keep_trying

logger = logging.getLogger(__name__)


class DiscoMetrics(object):
    """Class for sending custom metrics to AWS CloudWatch"""

    class MetricData(object):
        """Container for metric data"""
        def __init__(self):
            self.when = datetime.datetime.utcnow()
            self.mem = {}
            self.cpu = {}
            self.disk = {}
            self.rabbit = {}

    def __init__(self, dummy=False):
        if dummy:
            self._region = 'us-west-2'
            self._hostclass = "mhccloudwatchtest"
            self._environment_name = "desktop"
        else:
            metadata = boto.utils.get_instance_metadata()
            self._region = metadata['placement']['availability-zone'][0:-1]
            userdata = self.get_userdata()
            self._hostclass = userdata["hostclass"]
            self._environment_name = userdata["environment_name"]

        self._connection = cloudwatch.connect_to_region(self._region)
        self._dimensions = {
            "env_hostclass": "_".join((self._environment_name, self._hostclass))
        }
        self._metrics = None

    @staticmethod
    def get_userdata():
        """
        Returns Disco user data as a dict.
        """
        userdata = boto.utils.get_instance_userdata()
        regex = re.compile(r"(\w+)=\"(.*?)\"")
        info = {regex.match(line).group(1): regex.match(line).group(2)
                for line in userdata.split("\n")
                if regex.match(line)}
        logger.debug("userdata %s", info)
        return info

    @staticmethod
    def get_meminfo():
        """
        Returns Linux /proc/meminfo in a dict.
        Raises RuntimeError if it fails to parse meminfo.
        """
        regex = re.compile(r"(\w+):\s+(\d+).*")
        with open('/proc/meminfo') as f:
            info = {regex.match(line).group(1): float(regex.match(line).group(2))
                    for line in f
                    if regex.match(line)}
            logger.debug("meminfo %s", info)
            if not info:
                raise RuntimeError("Unable to parse /proc/meminfo")
            return info

    @staticmethod
    def get_cpuinfo():
        """
        Returns iostat's cpu info in a map.
        The keys are user, nice, system, iowait, steal and idle.
        The values are floats representing percentages.
        Raises RuntimeError if it fails to parse iostat output.
        """
        output = check_output(['iostat', '-c'])
        regex = re.compile(
            r"\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+).*")
        for line in output.split('\n'):
            match = regex.match(line)
            if match:
                info = {
                    'user': float(match.group(1)),
                    'nice': float(match.group(2)),
                    'system': float(match.group(3)),
                    'iowait': float(match.group(4)),
                    'steal': float(match.group(5)),
                    'idle': float(match.group(6))
                }
                logger.debug("cpuinfo %s", info)
                return info
        raise RuntimeError("Unable to parse cpu info from iostat output")

    @staticmethod
    def get_rabbitmqinfo():
        """
        Returns all RabbitMQ queues and their sizes
        """
        queues = {}
        try:
            output = check_output(['sudo', 'rabbitmqctl', 'list_queues'])
            regex = re.compile(r"(\w+)\s+(\d+).*")
            for line in output.split('\n'):
                match = regex.match(line)
                if match:
                    queues[match.group(1)] = float(match.group(2))
        except CalledProcessError:
            raise RuntimeError("Unable to call rabbitmqctl")
        logger.debug("queues %s", queues)
        return queues

    @staticmethod
    def get_diskinfo():
        """
        Returns df disk utilization percentage info in a map.
        Raises RuntimeError if it fails to parse df output.
        """
        output = check_output(['df', '-P'])
        regex = re.compile(r"(\S+)\s+\d+\s+\d+\s+\d+\s+(\d+)%.*")
        info = {regex.match(line).group(1): regex.match(line).group(2)
                for line in output.split("\n")
                if regex.match(line)}
        logger.debug("diskinfo %s", info)
        return info

    def send_custom_metric(self, namespace, name, value, unit, when=None):
        """Sends a custom metric to AWS CloudWatch"""
        logger.debug("Sending %s %s %s", name, value, unit)
        keep_trying(15, self._connection.put_metric_data,
                    namespace=namespace, name=name, value=value, unit=unit,
                    dimensions=self._dimensions, timestamp=when)

    def collect(self):
        """
        Collects the subset of machine info that we care about.
        """
        self._metrics = DiscoMetrics.MetricData()

        try:
            self._metrics.mem = DiscoMetrics.get_meminfo()
        except (RuntimeError, CalledProcessError):
            logger.exception("Ignoring this exception in DiscoMetrics.collect()")

        try:
            self._metrics.cpu = DiscoMetrics.get_cpuinfo()
        except (RuntimeError, CalledProcessError):
            logger.exception("Ignoring this exception in DiscoMetrics.collect()")

        try:
            self._metrics.disk = DiscoMetrics.get_diskinfo()
        except (RuntimeError, CalledProcessError):
            logger.exception("Ignoring this exception in DiscoMetrics.collect()")

        try:
            self._metrics.rabbit = DiscoMetrics.get_rabbitmqinfo()
        except (RuntimeError, CalledProcessError):
            logger.exception("Ignoring this exception in DiscoMetrics.collect()")

    def upload(self):
        """
        Uploads the subset of machine info that we care about to CloudWatch.

        """
        metrics = self._metrics

        queue = {key: metrics.rabbit[key]
                 for key in ["inference_workflow", "ingestion_workflow"]
                 if metrics.rabbit.get(key, None) is not None}
        if queue:
            self.send_custom_metric(
                'RabbitMQ', queue.keys(), queue.values(), 'Count', metrics.when)

        mem = {key: self._metrics.mem[key] / 1024
               for key in ["MemTotal", "MemFree", "Buffers", "Cached"]
               if self._metrics.mem.get(key, None) is not None}
        if mem:
            self.send_custom_metric('EC2/Memory', mem.keys(), mem.values(), 'Megabytes', metrics.when)

        # linux use physical RAM for buffers and cached memory. it will free them as long as process request
        # it. So free memory should include MemFree, Buffers and Cached
        # see https://goo.gl/kAiYji
        if set(["MemFree", "MemTotal", "Buffers", "Cached"]) <= set(metrics.mem.keys()):
            self.send_custom_metric(
                'EC2/Memory', "%MemFree",
                100.0 * (metrics.mem["MemFree"] +
                         metrics.mem["Buffers"] +
                         metrics.mem["Cached"]) / metrics.mem["MemTotal"],
                'Percent', metrics.when)

        cpu = {key: metrics.cpu[key]
               for key in ["iowait", "steal", 'user', 'system', 'idle']
               if metrics.cpu.get(key, None) is not None}
        if cpu:
            self.send_custom_metric('EC2/CPU', cpu.keys(), cpu.values(), 'Percent', metrics.when)

        disk = {key: metrics.disk[key]
                for key in metrics.disk
                if re.match("^/dev/(sd|xvd|mapper).*", key)}
        if disk:
            self.send_custom_metric(
                'EC2/Disk', disk.keys(), disk.values(), 'Percent', metrics.when)
