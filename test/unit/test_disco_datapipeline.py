"""Tests for datapipeline wrapper class and manager."""

from unittest import TestCase

import boto3
from mock import Mock, MagicMock

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
        "AsiaqDataPipeline.get_tag_dict with tags passed as list"
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
        # nasty cherry-pick:
        self.assertEquals("DailySchedule", pipeline._objects[0]['id'])
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

    def test__update_content__template__content_updated(self):
        "AsiaqDataPipline.update_content with a template"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline.update_content(template_name="dynamodb_restore")
        self.assertTrue(pipeline.has_content())
        self.assertEquals("DDBDestinationTable", pipeline._objects[1]['id'])

    def test__update_content__bad_args__error(self):
        "AsiaqDataPipline.update_content with bad argument combinations fails"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        self.assertRaises(asiaq_exceptions.ProgrammerError, pipeline.update_content)
        self.assertRaises(asiaq_exceptions.ProgrammerError, pipeline.update_content,
                          template_name="something", contents="something else")


class DataPipelineManagerTest(TestCase):
    "Tests for the pipeline management wrapper."

    # pylint: disable=invalid-name,missing-docstring
    SEARCH_DESCRIPTIONS = [
        {'name': 'pipeline1', 'description': 'pipeline with no tags', 'pipelineId': 'p1'},
        {'name': 'nodescpipeline', 'pipelineId': 'nodesc',
         'tags': [{'key': 'environment', 'value': 'build'}, {'key': 'extraneous', 'value': 'tag'}]},
        {'name': 'mypipeline', 'pipelineId': 'buildit', 'description': 'pipeline in build',
         'tags': [{'key': 'environment', 'value': 'build'}, {'key': 'extraneous', 'value': 'tag'}]},
        {'name': 'mypipeline', 'description': 'pipeline in ci',
         'pipelineId': 'ciya', 'tags': [{'key': 'environment', 'value': 'ci'}]},
    ]

    def _persisted_pipeline(self, contents=None):
        "Return a pipeline with a set AWS ID, so that it apppears to be 'saved' to AWS already."
        return AsiaqDataPipeline("test", "pipeline with id", pipeline_id="asdf", contents=contents)

    def _unpersisted_pipeline(self, contents=None):
        "Return a pipeline with no AWS ID."
        return AsiaqDataPipeline("test", "pipeline with no id", contents=contents)

    def setUp(self):
        self.mock_client = MagicMock(spec=boto3.client("datapipeline"))
        self.mock_client.list_pipelines.return_value = {
            'hasMoreResults': False,
            'pipelineIdList': [{'id': item} for item in ['abcd', 'qwerty', '12345']]
        }
        self.mock_client.describe_pipelines.return_value = {
            'pipelineDescriptionList': self.SEARCH_DESCRIPTIONS
        }
        self.mgr = AsiaqDataPipelineManager(self.mock_client)

    def test__construction__client_created(self):
        "AsiaqDataPipelineManager constructor does the very basic things it is supposed to"
        manager = AsiaqDataPipelineManager()
        self.assertIsNotNone(manager._dp_client)

    def test__fetch__no_params_object__ok(self):
        objects = Mock()
        self.mock_client.get_pipeline_definition.return_value = {'pipelineObjects': objects}
        fetched = self.mgr.fetch("ab-cdef")
        self.assertEquals(objects, fetched._objects)
        self.assertIsNone(fetched._param_values)
        self.assertIsNone(fetched._params)
        self.mock_client.get_pipeline_definition.assert_called_once_with(pipelineId="ab-cdef",
                                                                         version="latest")
        self.mock_client.describe_pipelines.assert_called_once_with(pipelineIds=['ab-cdef'])

    def test__fetch__full_content_object__ok(self):
        objects = Mock()
        params = Mock()
        values = Mock()
        self.mock_client.get_pipeline_definition.return_value = {
            'pipelineObjects': objects, 'parameterObjects': params, 'parameterValues': values}
        fetched = self.mgr.fetch("ab-cdef")
        self.assertEquals(objects, fetched._objects)
        self.assertEquals(values, fetched._param_values)
        self.assertEquals(params, fetched._params)
        self.mock_client.get_pipeline_definition.assert_called_once_with(pipelineId="ab-cdef",
                                                                         version="latest")
        self.mock_client.describe_pipelines.assert_called_once_with(pipelineIds=['ab-cdef'])

    def test__fetch_content__already_fetched_error(self):
        pipeline = self._persisted_pipeline(contents=Mock())
        self.assertRaises(Exception, self.mgr.fetch_content, pipeline)

    def test__fetch_content__not_saved_error(self):
        pipeline = self._unpersisted_pipeline()
        self.assertRaises(Exception, self.mgr.fetch_content, pipeline)

    def test__fetch_content__common_case__ok(self):
        pipeline = self._persisted_pipeline()
        objects = Mock()
        params = Mock()
        values = Mock()
        self.mock_client.get_pipeline_definition.return_value = {
            'pipelineObjects': objects, 'parameterObjects': params, 'parameterValues': values}
        self.mgr.fetch_content(pipeline)
        self.mock_client.get_pipeline_definition.assert_called_once_with(pipelineId="asdf", version="latest")
        self.assertEquals(objects, pipeline._objects)
        self.assertEquals(values, pipeline._param_values)
        self.assertEquals(params, pipeline._params)

    def test__delete__unsaved__error(self):
        pipeline = self._unpersisted_pipeline()
        self.assertRaises(Exception, self.mgr.delete, pipeline)

    def test__delete__common_case__ok(self):
        pipeline = self._persisted_pipeline()
        self.mgr.delete(pipeline)
        self.mock_client.delete_pipeline.assert_called_once_with(pipelineId='asdf')

    def test__save__update_pipeline__only_content_updated(self):
        contents = Mock()
        pipeline = self._persisted_pipeline(contents)
        self.mgr.save(pipeline)
        self.mock_client.create_pipeline.assert_not_called()
        self.mock_client.put_pipeline_definition.assert_called_once_with(
            pipelineId="asdf", pipelineObjects=contents, parameterObjects=[], parameterValues=[])

    def test__save__new_pipeline__meta_and_content_updated(self):
        self.mock_client.create_pipeline.return_value = {'pipelineId': 'qwerty'}
        contents = Mock()
        pipeline = self._unpersisted_pipeline(contents)
        self.mgr.save(pipeline)
        self.assertEquals(pipeline._id, 'qwerty')
        self.assertEquals(self.mock_client.create_pipeline.call_count, 1)  # args are too much of a pain
        self.mock_client.put_pipeline_definition.assert_called_once_with(
            pipelineId="qwerty", pipelineObjects=contents, parameterObjects=[], parameterValues=[])
        self.assertEquals('test', self.mock_client.create_pipeline.call_args[1]['name'])
        self.assertEquals('pipeline with no id', self.mock_client.create_pipeline.call_args[1]['description'])
        self.assertEquals([], self.mock_client.create_pipeline.call_args[1]['tags'])

    def test__search_descriptions__no_ids__no_results(self):
        self.mock_client.list_pipelines.return_value = {'hasMoreResults': False, 'pipelineIdList': []}
        self.mock_client.describe_pipelines.return_value = {'pipelineDescriptionList': []}
        self.assertEquals([], self.mgr.search_descriptions())
        self.mock_client.list_pipelines.assert_called_once_with()
        self.mock_client.describe_pipelines.assert_not_called()

    def test__search_descriptions__search_all__all_found(self):
        searched = self.mgr.search_descriptions()
        self.assertEquals(4, len(searched))
        for i in range(4):
            self.assertEquals(searched[i]._name, self.SEARCH_DESCRIPTIONS[i]['name'])
            self.assertEquals(searched[i]._id, self.SEARCH_DESCRIPTIONS[i]['pipelineId'])

        # hand-assert special cases
        self.assertIsNone(searched[0]._tags, msg="Missing tags handled correctly")
        self.assertIsNone(searched[1]._description, msg="Missing description handled correctly")
        self.assertEquals({'environment': 'build', 'extraneous': 'tag'}, searched[2].get_tag_dict())

    def test__search_descriptions__by_bad_name__nothing_found(self):
        self.assertEquals([], self.mgr.search_descriptions(name="asdfasdfasdfasd"))

    def test__search_descriptions__by_good_name__pipeline_found(self):
        searched = self.mgr.search_descriptions(name="nodescpipeline")
        self.assertEquals(1, len(searched))
        self.assertEquals("nodescpipeline", searched[0]._name)
        self.assertIsNone(searched[0]._description)

    def test__search_descriptions__by_dupe_name__pipelines_found(self):
        searched = self.mgr.search_descriptions(name="mypipeline")
        self.assertEquals(2, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("buildit", searched[0]._id)
        self.assertEquals("ciya", searched[1]._id)

    def test__search_descriptions__by_bad_tag__nothing_found(self):
        searched = self.mgr.search_descriptions(tags={'foo': 'bar'})
        self.assertEquals([], searched)

    def test__search_descriptions__by_good_tag__pipeline_found(self):
        searched = self.mgr.search_descriptions(tags={'environment': 'ci'})
        self.assertEquals(1, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("pipeline in ci", searched[0]._description)

    def test__search_descriptions__by_dupe_name_and_tag__pipelines_found(self):
        searched = self.mgr.search_descriptions(name="mypipeline", tags={'environment': 'build'})
        self.assertEquals(1, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("buildit", searched[0]._id)

    def test__start__unpersisted__error(self):
        self.assertRaises(Exception, self.mgr.start, self._unpersisted_pipeline())

    def test__start__persisted_without_params__started(self):
        self.mgr.start(self._persisted_pipeline())
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertIsNone(activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__start__persisted_with_param_values__started_with_param_values(self):
        pipeline = self._persisted_pipeline()
        pipeline._param_values = Mock()
        self.mgr.start(pipeline)
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertEquals(pipeline._param_values, activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__start__param_values_list__started_with_correct_param_values(self):
        pipeline = self._persisted_pipeline()
        pipeline._param_values = Mock()  # this is the WRONG set of params
        real_params = [Mock(), Mock()]
        self.mgr.start(pipeline, params=real_params)
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertEquals(real_params, activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__start__param_values_dict__started_with_correct_param_values(self):
        pipeline = self._persisted_pipeline()
        pipeline._param_values = Mock()  # this is the WRONG set of params
        real_params = {"foo": "bar", "qwerty": "asdf"}
        self.mgr.start(pipeline, params=real_params)
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertIn({'id': 'foo', 'stringValue': 'bar'}, activate_args['parameterValues'])
        self.assertIn({'id': 'qwerty', 'stringValue': 'asdf'}, activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__stop__unpersisted__error(self):
        self.assertRaises(Exception, self.mgr.stop, self._unpersisted_pipeline())

    def test__stop__persisted__stopped(self):
        self.mgr.stop(self._persisted_pipeline())
        self.mock_client.deactivate_pipeline.assert_called_once_with(pipelineId="asdf")
