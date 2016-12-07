"""Tests for datapipeline wrapper class and manager."""

from unittest import TestCase

from moto import mock_datapipeline
from mock import Mock

from disco_aws_automation.disco_datapipeline import AsiaqDataPipeline, AsiaqDataPipelineManager
from disco_aws_automation import exceptions as asiaq_exceptions


class DataPipelineTest(TestCase):
    "Unit tests for the data pipeline wrapper class."
    # pylint: disable=invalid-name

    def test__description_only_object__content_and_persisted_false(self):
        "AsiaqDataPipeline construction with only required args"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        self.assertEquals(pipeline._name, "asdf")
        self.assertEquals(pipeline._description, "qwerty")
        self.assertFalse(pipeline._tags)
        self.assertFalse(pipeline.is_persisted())
        self.assertFalse(pipeline.has_content())

    def test__get_tag_dict__no_tags__no_return(self):
        "AsiaqDataPipeline.get_tag_dict with no tags"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        self.assertIsNone(pipeline.get_tag_dict())

    def test__get_tag_dict__tags_dict_passed__correct_return(self):
        "AsiaqDataPipeline.get_tag_dict with tags passed as dict"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", tags={'template': 'silly'})
        self.assertEquals({"template": "silly"}, pipeline.get_tag_dict())

    def test__get_tag_dict__tags_list_passed__correct_return(self):
        "AsiaqDataPipeline.get_tag_dict with tags passed as lsit"
        pipeline = AsiaqDataPipeline(
            name="asdf", description="qwerty",
            tags=[{'key': 'template', 'value': 'silly'}, {'key': 'another', 'value': 'tag'}])
        self.assertEquals({"template": "silly", "another": "tag"}, pipeline.get_tag_dict())

    def test__get_tag_dict__duplicate_tag__exception(self):
        "AsiaqDataPipeline.get_tag_dict with a duplicate tag definition"
        pipeline = AsiaqDataPipeline(
            name="asdf", description="qwerty",
            tags=[{'key': 'template', 'value': 'silly'}, {'key': 'template', 'value': 'conflict'}])
        self.assertRaises(Exception, pipeline.get_tag_dict)

    def test__get_tag_dict__malformed_tag__exception(self):
        "AsiaqDataPipeline.get_tag_dict with an invalid tag definition"
        pipeline = AsiaqDataPipeline(
            name="asdf", description="qwerty",
            tags=[{'key': 'template', 'stringValue': 'conflict'}])
        self.assertRaises(KeyError, pipeline.get_tag_dict)

    def test__get_param_value_dict__duplicate_value__exception(self):
        "AsiaqDataPipeline.get_param_value_dict with a duplicate value definition"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", param_values=[
            {'id': 'template', 'stringValue': 'silly'},
            {'id': 'template', 'stringValue': 'conflict'}
        ])
        self.assertRaises(Exception, pipeline.get_param_value_dict)

    def test__from_template__template_missing__exception(self):
        "AsiaqDataPipline.from_template with an invalid template"
        self.assertRaises(asiaq_exceptions.AsiaqConfigError, AsiaqDataPipeline.from_template,
                          name="asdf", description="qwerty", template_name="nope")

    def test__from_template__template_ok__reasonable(self):
        "AsiaqDataPipline.from_template with a valid template"
        pipeline = AsiaqDataPipeline.from_template(
            name="asdf", description="qwerty", template_name="dynamodb_backup")
        self.assertFalse(pipeline._tags)
        self.assertFalse(pipeline.is_persisted())
        self.assertTrue(pipeline.has_content())
        self.assertEquals(pipeline._name, "asdf")
        self.assertEquals(pipeline._description, "qwerty")

    def test__update_content__no_values__content_updated(self):
        "AsiaqDataPipline.update_content with no parameter values"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline_objects = Mock()
        param_defs = Mock()
        pipeline.update_content(pipeline_objects, param_defs)
        self.assertIs(pipeline._objects, pipeline_objects)
        self.assertIs(pipeline._params, param_defs)
        self.assertIsNone(pipeline._param_values)

    def test__update_content__dict_values__content_updated(self):
        "AsiaqDataPipline.update_content with silly dictionary parameter values"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline_objects = Mock()
        param_defs = Mock()
        param_values = {'foo': 'bar', 'baz': '1'}
        pipeline.update_content(pipeline_objects, param_defs, param_values)
        self.assertIs(pipeline._objects, pipeline_objects)
        self.assertIs(pipeline._params, param_defs)
        self.assertEquals(2, len(pipeline._param_values))
        self.assertIs(list, type(pipeline._param_values))
        self.assertIn({'id': 'foo', 'stringValue': 'bar'}, pipeline._param_values)
        self.assertIn({'id': 'baz', 'stringValue': '1'}, pipeline._param_values)
        self.assertEquals({"foo": "bar", "baz": "1"}, pipeline.get_param_value_dict())

    def test__update_content__list_values__content_updated(self):
        "AsiaqDataPipline.update_content with silly listed parameter values"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline_objects = Mock()
        param_defs = Mock()
        param_values = [
            {'id': 'foo', 'stringValue': 'bar'},
            {'id': 'bar', 'stringValue': 'baz'},
            {'id': 'simple', 'stringValue': 'string'}
        ]
        pipeline.update_content(pipeline_objects, param_defs, param_values)
        self.assertIs(pipeline._objects, pipeline_objects)
        self.assertIs(pipeline._params, param_defs)
        self.assertEquals(3, len(pipeline._param_values))
        self.assertEquals({"foo": "bar", "bar": "baz", "simple": "string"}, pipeline.get_param_value_dict())


@mock_datapipeline
class DataPipelineManagerTest(TestCase):
    "Tests for the pipeline management wrapper."

    def setUp(self):
        self.mgr = AsiaqDataPipelineManager()

    def test__construction__client_created(self):
        "AsiaqDataPipelineManager does the very basic things it is supposed to"
        self.assertNotNone(self.mgr._dp_client)
