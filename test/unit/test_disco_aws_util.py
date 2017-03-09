"""
Tests of disco_aws_util
"""
from unittest import TestCase
from datetime import datetime

import boto.ec2.instance
from mock import create_autospec

from disco_aws_automation.disco_aws_util import (
    get_instance_launch_time,
    size_as_recurrence_map,
    size_as_minimum_int_or_none,
    size_as_maximum_int_or_none
)


class DiscoAWSUtilTests(TestCase):
    '''Test disco_aws_util.py'''

    def test_size_as_rec_map_with_none(self):
        """size_as_recurrence_map works with None"""
        self.assertEqual(size_as_recurrence_map(None), {"": None})
        self.assertEqual(size_as_recurrence_map(''), {"": None})

    def test_size_as_rec_map_with_int(self):
        """size_as_recurrence_map works with simple integer"""
        self.assertEqual(size_as_recurrence_map(5, sentinel="0 0 * * *"),
                         {"0 0 * * *": 5})

    def test_size_as_rec_map_with_map(self):
        """size_as_recurrence_map works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}
        self.assertEqual(size_as_recurrence_map(map_as_string), map_as_dict)

    def test_size_as_rec_map_with_duped_map(self):
        """size_as_recurrence_map works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}
        self.assertEqual(size_as_recurrence_map(map_as_string), map_as_dict)

    def test_min_size_with_none(self):
        """size_as_minimum_int_or_none works with None """
        self.assertEqual(size_as_minimum_int_or_none(None), None)
        self.assertEqual(size_as_minimum_int_or_none(''), None)

    def test_min_size_with_int(self):
        """size_as_minimum_int_or_none works with simple integer"""
        self.assertEqual(size_as_minimum_int_or_none(5), 5)

    def test_min_size_with_map(self):
        """size_as_minimum_int_or_none works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        expected_size = 2
        self.assertEqual(size_as_minimum_int_or_none(map_as_string), expected_size)

    def test_min_size_with_duped_map(self):
        """size_as_minimum_int_or_none works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        expected_size = 2
        self.assertEqual(size_as_minimum_int_or_none(map_as_string), expected_size)

    def test_max_size_with_none(self):
        """size_as_maximum_int_or_none works with None """
        self.assertEqual(size_as_maximum_int_or_none(None), None)
        self.assertEqual(size_as_maximum_int_or_none(''), None)

    def test_max_size_with_int(self):
        """size_as_maximum_int_or_none works with simple integer"""
        self.assertEqual(size_as_maximum_int_or_none(5), 5)

    def test_max_size_with_map(self):
        """size_as_maximum_int_or_none works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        expected_size = 3
        self.assertEqual(size_as_maximum_int_or_none(map_as_string), expected_size)

    def test_max_size_with_duped_map(self):
        """size_as_maximum_int_or_none works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        expected_size = 3
        self.assertEqual(size_as_maximum_int_or_none(map_as_string), expected_size)

    def test_get_instance_launch_time(self):
        '''test get instance launch time'''
        now = datetime.utcnow()
        instance = create_autospec(boto.ec2.instance.Instance)
        instance.id = "i-123123aa"
        instance.launch_time = str(now)

        self.assertEquals(get_instance_launch_time(instance), now)
