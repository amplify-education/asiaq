"""
Back end behaviors for creating data pipelines.
"""

import json
from datetime import datetime
from logging import getLogger

import boto3

from .disco_config import open_normalized
from .resource_helper import throttled_call
from .exceptions import ProgrammerError, DataPipelineFormatException, DataPipelineStateException


_LOG = getLogger(__name__)


class DataPipelineConsts(object):
    """Constants for data pipeline management."""
    TEMPLATE_DIR = "datapipeline_templates"


class AsiaqDataPipeline(object):
    """Local encapsulation of the information associated with a single data pipeline."""
    def __init__(self, name, description, tags=None, metadata=None,
                 pipeline_id=None, contents=None, parameter_definitions=None, param_values=None):
        self._objects = contents
        self._params = parameter_definitions
        self._param_values = _optional_dict_to_list(param_values)
        self._name = name
        self._description = description
        self._tags = _optional_dict_to_list(tags, key_string='key', value_string='value')
        self._metadata = metadata
        self._id = pipeline_id

    def is_persisted(self):
        "Return true if this pipeline has an AWS ID; false otherwise."
        return self._id is not None

    def has_content(self):
        "Return true if this pipeline has actual pipeline objects, false if it is only metadata."
        return self._objects is not None

    def get_tag_dict(self):
        "Retrieve tags as a dictionary."
        return _optional_list_to_dict(self._tags, key_string='key', value_string='value')

    def get_param_value_dict(self):
        "Retrieve parameter values as a dictionary."
        return _optional_list_to_dict(self._param_values)

    def update_content(self, contents=None, parameter_definitions=None, param_values=None,
                       template_name=None, log_location=None):
        """
        Set the pipeline content (pipeline nodes, parameters and values) for this pipeline.
        """
        if (not contents and not template_name) or (contents and template_name):
            raise ProgrammerError("Either pipeline content or a template (not both!) must be specified")
        if template_name:
            contents, parameter_definitions = _read_template(template_name, log_location)
        self._objects = contents
        self._params = parameter_definitions
        self._param_values = _optional_dict_to_list(param_values)

    @classmethod
    def from_template(cls, template_name, name, description, tags=None, param_values=None, log_location=None):
        """Create a new AsiaqDataPipeline object, populated from template in the configuration directory."""
        boto_objects, boto_parameters = _read_template(template_name, log_location)
        return cls(contents=boto_objects, parameter_definitions=boto_parameters,
                   name=name, description=description, tags=tags, param_values=param_values)


class AsiaqDataPipelineManager(object):
    "List, retrieve, store and delete pipelines."
    def __init__(self, client=None):
        self._dp_client = client or boto3.client("datapipeline")

    def save(self, pipeline):
        "Save or update the pipeline object in AWS."
        if not pipeline.is_persisted():  # if this is a new pipeline, save the metadata first
            unique_id = "pipeline_init_%s" % datetime.now().isoformat()  # make the create call idempotent
            created = throttled_call(self._dp_client.create_pipeline,
                                     name=pipeline._name, uniqueId=unique_id,
                                     description=pipeline._description, tags=pipeline._tags or [])
            pipeline._id = created['pipelineId']
        # regardless, save the pipeline content:
        resp = throttled_call(self._dp_client.put_pipeline_definition,
                              pipelineId=pipeline._id,
                              pipelineObjects=pipeline._objects or [],
                              parameterObjects=pipeline._params or [],
                              parameterValues=pipeline._param_values or [])
        return resp

    # possibly worth having but not currently needed: set_tags
    # http://boto3.readthedocs.io/en/latest/reference/services/datapipeline.html#DataPipeline.Client.add_tags

    def fetch(self, pipeline_id):
        "Fetch a pipeline (metadata and content) from AWS by ID."
        contents = throttled_call(self._dp_client.get_pipeline_definition, pipelineId=pipeline_id,
                                  version='latest')
        _LOG.debug("Contents for %s: %s", pipeline_id, contents)
        meta_resp = throttled_call(self._dp_client.describe_pipelines, pipelineIds=[pipeline_id])
        meta = meta_resp['pipelineDescriptionList'][0]
        return AsiaqDataPipeline(
            pipeline_id=pipeline_id, name=meta['name'], description=meta.get('description'),
            tags=meta.get('tags'), metadata=meta.get('fields'),
            contents=contents['pipelineObjects'],
            parameter_definitions=contents.get('parameterObjects'),
            param_values=contents.get('parameterValues')
        )

    def fetch_content(self, pipeline):
        "Populate the pipeline content fields of this pipeline object with the information fetched from AWS."
        if pipeline.has_content():
            raise DataPipelineStateException(
                "Content already fetched or locally generated for this pipeline object")
        if not pipeline.is_persisted():
            raise DataPipelineStateException("Cannot fetch content for a pipeline that has not been saved")
        definition = throttled_call(self._dp_client.get_pipeline_definition,
                                    pipelineId=pipeline._id, version='latest')
        pipeline.update_content(definition['pipelineObjects'],
                                definition.get('parameterObjects'),
                                definition.get('parameterValues'))

    def search_descriptions(self, name=None, tags=None):
        """
        Fetch all pipelines in this account/region that have the given tags and/or the given name.
        If arguments are left empty, all pipelines will be fetched.  Only names and metadata are
        retrieved: use fetch_content to retrieve the pipeline internal details.
        """
        id_objects = self._fetch_ids()
        descriptions = []
        window = 25
        tag_set = {(k, v) for k, v in tags.items()} if tags else set()

        def _search_matches(desc):
            if name and name != desc.get('name'):
                return False
            if tag_set:
                tags_found = [(tag['key'], tag['value']) for tag in desc.get('tags', [])]
                if not tag_set.issubset(set(tags_found)):
                    return False
            return True

        for i in range(0, len(id_objects), window):
            batch = throttled_call(self._dp_client.describe_pipelines,
                                   pipelineIds=[desc['id'] for desc in id_objects[i:i + window]])
            descriptions.extend([desc for desc in batch['pipelineDescriptionList'] if _search_matches(desc)])
        return [
            AsiaqDataPipeline(
                pipeline_id=meta['pipelineId'], name=meta['name'], description=meta.get('description'),
                tags=meta.get('tags'), metadata=meta.get('fields'))
            for meta in descriptions
        ]

    def start(self, pipeline, params=None):
        "Activate the pipeline in AWS."
        if not pipeline.is_persisted():
            raise DataPipelineStateException("Pipeline must be saved before it can be activated")
        param_values = _optional_dict_to_list(params) or pipeline._param_values
        return self._dp_client.activate_pipeline(pipelineId=pipeline._id, startTimestamp=datetime.utcnow(),
                                                 parameterValues=param_values)

    def stop(self, pipeline):
        "Deactivate the pipeline in AWS."
        if not pipeline.is_persisted():
            raise DataPipelineStateException("Pipeline must be saved before it can be deactivated")
        return self._dp_client.deactivate_pipeline(pipelineId=pipeline._id)

    def delete(self, pipeline):
        "Remove the pipeline completely from AWS."
        if not pipeline.is_persisted():
            raise DataPipelineStateException("Pipeline must be saved before it can be deleted (but...)")
        self._dp_client.delete_pipeline(pipelineId=pipeline._id)

    def fetch_or_create(self, template_name, pipeline_name, pipeline_description, tags, log_location):
        """
        If a pipeline with the given tags exists, return it; if not, create one with the given tags
        and name based on the provided pipeline, save it, and return it.
        """
        searched = self.search_descriptions(tags=tags)
        if searched:
            if len(searched) > 1:
                raise DataPipelineStateException(
                    "Expected one pipeline with tags %s, found %s" % (tags, len(searched)))
            pipeline = searched[0]
            _LOG.info("Found existing pipeline %s", pipeline._id)
            self.fetch_content(pipeline)
        else:
            pipeline = AsiaqDataPipeline.from_template(
                template_name, pipeline_name, description=pipeline_description,
                log_location=log_location, tags=tags)
            self.save(pipeline)
            _LOG.info("Created new pipeline %s", pipeline._id)
        return pipeline

    def _fetch_ids(self):
        found_defs = []
        resp = throttled_call(self._dp_client.list_pipelines)
        found_defs.extend(resp['pipelineIdList'])
        while resp['hasMoreResults']:
            resp = throttled_call(self._dp_client.list_pipelines, marker=resp['marker'])
            found_defs.extend(resp['pipelineIdList'])
        return found_defs


def template_to_boto(template_json):
    "Transform data in the format that Amazon gives in their templates to the format boto3 uses."
    boto3_objects = []
    boto3_params = []
    conserved_object_fields = ['id', 'name']
    for obj in template_json['objects']:
        xformed = {k: obj[k] for k in conserved_object_fields if k in obj}
        fields = []
        for key, value in obj.items():
            if key in conserved_object_fields:
                continue
            if isinstance(value, dict) and 'ref' in value:
                fields.append({'key': key, 'refValue': value['ref']})
            elif isinstance(value, (list, tuple)):
                fields.extend([{'key': key, 'stringValue': step} for step in value])
            else:
                fields.append({'key': key, 'stringValue': value})
        xformed['fields'] = fields
        boto3_objects.append(xformed)

    for param in template_json['parameters']:
        xformed = {k: param[k] for k in conserved_object_fields if k in param}
        xformed['attributes'] = [{'key': k, 'stringValue': param[k]}
                                 for k in param
                                 if k not in conserved_object_fields]
        boto3_params.append(xformed)
    return (boto3_objects, boto3_params)


def add_log_location_param(boto_objects, log_location):
    """
    Silly-looking utility function to traverse a pipeline template, find the place where we want
    to insert the URI for logs to be written, and insert it there.  Abstracted out to keep it from
    cluttering up more interesting code.
    """
    default_found = False
    for pipeline_obj in boto_objects:
        if pipeline_obj['id'] == 'Default':
            default_found = True
            log_setting_found = False
            for field in pipeline_obj['fields']:
                if field['key'] == 'pipelineLogUri':
                    log_setting_found = True
                    _LOG.debug("Updating existing log bucket %s to %s", field['stringValue'], log_location)
                    field['stringValue'] = log_location
            if not log_setting_found:
                pipeline_obj['fields'].append({'key': 'pipelineLogUri', 'stringValue': log_location})
            break
    if not default_found:
        raise DataPipelineFormatException(
            "No 'Default' object found: this is probably not a valid data pipeline definition")


def _optional_dict_to_list(param_value_dict, key_string='id', value_string='stringValue'):
    """
    If given a dictionary, convert it to a list of key-value dictionary entries.
    If not given a dictionary, just return whatever we were given.
    """
    if not isinstance(param_value_dict, dict):
        return param_value_dict
    value_objects = []
    for param_id, value in param_value_dict.items():
        value_objects.append({key_string: param_id, value_string: value})
    return value_objects


def _optional_list_to_dict(dict_list, key_string='id', value_string='stringValue'):
    """
    If given a list of dictionaries, a dictionary using the given lookup keys.
    If given None, return None.  If given something invalid, sneeze demons.
    """
    if dict_list is None:
        return None
    value_dict = {}
    for item in dict_list:
        key = item[key_string]
        if key in value_dict:
            raise DataPipelineFormatException("Repeated item %s in list-to-dictionary transform!" % key)
        try:
            value_dict[key] = item[value_string]
        except KeyError as missing:
            raise DataPipelineFormatException("Bad dictionary in list-to-dictionary transform: %s"
                                              % str(missing))
    return value_dict


def _read_template(template_name, log_location):
    """
    Open a template file, read the definition, and translate it into the format expected by boto.
    If log_location is supplied, insert it into the template definition before returning it.
    """
    with open_normalized(DataPipelineConsts.TEMPLATE_DIR, template_name + ".json") as f:
        template_data = json.load(f)
    boto_objects, boto_params = template_to_boto(template_data)
    if log_location:
        add_log_location_param(boto_objects, log_location)
    return boto_objects, boto_params
