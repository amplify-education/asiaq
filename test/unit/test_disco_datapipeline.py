"""Tests for datapipeline wrapper class and manager."""

import copy
from unittest import TestCase

import boto3
from mock import Mock, MagicMock, patch

from disco_aws_automation.disco_datapipeline import (
    AsiaqDataPipeline, AsiaqDataPipelineManager, template_to_boto, add_default_object_fields)
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
        self.assertRaises(asiaq_exceptions.DataPipelineFormatException, pipeline.get_tag_dict)

    def test__get_tag_dict__malformed_tag__exception(self):
        "AsiaqDataPipeline.get_tag_dict with an invalid tag definition"
        pipeline = AsiaqDataPipeline(
            name="asdf", description="qwerty",
            tags=[{'key': 'template', 'stringValue': 'conflict'}])
        self.assertRaises(asiaq_exceptions.DataPipelineFormatException, pipeline.get_tag_dict)

    def test__last_run__no_metadata__state_exception(self):
        "AsiaqDataPipeline.last_run fails appropriately when no metadata is set"
        pipeline = AsiaqDataPipeline("TEST", "TESTY")
        with self.assertRaises(asiaq_exceptions.DataPipelineStateException):
            _ = pipeline.last_run

    def test__last_run__no_field__none_returned(self):
        "AsiaqDataPipeline.last_run is None when metadata does not include last-run"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[{'key': '@foo', 'stringValue': 'bar'}])
        self.assertIsNone(pipeline.last_run)

    def test__last_run__valid_date__datetime_returned(self):
        "AsiaqDataPipeline.last_run is a correct datetime"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[
            {'key': '@latestRunTime', 'stringValue': '1978-08-05T08:00:00'}])
        self.assertEquals(1978, pipeline.last_run.year)
        self.assertEquals(8, pipeline.last_run.month)
        self.assertEquals(5, pipeline.last_run.day)
        self.assertEquals(8, pipeline.last_run.hour)
        self.assertEquals(0, pipeline.last_run.utcoffset().total_seconds())

    def test__health__no_field__none_returned(self):
        "AsiaqDataPipeline.health is none if the field is absent"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[{'key': '@foo', 'stringValue': 'bar'}])
        self.assertIsNone(pipeline.health)

    def test__health__field_set__value_found(self):
        "AsiaqDataPipeline.health is found if set"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[
            {'key': '@healthStatus', 'stringValue': 'SUPERHEALTHY'}])
        self.assertEquals('SUPERHEALTHY', pipeline.health)

    def test__pipeline_state__field_absent__exception(self):
        "AsiaqDataPipeline.pipeline_state causes an exception if not set"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[{'key': '@foo', 'stringValue': 'bar'}])
        with self.assertRaises(KeyError):
            _ = pipeline.pipeline_state

    def test__pipeline_state__field_set__value_found(self):
        "AsiaqDataPipeline.pipeline_state is found if set"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[
            {'key': '@pipelineState', 'stringValue': 'NIFTY'}])
        self.assertEquals('NIFTY', pipeline.pipeline_state)

    def test__create_date__field_absent__exception(self):
        "AsiaqDataPipeline.create_date causes an exception if not set"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[{'key': '@foo', 'stringValue': 'bar'}])
        with self.assertRaises(KeyError):
            _ = pipeline.create_date

    def test__create_date__field_set__datetime_found(self):
        "AsiaqDataPipeline.create_date is a correct datetime"
        pipeline = AsiaqDataPipeline("TEST", "TESTY", metadata=[
            {'key': '@creationTime', 'stringValue': '2008-01-20T17:00:00'}])
        self.assertEquals(2008, pipeline.create_date.year)
        self.assertEquals(1, pipeline.create_date.month)
        self.assertEquals(20, pipeline.create_date.day)
        self.assertEquals(17, pipeline.create_date.hour)
        self.assertEquals(0, pipeline.create_date.utcoffset().total_seconds())

    def test__get_param_value_dict__duplicate_value__exception(self):
        "AsiaqDataPipeline.get_param_value_dict with a duplicate value definition"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", param_values=[
            {'id': 'template', 'stringValue': 'silly'},
            {'id': 'template', 'stringValue': 'conflict'}
        ])
        self.assertRaises(asiaq_exceptions.DataPipelineFormatException, pipeline.get_param_value_dict)

    def test__from_template__template_missing__exception(self):
        "AsiaqDataPipeline.from_template with an invalid template"
        self.assertRaises(asiaq_exceptions.AsiaqConfigError, AsiaqDataPipeline.from_template,
                          name="asdf", description="qwerty", template_name="nope")

    def test__from_template__template_ok__reasonable(self):
        "AsiaqDataPipeline.from_template with a valid template"
        pipeline = AsiaqDataPipeline.from_template(
            name="asdf", description="qwerty", template_name="dynamodb_backup")
        self.assertFalse(pipeline._tags)
        self.assertFalse(pipeline.is_persisted())
        self.assertTrue(pipeline.has_content())
        # nasty cherry-pick:
        self.assertEquals("DailySchedule", pipeline._objects[0]['id'])
        self.assertEquals(pipeline._name, "asdf")
        self.assertEquals(pipeline._description, "qwerty")

    def test__from_template__log_and_subnet_fields__fields_set(self):
        "AsiaqDataPipeline.from_template with a log location and subnet ID"
        pipeline = AsiaqDataPipeline.from_template(
            name="asdf", description="qwerty", template_name="dynamodb_backup",
            log_location="FAKEY", subnet_id="McFAKEFAKE")
        self.assertFalse(pipeline._tags)
        self.assertFalse(pipeline.is_persisted())
        self.assertTrue(pipeline.has_content())

        def _find_default(objects):
            for obj in objects:
                if obj['id'] == 'Default':
                    return obj

        default_object = _find_default(pipeline._objects)
        self.assertIn({'key': 'pipelineLogUri', 'stringValue': 'FAKEY'}, default_object['fields'])
        self.assertIn({'key': 'subnetId', 'stringValue': 'McFAKEFAKE'}, default_object['fields'])

    def test__update_content__no_values__content_updated(self):
        "AsiaqDataPipeline.update_content with no parameter values"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline_objects = Mock()
        param_defs = Mock()
        pipeline.update_content(pipeline_objects, param_defs)
        self.assertIs(pipeline._objects, pipeline_objects)
        self.assertIs(pipeline._params, param_defs)
        self.assertIsNone(pipeline._param_values)

    def test__update_content__new_and_old_values__values_updated(self):
        "AsiaqDataPipeline.update_content overwrites parameter values when appropriate"
        orig_values = {'this': 'will', 'be': 'overwritten'}
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", param_values=orig_values)
        new_values = {'foo': 'bar', 'baz': '1'}
        pipeline_objects = Mock()
        param_defs = Mock()
        pipeline.update_content(pipeline_objects, param_defs, new_values)
        self.assertEquals([{'id': 'foo', 'stringValue': 'bar'}, {'id': 'baz', 'stringValue': '1'}],
                          pipeline._param_values)

    def test__update_content__old_values_not_new_ones__values_unchanged(self):
        "AsiaqDataPipeline.update_content does not overwrite parameter values when not appropriate"
        orig_values = {'this': 'will not', 'be': 'overwritten'}
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", param_values=orig_values)
        pipeline_objects = Mock()
        param_defs = Mock()
        pipeline.update_content(pipeline_objects, param_defs)
        self.assertEquals(
            [{'id': 'this', 'stringValue': 'will not'}, {'id': 'be', 'stringValue': 'overwritten'}],
            pipeline._param_values
        )

    def test__update_content__old_values_new_empty__values_cleared(self):
        "AsiaqDataPipeline.update_content does not overwrite parameter values when not appropriate"
        orig_values = {'this': 'will', 'be': 'overwritten'}
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty", param_values=orig_values)
        pipeline_objects = Mock()
        param_defs = Mock()
        pipeline.update_content(pipeline_objects, param_defs, [])
        self.assertEquals(
            [],
            pipeline._param_values
        )

    def test__update_content__dict_values__content_updated(self):
        "AsiaqDataPipeline.update_content with silly dictionary parameter values"
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
        "AsiaqDataPipeline.update_content with silly listed parameter values"
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
        "AsiaqDataPipeline.update_content with a template"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        pipeline.update_content(template_name="dynamodb_restore")
        self.assertTrue(pipeline.has_content())
        self.assertEquals("DDBDestinationTable", pipeline._objects[1]['id'])

    def test__update_content__log_location_and_subnet__fields_set(self):
        "AsiaqDataPipeline.update_content with log location and subnet ID"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        new_contents = [
            {'id': 'Default', 'fields': [{'key': 'uninteresting', 'stringValue': 'thing'}], 'name': 'short'},
            {'id': 'Other', 'fields': [], 'name': 'unused'}
        ]
        pipeline.update_content(log_location='FAKEFAKE', subnet_id='EVENFAKER', contents=new_contents)
        self.assertIn({'key': 'pipelineLogUri', 'stringValue': 'FAKEFAKE'}, pipeline._objects[0]['fields'])
        self.assertIn({'key': 'subnetId', 'stringValue': 'EVENFAKER'}, pipeline._objects[0]['fields'])

    def test__update_content__bad_args__error(self):
        "AsiaqDataPipeline.update_content with bad argument combinations fails"
        pipeline = AsiaqDataPipeline(name="asdf", description="qwerty")
        self.assertRaises(asiaq_exceptions.ProgrammerError, pipeline.update_content)
        self.assertRaises(asiaq_exceptions.ProgrammerError, pipeline.update_content,
                          template_name="something", contents="something else")


class DataPipelineManagerTest(TestCase):
    "Tests for the pipeline management wrapper."

    # pylint: disable=invalid-name
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
        "AsiaqDataPipelineManager.fetch behaves as expected for param-less pipeline"
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
        "AsiaqDataPipelineManager.fetch behaves as expected for fully-defined pipeline"
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
        "AsiaqDataPipelineManager.fetch_content on an already-populated object: error"
        pipeline = self._persisted_pipeline(contents=Mock())
        self.assertRaises(asiaq_exceptions.DataPipelineStateException, self.mgr.fetch_content, pipeline)

    def test__fetch_content__not_saved_error(self):
        "AsiaqDataPipelineManager.fetch_content on a detached object: error"
        pipeline = self._unpersisted_pipeline()
        self.assertRaises(asiaq_exceptions.DataPipelineStateException, self.mgr.fetch_content, pipeline)

    def test__fetch_content__common_case__ok(self):
        "AsiaqDataPipelineManager.fetch_content in a 'normal' case behaves normally"
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
        "AsiaqDataPipelineManager.delete on a detached object: error"
        pipeline = self._unpersisted_pipeline()
        self.assertRaises(asiaq_exceptions.DataPipelineStateException, self.mgr.delete, pipeline)

    def test__delete__common_case__ok(self):
        "AsiaqDataPipelineManager.delete on a saved object: deletes"
        pipeline = self._persisted_pipeline()
        self.mgr.delete(pipeline)
        self.mock_client.delete_pipeline.assert_called_once_with(pipelineId='asdf')

    def test__save__update_pipeline__only_content_updated(self):
        "AsiaqDataPipelineManager.save on a persisted object: update only"
        contents = Mock()
        pipeline = self._persisted_pipeline(contents)
        self.mgr.save(pipeline)
        self.mock_client.create_pipeline.assert_not_called()
        self.mock_client.put_pipeline_definition.assert_called_once_with(
            pipelineId="asdf", pipelineObjects=contents, parameterObjects=[], parameterValues=[])

    def test__save__new_pipeline__meta_and_content_updated(self):
        "AsiaqDataPipelineManager.save on a detached object: create and save content"
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
        "AsiaqDataPipelineManager.search_descriptions with empty results"
        self.mock_client.list_pipelines.return_value = {'hasMoreResults': False, 'pipelineIdList': []}
        self.mock_client.describe_pipelines.return_value = {'pipelineDescriptionList': []}
        self.assertEquals([], self.mgr.search_descriptions())
        self.mock_client.list_pipelines.assert_called_once_with()
        self.mock_client.describe_pipelines.assert_not_called()

    def test__search_descriptions__search_all__all_found(self):
        "AsiaqDataPipelineManager.search_descriptions without filtering"
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
        "AsiaqDataPipelineManager.search_descriptions filtering for a non-existent pipeline name"
        self.assertEquals([], self.mgr.search_descriptions(name="asdfasdfasdfasd"))

    def test__search_descriptions__by_good_name__pipeline_found(self):
        "AsiaqDataPipelineManager.search_descriptions filtering for a unique pipeline name"
        searched = self.mgr.search_descriptions(name="nodescpipeline")
        self.assertEquals(1, len(searched))
        self.assertEquals("nodescpipeline", searched[0]._name)
        self.assertIsNone(searched[0]._description)

    def test__search_descriptions__by_dupe_name__pipelines_found(self):
        "AsiaqDataPipelineManager.search_descriptions filtering for a repeated pipeline name"
        searched = self.mgr.search_descriptions(name="mypipeline")
        self.assertEquals(2, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("buildit", searched[0]._id)
        self.assertEquals("ciya", searched[1]._id)

    def test__search_descriptions__by_bad_tag__nothing_found(self):
        "AsiaqDataPipelineManager.search_descriptions filtering for a non-existent tag"
        searched = self.mgr.search_descriptions(tags={'foo': 'bar'})
        self.assertEquals([], searched)

    def test__search_descriptions__by_good_tag__pipeline_found(self):
        "AsiaqDataPipelineManager.search_descriptions filtering for a unique tag"
        searched = self.mgr.search_descriptions(tags={'environment': 'ci'})
        self.assertEquals(1, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("pipeline in ci", searched[0]._description)

    def test__search_descriptions__by_dupe_name_and_tag__pipelines_found(self):
        "AsiaqDataPipelineManager.search_descriptions filtering for a tag/name combination"
        searched = self.mgr.search_descriptions(name="mypipeline", tags={'environment': 'build'})
        self.assertEquals(1, len(searched))
        self.assertEquals("mypipeline", searched[0]._name)
        self.assertEquals("buildit", searched[0]._id)

    def test__start__unpersisted__error(self):
        "AsiaqDataPipelineManager.start on a detached object: error"
        self.assertRaises(asiaq_exceptions.DataPipelineStateException,
                          self.mgr.start, self._unpersisted_pipeline())

    def test__start__persisted_without_params__started(self):
        "AsiaqDataPipelineManager.start with no parameter values anywhere"
        self.mgr.start(self._persisted_pipeline())
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertIsNone(activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__start__persisted_with_param_values__started_with_param_values(self):
        "AsiaqDataPipelineManager.start with parameter values in the object"
        pipeline = self._persisted_pipeline()
        pipeline._param_values = Mock()
        self.mgr.start(pipeline)
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals("asdf", activate_args['pipelineId'])
        self.assertEquals(pipeline._param_values, activate_args['parameterValues'])
        self.assertIn('startTimestamp', activate_args)

    def test__start__param_values_list__started_with_correct_param_values(self):
        "AsiaqDataPipelineManager.start with parameter values as a list and in the object"
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
        "AsiaqDataPipelineManager.start with parameter values as a dict and in the object"
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

    @patch('disco_aws_automation.disco_datapipeline.datetime')
    def test__start__no_time_given__utcnow_called(self, datetime):
        "AsiaqDataPipelineManager.start with no start time uses utcnow"
        fake_now = Mock()
        datetime.utcnow = Mock(return_value=fake_now)
        self.mgr.start(self._persisted_pipeline())
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals(fake_now, activate_args['startTimestamp'])

    def test__start__time_passed__time_used(self):
        "AsiaqDataPipelineManager.start with passed-in start time uses passed-in value"
        start_time = Mock()
        self.mgr.start(self._persisted_pipeline(), start_time=start_time)
        self.assertEquals(1, self.mock_client.activate_pipeline.call_count)
        activate_args = self.mock_client.activate_pipeline.call_args[1]
        self.assertEquals(start_time, activate_args['startTimestamp'])

    def test__stop__unpersisted__error(self):
        "AsiaqDataPipelineManager.stop with a detached object: error"
        self.assertRaises(asiaq_exceptions.DataPipelineStateException,
                          self.mgr.stop, self._unpersisted_pipeline())

    def test__stop__persisted__stopped(self):
        "AsiaqDataPipelineManager.stop with a saved pipeline: stops"
        self.mgr.stop(self._persisted_pipeline())
        self.mock_client.deactivate_pipeline.assert_called_once_with(pipelineId="asdf")


class PipelineUtilityTest(TestCase):
    "Unit tests for the utility functions in the data pipeline package."
    # pylint: disable=invalid-name
    FAKE_S3_URL = "s3://my-bucket/logs"

    def test__template_to_boto__missing_keys__key_error(self):
        "template_to_boto: missing top-level template keys fail."
        self.assertRaises(KeyError, template_to_boto, {})
        self.assertRaises(KeyError, template_to_boto, {'objects': []})
        self.assertRaises(KeyError, template_to_boto, {'parameters': []})

    def test__template_to_boto__empty_values__empty_return(self):
        "template_to_boto: degenerate input succeeds."
        objects, parameter_defs = template_to_boto({'objects': [], 'parameters': []})
        self.assertEquals([], objects)
        self.assertEquals([], parameter_defs)

    def test__template_to_boto__only_conserved_fields__identical_return(self):
        "template_to_boto: conserved_fields conserved."
        objects, parameter_defs = template_to_boto({
            'objects': [
                {'id': 'asdf', 'name': 'George'},
                {'id': 'qwerty', 'name': 'Fred'}
            ],
            'parameters': [{'id': '1234'}]
        })
        self.assertEquals(
            [{'id': 'asdf', 'name': 'George', 'fields': []}, {'id': 'qwerty', 'name': 'Fred', 'fields': []}],
            objects)
        self.assertEquals([{'id': '1234', 'attributes': []}], parameter_defs)

    def test__template_to_boto__object_simple_defs__fields_dict(self):
        "template_to_boto: string values translated."
        objects, _ = template_to_boto({
            'objects': [
                {'id': 'asdf', 'name': 'George', 'some_field': 'some_value'},
                {'id': 'qwerty', 'name': 'Fred', 'my_field': 'vaaalue'}
            ],
            'parameters': []
        })
        self.assertEquals(
            [{'id': 'asdf', 'name': 'George', 'fields': [{'key': 'some_field', 'stringValue': 'some_value'}]},
             {'id': 'qwerty', 'name': 'Fred', 'fields': [{'key': 'my_field', 'stringValue': 'vaaalue'}]}],
            objects)

    def test__template_to_boto__object_list_def__list_of_dicts(self):
        "template_to_boto: list values translated."
        objects, _ = template_to_boto({
            'objects': [
                {'id': 'asdf', 'name': 'George', 'some_field': ['val1', 'val2', 'val3']}
            ],
            'parameters': []
        })
        self.assertEquals(
            [{'id': 'asdf', 'name': 'George', 'fields': [
                {'key': 'some_field', 'stringValue': 'val1'},
                {'key': 'some_field', 'stringValue': 'val2'},
                {'key': 'some_field', 'stringValue': 'val3'},
            ]}],
            objects)

    def test__template_to_boto__object_ref__ref_value_found(self):
        "template_to_boto: ref values translated."
        objects, _ = template_to_boto({
            'objects': [
                {'id': 'asdf', 'name': 'George', 'some_field': {'ref': 'someIdGoesHere'}}
            ],
            'parameters': []
        })
        self.assertEquals(
            [{'id': 'asdf', 'name': 'George', 'fields': [
                {'key': 'some_field', 'refValue': 'someIdGoesHere'}
            ]}],
            objects)

    def test__template_to_boto__parameter_strings__fields_dicts(self):
        "template_to_boto: parameter defs translated."
        _, parameter_defs = template_to_boto({
            'objects': [],
            'parameters': [{
                "id": "myDDBRegion",
                "type": "String",
                "description": "Region of the DynamoDB table",
                "default": "us-east-1",
                "watermark": "us-east-1"
            }]
        })
        self.assertEquals(1, len(parameter_defs))
        self.assertEquals('myDDBRegion', parameter_defs[0]['id'])
        # These are not necessarily well-ordered, so we have to work around a little
        attrs_found = parameter_defs[0]['attributes']
        expected = [
            {'key': 'description', 'stringValue': 'Region of the DynamoDB table'},
            {'key': 'default', 'stringValue': 'us-east-1'},
            {'key': 'watermark', 'stringValue': 'us-east-1'},
            {'key': 'type', 'stringValue': 'String'}
        ]
        for expected_attr in expected:
            self.assertIn(expected_attr, attrs_found)

    def test__add_default_object_fields__no_default__error(self):
        "add_default_object_fields: invalid input produces an exception"
        object_list = [
            {'id': 'foo', 'name': 'bar', 'fields': []}, {'id': 'bar', 'name': 'baz', 'fields': []}
        ]
        added_fields = {'pipelineLogUri': self.FAKE_S3_URL}
        backup_list = copy.deepcopy(object_list)
        self.assertRaises(asiaq_exceptions.DataPipelineFormatException,
                          add_default_object_fields, object_list, added_fields)
        self.assertEquals(backup_list, object_list)

    def test__add_default_object_fields__existing_value__update(self):
        "add_default_object_fields: existing log location gets updated"
        object_list = [
            {'id': 'foo', 'name': 'bar', 'fields': []},
            {'id': 'Default', 'name': 'Deffy', 'fields': [
                {'key': 'pipelineLogUri', 'stringValue': 's3://stupid-bucket'},
                {'key': 'scheduleType', 'refValue': 'DailySchedule'}
            ]},
            {'id': 'bar', 'name': 'baz', 'fields': []}
        ]
        added_fields = {'pipelineLogUri': self.FAKE_S3_URL}
        backup_list = copy.deepcopy(object_list)
        add_default_object_fields(object_list, added_fields)
        self.assertNotEquals(backup_list, object_list)
        self.assertEquals(self.FAKE_S3_URL, object_list[1]['fields'][0]['stringValue'])

    def test__add_default_object_fields__no_value__insert(self):
        "add_default_object_fields: missing log location gets inserted"
        object_list = [
            {'id': 'foo', 'name': 'bar', 'fields': []},
            {'id': 'Default', 'name': 'Deffy', 'fields': [
                {'key': 'scheduleType', 'refValue': 'DailySchedule'}
            ]},
            {'id': 'bar', 'name': 'baz', 'fields': []}
        ]
        added_fields = {'fakeParam': self.FAKE_S3_URL}
        backup_list = copy.deepcopy(object_list)
        add_default_object_fields(object_list, added_fields)
        self.assertNotEquals(backup_list, object_list)
        self.assertEquals('fakeParam', object_list[1]['fields'][1]['key'])
        self.assertEquals(self.FAKE_S3_URL, object_list[1]['fields'][1]['stringValue'])

    def test__add_default_object_fields__new_and_existing_values__insert_and_update(self):
        "add_default_object_fields: existing log location gets updated, new field gets added"
        default_fields = [
            {'key': 'pipelineLogUri', 'stringValue': 's3://stupid-bucket'},
            {'key': 'scheduleType', 'refValue': 'DailySchedule'}
        ]
        object_list = [
            {'id': 'foo', 'name': 'bar', 'fields': []},
            {'id': 'Default', 'name': 'Deffy', 'fields': default_fields},
            {'id': 'bar', 'name': 'baz', 'fields': []}
        ]
        added_fields = {'pipelineLogUri': self.FAKE_S3_URL, 'newThing': 'newValue'}
        backup_list = copy.deepcopy(object_list)
        add_default_object_fields(object_list, added_fields)
        self.assertNotEquals(backup_list, object_list)
        self.assertEquals(self.FAKE_S3_URL, default_fields[0]['stringValue'])
        self.assertEquals('newValue', default_fields[2]['stringValue'])
