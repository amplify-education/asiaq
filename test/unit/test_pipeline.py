"""
Tests of pipeline
"""
from unittest import TestCase


from disco_aws_automation.pipeline import Pipeline


class PipelineTests(TestCase):
    '''Test Pipeline class'''

    def test_size_as_rec_map_with_none(self):
        """_size_as_recurrence_map works with None"""
        self.assertEqual(Pipeline._size_as_recurrence_map(None), {None: None})
        self.assertEqual(Pipeline._size_as_recurrence_map(""), {None: None})

    def test_size_as_rec_map_with_int(self):
        """_size_as_recurrence_map works with simple integer"""
        self.assertEqual(Pipeline._size_as_recurrence_map(5),
                         {None: 5})

    def test_size_as_rec_map_with_map(self):
        """_size_as_recurrence_map works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}

        self.assertEqual(Pipeline._size_as_recurrence_map(map_as_string), map_as_dict)

    def test_size_as_rec_map_with_duped_map(self):
        """_size_as_recurrence_map works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}

        self.assertEqual(Pipeline._size_as_recurrence_map(map_as_string), map_as_dict)