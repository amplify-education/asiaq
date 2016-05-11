"""
Some code to manage elastic load balancers.
ELBs are load balancers that we can assign to auto scaling groups.
"""
import re
import logging

import boto3
import botocore

from .disco_route53 import DiscoRoute53
from .disco_acm import DiscoACM
from .disco_iam import DiscoIAM
from .exceptions import CommandError
from .resource_helper import throttled_call


STICKY_POLICY_NAME = 'session-cookie-policy'


class DiscoELB(object):
    """
    A simple class to manage ELBs
    """

    def __init__(self, vpc, elb=None, route53=None, acm=None, iam=None):
        self.vpc = vpc
        self._elb_client = elb
        self.route53 = route53 or DiscoRoute53()
        self.acm = acm or DiscoACM()
        self.iam = iam or DiscoIAM()

    @property
    def elb_client(self):
        """
        Lazily creates boto3 ELB Connection
        """
        if not self._elb_client:
            self._elb_client = boto3.client('elb')
        return self._elb_client

    def get_certificate_arn(self, dns_name):
        """
        Returns a Certificate from ACM if available with fallback to the legacy IAM server certs

        If no certificate is found from either ACM or IAM, returns None.
        """
        try:
            return self.acm.get_certificate_arn(dns_name) or self.iam.get_certificate_arn(dns_name)
        except Exception:
            logging.info("Unable to find a SSL certificate for DNS entry %s", dns_name)
            return None

    def list(self):
        """Returns all of the ELBs for the current environment"""
        return [elb for elb in
                throttled_call(self.elb_client.describe_load_balancers).get('LoadBalancerDescriptions', [])
                if elb['VPCId'] == self.vpc.get_vpc_id()]

    def get_cname(self, hostclass, domain_name):
        """Get the expected subdomain for an ELB for a hostclass"""
        return hostclass + '-' + self.vpc.environment_name + '.' + domain_name

    def _setup_health_check(self, elb_name, health_check_url, instance_protocol, instance_port):
        if not health_check_url:
            logging.warning("No health check url configured for ELB %s", elb_name)
            if instance_protocol.upper() in ('HTTP', 'HTTPS'):
                health_check_url = '/'
            else:
                health_check_url = ''

        target = '{}:{}{}'.format(instance_protocol, instance_port, health_check_url)

        throttled_call(self.elb_client.configure_health_check,
                       LoadBalancerName=elb_name,
                       HealthCheck={
                           'Target': target,
                           'Interval': 5,
                           'Timeout': 4,
                           'UnhealthyThreshold': 2,
                           'HealthyThreshold': 2})

    def _setup_sticky_cookies(self, elb_name, elb_port, sticky_app_cookie):
        if sticky_app_cookie:
            logging.warning("Using sticky sessions for ELB %s", elb_name)
            throttled_call(self.elb_client.create_app_cookie_stickiness_policy,
                           LoadBalancerName=elb_name,
                           PolicyName=STICKY_POLICY_NAME,
                           CookieName=sticky_app_cookie)
            throttled_call(self.elb_client.set_load_balancer_policies_of_listener,
                           LoadBalancerName=elb_name,
                           LoadBalancerPort=elb_port,
                           PolicyNames=[STICKY_POLICY_NAME])
        # TBD Remove stickiness policy if it exists

    # Pylint thinks this function has too many arguments
    # pylint: disable=R0913, R0914
    def get_or_create_elb(self, hostclass, security_groups, subnets, hosted_zone_name,
                          health_check_url, instance_protocol, instance_port,
                          elb_protocol, elb_port, elb_public, sticky_app_cookie,
                          idle_timeout=None, connection_draining_timeout=None):
        """
        Returns an elb.
        This updates an existing elb if it exists, otherwise this creates a new elb.
        Creates a DNS record for the ELB using the hostclass and environment names

        Args:
            hostclass (str):
            security_groups (List[str]):
            subnets (List[str]): list of subnets where instances will be in
            hosted_zone_name (str): The name of the Hosted Zone(domain name) to create a subdomain for the ELB
            health_check_url (str): The heartbeat url to use if protocol is HTTP or HTTPS
            instance_protocol (str): HTTP, HTTPS, SSL or TCP
            instance_port (int): The port that services on instances are running on
            elb_protocol (str): HTTP, HTTPS, SSL or TCP
            elb_port (int): The port to expose from the load balancer
            elb_public (bool): True if the ELB should be internet routable
            sticky_app_cookie (str): The name of a cookie from your service to use for sticky sessions
            idle_timeout (int): time limit (in seconds) that ELB should wait before killing idle connections
            connection_draining_timeout (int): timeout limit (in seconds) that ELB should allow for open
                                               requests to resolve before removing EC2 instance from ELB
        """
        cname = self.get_cname(hostclass, hosted_zone_name)
        elb_name = DiscoELB.get_elb_name(self.vpc.environment_name, hostclass)
        elb = self.get_elb(hostclass)
        if not elb:
            logging.info("Creating ELB %s", elb_name)

            listener = {
                'Protocol': elb_protocol,
                'LoadBalancerPort': elb_port,
                'InstanceProtocol': instance_protocol,
                'InstancePort': instance_port
            }

            # Only try to lookup a cert if we are using a secure protocol for the ELB
            if elb_protocol.upper() in ["HTTPS", "SSL"]:
                listener['SSLCertificateId'] = self.get_certificate_arn(cname) or ''

            elb_args = {
                'LoadBalancerName': elb_name,
                'Listeners': [listener],
                'SecurityGroups': security_groups,
                'Subnets': subnets
            }

            if not elb_public:
                elb_args['Scheme'] = 'internal'

            throttled_call(self.elb_client.create_load_balancer, **elb_args)
            elb = self.get_elb(hostclass)

        self.route53.create_record(hosted_zone_name, cname, 'CNAME', elb['DNSName'])

        self._setup_health_check(elb_name, health_check_url, instance_protocol, instance_port)
        self._setup_sticky_cookies(elb_name, elb_port, sticky_app_cookie)
        self._update_elb_attributes(elb_name, idle_timeout, connection_draining_timeout)

        return elb

    def _update_elb_attributes(self, elb_name, idle_timeout, connection_draining_timeout):
        updates = {}
        if idle_timeout:
            updates['ConnectionSettings'] = {
                'IdleTimeout': idle_timeout
            }

        if connection_draining_timeout:
            updates['ConnectionDraining'] = {
                'Enabled': True,
                'Timeout': connection_draining_timeout
            }
        else:
            updates['ConnectionDraining'] = {
                'Enabled': False,
                'Timeout': 0
            }

        if updates:
            throttled_call(self.elb_client.modify_load_balancer_attributes,
                           LoadBalancerName=elb_name,
                           LoadBalancerAttributes=updates)

    def get_elb(self, hostclass):
        """Get an existing ELB without creating it"""
        name = DiscoELB.get_elb_name(self.vpc.environment_name, hostclass)

        try:
            load_balancers = throttled_call(self.elb_client.describe_load_balancers,
                                            LoadBalancerNames=[name]).get('LoadBalancerDescriptions', [])

            return load_balancers[0] if load_balancers else None
        except botocore.exceptions.ClientError:
            return None

    def delete_elb(self, hostclass):
        """Delete an ELB if it exists"""
        elb = self.get_elb(hostclass)

        if not elb:
            logging.info("ELB for '%s' does not exist. Nothing to delete", hostclass)
            return

        logging.info("Deleting ELB %s", elb['LoadBalancerName'])

        # delete any CNAME records that point to the deleted ELB because they are no longer valid
        self.route53.delete_records_by_value('CNAME', elb['DNSName'])
        throttled_call(self.elb_client.delete_load_balancer, LoadBalancerName=elb['LoadBalancerName'])

    @staticmethod
    def get_elb_name(environment_name, hostclass):
        """Returns the elb name for a given hostclass"""
        name = environment_name + '-' + hostclass

        # load balancers can only have letters, numbers or dashes in their names so strip everything else
        elb_name = re.sub(r'[^a-zA-Z0-9-]', '', name)

        if len(elb_name) > 32:
            raise CommandError('ELB name ' + elb_name + " is over 32 characters")

        return elb_name

    def destroy_all_elbs(self):
        """Destroy all ELB for current environment"""
        for elb in self.list():
            self.route53.delete_records_by_value('CNAME', elb['DNSName'])
            throttled_call(self.elb_client.delete_load_balancer, LoadBalancerName=elb['LoadBalancerName'])
