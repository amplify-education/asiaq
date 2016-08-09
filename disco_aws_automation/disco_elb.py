"""
Some code to manage elastic load balancers.
ELBs are load balancers that we can assign to auto scaling groups.
"""
import re
import logging
import time
import hashlib

import boto3
import botocore

from .disco_aws_util import get_tag_value
from .disco_route53 import DiscoRoute53
from .disco_acm import DiscoACM
from .disco_iam import DiscoIAM
from .exceptions import CommandError, TimeoutError
from .resource_helper import throttled_call
from .disco_aws_util import chunker


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

    def list_for_display(self):
        """Returns information about all of the ELBs in the current environment for display purposes"""
        # Grab all of the ELBs in this environment
        elbs_in_env = self.list()

        tag_descriptions = []

        # Grab their tags in chunks of 20
        for elbs in chunker([elb["LoadBalancerName"] for elb in elbs_in_env], 20):
            tag_descriptions += [tag_description for tag_description
                                 in throttled_call(self.elb_client.describe_tags,
                                                   LoadBalancerNames=elbs).get('TagDescriptions', [])]

        elb_infos = []

        # Populate our ELB info dicts
        for tag_description in tag_descriptions:
            elb_id = tag_description["LoadBalancerName"]
            # Try to figure out the name of the ELB from its tags
            hostclass = get_tag_value(tag_description["Tags"], "hostclass")
            environment = get_tag_value(tag_description["Tags"], "environment")
            testing = get_tag_value(tag_description["Tags"], "is_testing")
            if hostclass and environment and testing:
                elb_name = self.get_elb_name(environment, hostclass, testing)
            else:
                # Otherwise, look for the elb_name tag or just use the name of the load balancer
                elb_name = get_tag_value(tag_description["Tags"], "elb_name") or elb_id
            elb = [elb_in_env for elb_in_env in elbs_in_env
                   if elb_in_env["LoadBalancerName"] == elb_id][0]
            availability_zones = ','.join(elb["AvailabilityZones"])

            elb_infos.append({
                "elb_name": elb_name,
                "elb_id": elb_id,
                "availability_zones": availability_zones
            })

        return elb_infos

    def get_cname(self, hostclass, domain_name, testing=False):
        """Get the expected subdomain for an ELB for a hostclass"""
        if testing:
            hostclass += "-test"
        return hostclass + '-' + self.vpc.environment_name + '.' + domain_name

    def _setup_health_check(self, elb_id, health_check_url, instance_protocol, instance_port,
                            elb_name):
        if not health_check_url:
            logging.warning("No health check url configured for ELB %s", elb_name)
            if instance_protocol.upper() in ('HTTP', 'HTTPS'):
                health_check_url = '/'
            else:
                health_check_url = ''

        target = '{}:{}{}'.format(instance_protocol, instance_port, health_check_url)

        throttled_call(self.elb_client.configure_health_check,
                       LoadBalancerName=elb_id,
                       HealthCheck={
                           'Target': target,
                           'Interval': 5,
                           'Timeout': 4,
                           'UnhealthyThreshold': 2,
                           'HealthyThreshold': 2})

    def _setup_sticky_cookies(self, elb_id, elb_ports, sticky_app_cookie, elb_name):
        policies = throttled_call(self.elb_client.describe_load_balancer_policies,
                                  LoadBalancerName=elb_id)
        logging.debug("ELB policies found: %s", policies['PolicyDescriptions'])

        def _set_policies_for_elb_ports(policies):
            for elb_port in elb_ports:
                throttled_call(self.elb_client.set_load_balancer_policies_of_listener,
                               LoadBalancerName=elb_id,
                               LoadBalancerPort=int(elb_port),
                               PolicyNames=policies)

        if [desc for desc in policies['PolicyDescriptions'] if desc['PolicyName'] == STICKY_POLICY_NAME]:
            logging.warning("Deleting sticky session policy from ELB %s", elb_name)
            _set_policies_for_elb_ports([])
            throttled_call(self.elb_client.delete_load_balancer_policy,
                           LoadBalancerName=elb_id,
                           PolicyName=STICKY_POLICY_NAME)

        if sticky_app_cookie:
            policy_args = dict(LoadBalancerName=elb_id, PolicyName=STICKY_POLICY_NAME)
            if sticky_app_cookie in ('ELB', 'AWSELB'):
                logging.warning("Using ELB-generated sticky sessions for ELB %s", elb_name)
                policy_creator = self.elb_client.create_lb_cookie_stickiness_policy
            else:
                logging.warning("Using app-generated sticky sessions for ELB %s", elb_name)
                policy_args['CookieName'] = sticky_app_cookie
                policy_creator = self.elb_client.create_app_cookie_stickiness_policy
            throttled_call(policy_creator, **policy_args)
            # add sticky sessions policy to every listener
            _set_policies_for_elb_ports([STICKY_POLICY_NAME])

    # Pylint thinks this function has too many arguments
    # pylint: disable=R0913, R0914
    def get_or_create_elb(self, hostclass, security_groups, subnets, hosted_zone_name,
                          health_check_url, instance_protocol, instance_port,
                          elb_protocols, elb_ports, elb_public, sticky_app_cookie,
                          idle_timeout=None, connection_draining_timeout=None, testing=False):
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
            elb_protocols (str): Comma separated list of protocols to expose. HTTP, HTTPS, SSL or TCP
            elb_ports (str): Comma separated list of ports to expose from the load balancer
            elb_public (bool): True if the ELB should be internet routable
            sticky_app_cookie (str): The name of a cookie from your service to use for sticky sessions
            idle_timeout (int): time limit (in seconds) that ELB should wait before killing idle connections
            connection_draining_timeout (int): timeout limit (in seconds) that ELB should allow for open
                                               requests to resolve before removing EC2 instance from ELB
            testing (bool): True if the ELB will be used for testing purposes only.
        """
        cname = self.get_cname(hostclass, hosted_zone_name, testing=testing)
        elb_id = DiscoELB.get_elb_id(self.vpc.environment_name, hostclass, testing=testing)
        elb_name = DiscoELB.get_elb_name(self.vpc.environment_name, hostclass, testing=testing)
        elb = self.get_elb(hostclass, testing=testing)

        elb_protocols = str(elb_protocols).split(',')
        elb_ports = str(elb_ports).split(',')

        if not elb:
            logging.info("Creating ELB %s", elb_name)

            if len(elb_protocols) != len(elb_ports):
                raise CommandError('The number of ELB ports and protocols must match for ELB %s', elb_name)

            listeners = [{
                'Protocol': listener_info[0].strip(),
                'LoadBalancerPort': int(listener_info[1]),
                'InstanceProtocol': instance_protocol,
                'InstancePort': int(instance_port)
            } for listener_info in zip(elb_protocols, elb_ports)]

            for listener in listeners:
                # Only try to lookup a cert if we are using a secure protocol for the ELB
                if listener['Protocol'].upper() in ["HTTPS", "SSL"]:
                    listener['SSLCertificateId'] = self.get_certificate_arn(cname) or ''

            elb_args = {
                'LoadBalancerName': elb_id,
                'Listeners': listeners,
                'SecurityGroups': security_groups,
                'Subnets': subnets,
                'Tags': [
                    {'Key': 'hostclass', 'Value': hostclass},
                    {'Key': 'environment', 'Value': self.vpc.environment_name},
                    {'Key': 'is_testing', 'Value': '1' if testing else '0'}
                ]
            }

            if not elb_public:
                elb_args['Scheme'] = 'internal'

            throttled_call(self.elb_client.create_load_balancer, **elb_args)
            elb = self.get_elb(hostclass, testing=testing)

        # Make sure elb_id refers to the load balancer name that was returned/picked.
        elb_id = elb["LoadBalancerName"]

        self.route53.create_record(hosted_zone_name, cname, 'CNAME', elb['DNSName'])

        self._setup_health_check(elb_id, health_check_url, instance_protocol, instance_port, elb_name)
        self._setup_sticky_cookies(elb_id, elb_ports, sticky_app_cookie, elb_name)
        self._update_elb_attributes(elb_id, idle_timeout, connection_draining_timeout)

        return elb

    def _update_elb_attributes(self, elb_id, idle_timeout, connection_draining_timeout):
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
                           LoadBalancerName=elb_id,
                           LoadBalancerAttributes=updates)

    def get_elb(self, hostclass, testing=False):
        """
        Get an existing ELB without creating it.
        This method will try to lookup an ELB by both it's ID and its name.
        """
        # Use both names to make this backwards compatible with old-style ELB names.
        elb_id = DiscoELB.get_elb_id(self.vpc.environment_name, hostclass, testing=testing)
        elb_name = DiscoELB.get_elb_name(self.vpc.environment_name, hostclass, testing=testing)

        return self._get_elb(elb_id) or self._get_elb(elb_name)

    def _get_elb(self, load_balancer_name):
        """Get an ELB by its name"""
        try:
            load_balancers = throttled_call(
                self.elb_client.describe_load_balancers,
                LoadBalancerNames=[load_balancer_name]).get('LoadBalancerDescriptions', [])
            return load_balancers[0] if load_balancers else None
        except botocore.exceptions.ClientError:
            return None

    def delete_elb(self, hostclass, testing=False):
        """Delete an ELB if it exists"""
        elb = self.get_elb(hostclass, testing=testing)

        if not elb:
            logging.info("ELB for '%s' does not exist. Nothing to delete", hostclass)
            return

        logging.info("Deleting ELB %s", DiscoELB.get_elb_name(self.vpc.environment_name,
                                                              hostclass=hostclass,
                                                              testing=testing))

        # delete any CNAME records that point to the deleted ELB because they are no longer valid
        self.route53.delete_records_by_value('CNAME', elb['DNSName'])
        throttled_call(self.elb_client.delete_load_balancer, LoadBalancerName=elb['LoadBalancerName'])

    @staticmethod
    def get_elb_id(environment_name, hostclass, testing=False):
        """Returns the elb name for a given hostclasses, hashed with SHA-256 and truncated to 32 characters"""
        human_elb_name = DiscoELB.get_elb_name(environment_name, hostclass=hostclass,
                                               testing=testing)
        return hashlib.sha256(human_elb_name).hexdigest()[:32]

    @staticmethod
    def get_elb_name(environment_name, hostclass, testing=False):
        """Returns the elb name for a given hostclass"""
        name = environment_name + '-' + hostclass

        if testing:
            name += '-test'

        # load balancers can only have letters, numbers or dashes in their names so strip everything else
        elb_name = re.sub(r'[^a-zA-Z0-9-]', '', name)

        if len(elb_name) > 255:
            raise CommandError('ELB name ' + elb_name + " is over 255 characters")

        return elb_name

    def destroy_all_elbs(self):
        """Destroy all ELB for current environment"""
        for elb in self.list():
            self.route53.delete_records_by_value('CNAME', elb['DNSName'])
            throttled_call(self.elb_client.delete_load_balancer, LoadBalancerName=elb['LoadBalancerName'])

    def _describe_instance_health(self, elb_id, instance_ids=None):
        """
        Returns instance health state information about an ELB. Can be limited to return only particular
        instances.

        Params:
        elb_id        The name of the ELB to check.
        instance_ids    A list of instance_ids to report on. Defaults to all instances in the ELB.

        Returns a list of dictionaries in the format of:
        [
            {
                'InstanceId': 'string',
                'State': 'string',
                'ReasonCode': 'string',
                'Description': 'string'
            },
            ...
        ]
        See http://boto3.readthedocs.io/en/latest/reference/services/elb.html
        """
        if instance_ids:
            desired_instance_ids = [{"InstanceId": instance_id} for instance_id in instance_ids]
        else:
            desired_instance_ids = []
        instances = throttled_call(
            self.elb_client.describe_instance_health,
            LoadBalancerName=elb_id,
            Instances=desired_instance_ids)
        return instances["InstanceStates"]

    def wait_for_instance_health_state(self, hostclass, testing=False, instance_ids=None, state="InService",
                                       timeout=180):
        """
        Waits for instances attached to an ELB to enter a specific state. At least one instance must enter the
        specified state.

        Params:
        hostclass       The name of the hostclass whose ELB you are interested in.
        testing         True if the testing ELB should be used. Default: False.
        instance_ids    A list of instance_ids to filter to. Defaults to all instances in the ELB.
        state           The state to wait for the instances. Should be one of ['InService', 'OutOfService',
                        'Unknown']. Defaults to 'InService'.
        timeout         The number of seconds to wait for instances to reach that state. Default: 60.
        See http://boto3.readthedocs.io/en/latest/reference/services/elb.html
        """
        elb = self.get_elb(hostclass=hostclass, testing=testing)
        elb_id = elb["LoadBalancerName"]
        elb_name = DiscoELB.get_elb_name(self.vpc.environment_name, hostclass,
                                         testing=testing)
        stop_time = time.time() + timeout
        original_scope = scope = instance_ids if instance_ids else "all instances"
        while time.time() < stop_time:
            instances = self._describe_instance_health(elb_id=elb_id, instance_ids=instance_ids)
            if len(instances) >= 1 and all(instance["State"] == state for instance in instances):
                logging.info("Successfully waited for %s in ELB (%s) to enter state (%s)",
                             original_scope, elb_name, state)
                return
            # Update scope to be the instances that have not yet entered the desired state
            scope = [instance["InstanceId"] for instance in instances if instance["State"] != state]
            logging.info("Waiting for %s in ELB %s to enter state (%s)",
                         scope or original_scope, elb_name, state)
            time.sleep(5)
        raise TimeoutError(
            "Timed out after waiting {} seconds for {} in ELB ({}) to enter state ({})".format(timeout,
                                                                                               scope,
                                                                                               elb_name,
                                                                                               state))
