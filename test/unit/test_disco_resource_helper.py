"""
Tests of disco_aws
"""
from unittest import TestCase

from disco_aws_automation import resource_helper
from disco_aws_automation import exceptions


class DiscoResourceHelperTests(TestCase):
    '''Test ResourceHelper class'''

    def test_check_written_s3_0(self):
        """Check raise exception when length doesn't match"""
        with self.assertRaises(exceptions.S3WritingError):
            resource_helper.check_written_s3("test", 1024, 0)

    def test_check_written_s3_1(self):
        """Check raise exception when length does match"""
        resource_helper.check_written_s3("test", 1024, 1024)

    def test_key_values_to_tags(self):
        """Test the conversion of key:value to tag dict"""
        mykeyvalues = ["productline:astronauts", "mytag:tag:value"]
        expected_tag_dict = [{'Value': 'astronauts', 'Key': 'productline'},
                             {'Value': 'tag:value', 'Key': 'mytag'}]
        returned_tags = resource_helper.key_values_to_tags(mykeyvalues)
        self.assertEqual(returned_tags, expected_tag_dict)
