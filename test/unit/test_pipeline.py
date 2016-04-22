"""
Tests of pipeline
"""
from unittest import TestCase


from disco_aws_automation.pipeline import Pipeline


class PipelineTests(TestCase):
    '''Test Pipeline class'''

    def test_size_as_rec_map_with_none(self):
        """_size_as_recurrence_map works with None"""
        pipeline = Pipeline({"some_key": None})
        self.assertEqual(pipeline._val_as_recurrence_map("some_key"), {None: None})

        pipeline = Pipeline({"some_key": ""})
        self.assertEqual(pipeline._val_as_recurrence_map("some_key"), {None: None})

    def test_size_as_rec_map_with_int(self):
        """_size_as_recurrence_map works with simple integer"""
        pipeline = Pipeline({"some_key": 5})
        self.assertEqual(pipeline._val_as_recurrence_map("some_key"),
                         {None: 5})

    def test_size_as_rec_map_with_map(self):
        """_size_as_recurrence_map works with a map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}

        pipeline = Pipeline({"some_key": map_as_string})
        self.assertEqual(pipeline._val_as_recurrence_map("some_key"), map_as_dict)

    def test_size_as_rec_map_with_duped_map(self):
        """_size_as_recurrence_map works with a duped map"""
        map_as_string = "2@1 0 * * *:3@6 0 * * *:3@6 0 * * *"
        map_as_dict = {"1 0 * * *": 2, "6 0 * * *": 3}

        pipeline = Pipeline({"some_key": map_as_string})
        self.assertEqual(pipeline._val_as_recurrence_map("some_key"), map_as_dict)