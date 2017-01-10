#!/usr/bin/env python
"""
Asiaq SSH utility. Starts an ssh session to a host by automatically determining whether to use its
private ip, public ip, or to tunnel through a jump host.

Usage:
    disco_ssh.py [options] <host>

Variables:
     <host>                 A hostname/hostclass/instance substring

Options:
     -h --help              Show this screen
     --debug                Log in debug level
     --env ENV              Environment to operate in
     --first                In case of multiple matching instances, connect to the first instead of failing
"""

import logging
import os
import re
import socket

from docopt import docopt

from disco_aws_automation import DiscoAWS
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_aws_util import run_gracefully, EasyExit
from disco_aws_automation.disco_logging import configure_logging

logger = logging.getLogger(__name__)

SSH_OPTIONS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=7"


class DiscoSSH(object):
    """Utility class for ssh-ing into AWS hosts"""
    _instances = None  # lazily initialized
    _aws = None  # lazily initialized

    def __init__(self, args):
        self.args = args
        self.config = read_config()
        self.env = self.args["--env"] or self.config.get("disco_aws", "default_environment")
        self.pick_instance = self.args['--first']
        configure_logging(args["--debug"])

    def is_ip(self, string):
        """Returns True if the given string is an IPv4 address"""
        ip4_regex = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
        return ip4_regex.match(string) is not None

    def aws(self):
        """Lazily creates a DiscoAWS object"""
        if not self._aws:
            self._aws = DiscoAWS(self.config, self.env)
        return self._aws

    def instances(self):
        """Lazily fetches all instances in the environment"""
        if not self._instances:
            logger.info("Fetching info about instances in %s", self.env)
            self._instances = self.aws().instances()
        return self._instances

    def match_instance(self, host_string):
        """
        Returns the instance that matches the given host string, or None.
        Matches on hostname or hostclass substring.
        Raises ValueError if more than one match.
        """
        matched_instances = []
        names = []
        hostclasses = set()
        for i in self.instances():
            if host_string in i.tags.get("hostclass", "") or host_string in i.tags.get("hostname", ""):
                matched_instances.append(i)
                names.append(i.tags.get("hostname") or i.id)
                hostclasses.add(i.tags.get("hostclass", "MISSING_HOSTCLASS"))

        if not matched_instances:
            return None
        elif len(matched_instances) == 1:
            return matched_instances[0]
        elif self.pick_instance:
            if len(hostclasses) != 1:
                raise EasyExit("Matched instances from multiple hostclasses: %s" % ", ".join(hostclasses))
            logger.info("Matched instances %s: connecting to %s", ", ".join(names), names[0])
            return matched_instances[0]
        else:
            raise EasyExit("Too many instances matched: %s" % ", ".join(names))

    def is_reachable(self, ip_address):
        """Returns True if we can connect to port 22 at the given ip address"""
        if not ip_address:
            return False
        logger.info("Probing %s", ip_address)
        try:
            sock = socket.create_connection((ip_address, 22), timeout=2)
            sock.close()
            return True
        except socket.timeout:
            return False

    def detect_best_route(self, host):
        """
        Detects the best way to ssh into an instance. Returns a list of ip addresses where
        we should tunnel through the first n-1 hosts to reach the n-th host
        """
        if self.is_ip(host):
            return [host]  # if user gave an ip, we assume they explicitly want to go there without jumping

        instance = self.match_instance(host)
        if not instance:
            raise EasyExit("No instances in the {} environment matched '{}'".format(self.env, host))

        logger.info("Detecting best route to %s", instance.tags.get("hostname"))

        if self.is_reachable(instance.ip_address):  # ip_address is actually the public ip address (or None)
            return [instance.ip_address]

        for interface in instance.interfaces:
            if self.is_reachable(interface.private_ip_address):
                return [interface.private_ip_address]

        logger.info("No direct route. Trying jump host.")
        jump_host_ip = self.aws().find_jump_address()
        if not jump_host_ip:
            raise EasyExit("No direct route to host and no jump host in {}".format(self.env))

        return [jump_host_ip, instance.private_ip_address]

    def build_ssh_cmd(self, ips):
        """
        Given a list of ip addresses, build an ssh command to tunnel through n-1 ips to reach the n-th ip
        """
        command = " ".join([
            "ssh -At {} {}".format(SSH_OPTIONS, ip)
            for ip in ips])
        return command

    def run(self):
        """Parses command line and dispatches the commands"""
        host = self.args["<host>"]

        ips = self.detect_best_route(host)
        cmd = self.build_ssh_cmd(ips)
        logger.info("Now ssh-ing: %s", cmd)
        os.system(cmd)

if __name__ == "__main__":
    disco_ssh = DiscoSSH(docopt(__doc__))
    run_gracefully(disco_ssh.run)
