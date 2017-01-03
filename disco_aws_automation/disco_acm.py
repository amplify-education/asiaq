"""
Some code to manage the Amazon Certificate Service.
"""
import logging

import boto3
import botocore
from .resource_helper import throttled_call

logger = logging.getLogger(__name__)

CERT_SUMMARY_LIST_KEY = 'CertificateSummaryList'
CERT_ARN_KEY = 'CertificateArn'
CERT_ALT_NAMES_KEY = 'SubjectAlternativeNames'
DOMAIN_NAME_KEY = 'DomainName'
CERT_KEY = 'Certificate'


class DiscoACM(object):
    """
    A class to manage the Amazon Certificate Service

    """
    WILDCARD_PREFIX = "*."

    def __init__(self, connection=None):
        self._acm = connection

    def _in_domain(self, domain, dns_name):
        """
        Returns whether the host is in the ACM certificate domain
        Only supports top level domain wildcard matching
        e.g. *.blah.com will match, but not *.*.blah.com
        It would be good to use a standard library here if becomes available.
        """
        if not (domain and dns_name):
            return False

        # sanity check left-most label
        name, subdomain = dns_name.split('.', 1)
        if not name or name == '*':
            logger.error('Left-most label "%s" of "%s" is invalid', name, dns_name)
            return False

        # exact match
        if dns_name == domain:
            return True

        # handle wildcard cert domains
        if domain.startswith(self.WILDCARD_PREFIX):
            domain = domain[len(self.WILDCARD_PREFIX):]
        return subdomain == domain

    @property
    def acm(self):
        """
        Lazily creates ACM connection

        Return None if service does not exist in current region
        """
        if not self._acm:
            try:
                self._acm = boto3.client('acm')
            except Exception:
                logger.warning("ACM service does not exist in current region")
                return None
        return self._acm

    def get_certificate_arn(self, dns_name):
        """Returns a Certificate ARN from the Amazon Certificate Service given the DNS name"""
        if not self.acm:
            return None

        try:
            cert_summary = throttled_call(self.acm.list_certificates)[CERT_SUMMARY_LIST_KEY]

            cert_matches = []
            for cert in cert_summary:
                response = throttled_call(self.acm.describe_certificate, CertificateArn=cert[CERT_ARN_KEY])
                cert = response[CERT_KEY]
                # search for the dns_name in the cert's alternative domain names (includes the main one)
                for alt_name in cert[CERT_ALT_NAMES_KEY]:
                    if self._in_domain(alt_name, dns_name):
                        # a tuple of the matching cert domain name and the actual cert
                        cert_matches.append((alt_name, cert))

            # sort the matches by name, longest first
            cert_matches.sort(key=lambda cert: len(cert[0]), reverse=True)

            if not cert_matches:
                logger.warning("No ACM certificates returned for %s", dns_name)
                return None

            # pick the cert for the longest matched domain name
            return cert_matches[0][1][CERT_ARN_KEY]
        except (botocore.exceptions.EndpointConnectionError,
                botocore.vendored.requests.exceptions.ConnectionError):
            # some versions of botocore(1.3.26) will try to connect to acm even if outside us-east-1
            logger.exception("Unable to get ACM certificate")
            return None
