"""
Tests of disco_network_helper
"""
from unittest import TestCase

from netaddr import IPSet

from disco_aws_automation.network_helper import get_random_free_subnet, calc_subnet_offset


class DiscoNetworkHelperTests(TestCase):
    """Test network helper functions"""

    def test_get_random_free_subnet(self):
        """Test getting getting a random subnet from a network"""
        subnet = get_random_free_subnet('10.0.0.0/28', 30, [])

        possible_subnets = ['10.0.0.0/30', '10.0.0.4/30', '10.0.0.8/30', '10.0.0.12/30']
        self.assertIn(str(subnet), possible_subnets)

    def test_get_random_free_subnet_returns_none(self):
        """Test that None is returned if no subnets are available"""
        used_subnets = ['10.0.0.0/30', '10.0.0.4/32', '10.0.0.8/30', '10.0.0.12/30']

        subnet = get_random_free_subnet('10.0.0.0/28', 30, used_subnets)
        IPSet(subnet)
        self.assertIsNone(subnet)

    def test_calc_subnet_offset(self):
        """Test that subnet cidr sizes are calculated correctly"""
        self.assertEquals(1, calc_subnet_offset(2))
        self.assertEquals(2, calc_subnet_offset(3))
        self.assertEquals(2, calc_subnet_offset(4))
