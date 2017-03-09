"""
Back end behaviors for creating data pipelines.
"""

import json
from datetime import datetime
from logging import getLogger

from pytz import utc
import boto3

from .disco_config import open_normalized
from .disco_vpc import DiscoVPC
from .resource_helper import throttled_call
from .exceptions import (
    ProgrammerError, DataPipelineFormatException, DataPipelineStateException, VPCEnvironmentError
)


_LOG = getLogger(__name__)


class DataPipelineConsts(object):
    """Constants for data pipeline management."""
    TEMPLATE_DIR = "datapipeline_templates"
    LOG_LOCATION_FIELD = "pipelineLogUri"
    SUBNET_ID_FIELD = 'subnetId'


class DataPipelineMetadata(object):
    """Constants for data pipeline metadata field names in AWS."""
    HEALTH = '@healthStatus'
    STATE = '@pipelineState'
    LAST_RUN = '@latestRunTime'
    INITIAL_ACTIVATION = '@firstActivationTime'
    CREATION = '@creationTime'


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

    @property
    def last_run(self):
        "Return a UTC datetime for the last time this pipeline was run (and if it fails...?)"
        try:
            return self._date_metadata_field(DataPipelineMetadata.LAST_RUN)
        except KeyError:
            return None

    @property
    def health(self):
        """Return the health code (e.g. "HEALTHY", we hope) for this pipeline."""
        try:
            return self._metadata_field(DataPipelineMetadata.HEALTH)
        except KeyError:
            return None

    @property
    def pipeline_state(self):
        """Return the state code (e.g. "SCHEDULED") for this pipeline."""
        return self._metadata_field(DataPipelineMetadata.STATE)

    @property
    def create_date(self):
        "Return a UTC datetime for the creation date of this pipeline."
        return self._date_metadata_field(DataPipelineMetadata.CREATION)

    def get_tag_dict(self):
        "Retrieve tags as a dictionary."
        return _optional_list_to_dict(self._tags, key_string='key', value_string='value')

    def get_param_value_dict(self):
        "Retrieve parameter values as a dictionary."
        return _optional_list_to_dict(self._param_values)

    def update_content(self, contents=None, parameter_definitions=None, param_values=None,
                       template_name=None, log_location=None, subnet_id=None):
        """
        Set the pipeline content (pipeline nodes, parameters and values) for this pipeline.

        If param_values is not passed in, existing values will be left untouched.
        """
        if (not contents and not template_name) or (contents and template_name):
            raise ProgrammerError("Either pipeline content or a template (not both!) must be specified")
        if template_name:
            contents, parameter_definitions = _read_template(template_name)
        _update_defaults(contents, log_location, subnet_id)
        self._objects = contents
        self._params = parameter_definitions
        if param_values is not None:
            self._param_values = _optional_dict_to_list(param_values)

    def _metadata_field(self, field_name):
        if not self._metadata:
            raise DataPipelineStateException("No metadata fields found on pipeline '%s'" % self._name)
        for field_definition in self._metadata:
            if field_definition['key'] == field_name:
                return field_definition['stringValue']
        raise KeyError("Field '%s' was not found in pipeline '%s'" % (field_name, self._name))

    def _date_metadata_field(self, field_name, with_timezone=True):
        timestamp = self._metadata_field(field_name)
        parsed = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S')
        return parsed.replace(tzinfo=utc) if with_timezone else parsed

    @classmethod
    def from_template(cls, template_name, name, description, tags=None, param_values=None,
                      log_location=None, subnet_id=None):
        """Create a new AsiaqDataPipeline object, populated from template in the configuration directory."""
        boto_objects, boto_parameters = _read_template(template_name)
        _update_defaults(boto_objects, log_location, subnet_id)
        return cls(contents=boto_objects, parameter_definitions=boto_parameters,
                   name=name, description=description, tags=tags, param_values=param_values)


class AsiaqDataPipelineManager(object):
    "List, retrieve, store and delete pipelines."
    def __init__(self, client=None, config=None):
        self._dp_client = client or boto3.client("datapipeline")
        self.config = config  # REFACTOR-BAIT

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

    def start(self, pipeline, params=None, start_time=None):
        """
        Activate the pipeline in AWS.
        Optional Parameters:
            params: a dictionary or list of parameter values to pass in at activation time (default: none)
            start_time: the time at which to activate, as a datetime object (default: now)
        """
        if not pipeline.is_persisted():
            raise DataPipelineStateException("Pipeline must be saved before it can be activated")
        if not start_time:
            start_time = datetime.utcnow()
        param_values = _optional_dict_to_list(params) or pipeline._param_values
        return self._dp_client.activate_pipeline(pipelineId=pipeline._id,
                                                 startTimestamp=start_time,
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

    def fetch_or_create(self, template_name, pipeline_name, pipeline_description, tags, log_location,
                        force_update=False, metanetwork=None, availability_zone=None):
        """
        If a pipeline with the given tags exists, return it; if not, create one with the given tags
        and name based on the provided pipeline, save it, and return it.

        If metanetwork is supplied, the pipeline will be set to run EC2 instances in a subnet that
        belongs to that metanetwork; if availability_zone is supplied, it will be run in the subnet
        that belongs to that availability zone.  NOTE: availability_zone may be either a full AZ name
        (e.g. "us-west-2a") or a one-letter suffix ("a"); in the latter case, the AZ that ends with that
        suffix will be used. In any case where a specific subnet or metanetwork is requested and but
        cannot be found, a VPCEnvironmentError will be raised.

        If force_update is supplied, update the pipeline contents as if we had just created it, rather
        than fetching the existing content.
        """
        searched = self.search_descriptions(tags=tags)
        subnet_id = None
        if searched:
            if len(searched) > 1:
                raise DataPipelineStateException(
                    "Expected one pipeline with tags %s, found %s" % (tags, len(searched)))
            pipeline = searched[0]
            _LOG.info("Found existing pipeline %s", pipeline._id)
            if force_update:
                _LOG.info("Re-loading content of pipeline %s from template %s", pipeline._id, template_name)
                if metanetwork:
                    subnet_id = self._find_subnet_id(metanetwork, availability_zone)
                pipeline.update_content(template_name=template_name,
                                        log_location=log_location, subnet_id=subnet_id)
                self.save(pipeline)
            else:
                self.fetch_content(pipeline)
        else:
            if metanetwork:
                subnet_id = self._find_subnet_id(metanetwork, availability_zone)
            pipeline = AsiaqDataPipeline.from_template(
                template_name, pipeline_name, description=pipeline_description,
                log_location=log_location, tags=tags, subnet_id=subnet_id)
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

    def _find_subnet_id(self, metanetwork, availability_zone=None):
        """
        Find a DiscoSubnet object that matches the input parameters and return its ID.

        If availability_zone is not supplied, an arbitrary subnet will be chosen;
        if it is supplied and is a valid AZ, the subnet for that AZ will be chosen;
        if it is supplied and is not a valid AZ but is a suffix of an AZ that has
        a subnet in it, that subnet will be chosen (e.g. 'a' for 'us-west-2a' but
        also 'us-east-1a', if we were to move regions).
        """

        vpc = DiscoVPC.fetch_environment(environment_name=self.config.environment)
        subnets = vpc.networks[metanetwork].disco_subnets
        if availability_zone:
            if availability_zone in subnets:
                found_subnet = subnets[availability_zone]
            else:
                for az_name, subnet in subnets.items():
                    if az_name.endswith(availability_zone):
                        found_subnet = subnet
                        break
                else:
                    raise VPCEnvironmentError(
                        "No match found for availability_zone argument '%s' in metanetwork '%s'" %
                        (metanetwork, availability_zone)
                    )
        else:
            found_subnet = subnets.values()[0]
        _LOG.debug("Found subnet %s in AZ %s for network %s",
                   found_subnet.subnet_id, found_subnet.name, metanetwork)
        return found_subnet.subnet_id


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


def add_default_object_fields(boto_objects, field_values):
    """
    Silly-looking utility function to traverse a pipeline template, find the place where we want
    to insert default values (e.g. the URI for logs to be written, the subnetId to run instances,
    or roles to run under), and insert it there.  Abstracted out to keep it from cluttering up
    more interesting code.
    """
    default_found = False
    field_found = {}
    for pipeline_obj in boto_objects:
        if pipeline_obj['id'] == 'Default':
            default_found = True
            for field in pipeline_obj['fields']:
                field_key = field['key']
                if field_key in field_values:
                    new_value = field_values[field_key]
                    field_found[field_key] = True
                    _LOG.debug("Updating existing default for '%s' from %s to %s",
                               field_key, field['stringValue'], new_value)
                    field['stringValue'] = new_value
            for field_key, new_value in field_values.items():
                if field_key not in field_found:
                    pipeline_obj['fields'].append({'key': field_key, 'stringValue': new_value})
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


def _read_template(template_name):
    """
    Open a template file, read the definition, and translate it into the format expected by boto.
    If log_location is supplied, insert it into the template definition before returning it.
    """
    with open_normalized(DataPipelineConsts.TEMPLATE_DIR, template_name + ".json") as f:
        template_data = json.load(f)
    boto_objects, boto_params = template_to_boto(template_data)
    return boto_objects, boto_params


def _update_defaults(pipeline_objects, log_location=None, subnet_id=None):
    new_fields = {}
    if log_location:
        new_fields[DataPipelineConsts.LOG_LOCATION_FIELD] = log_location
    if subnet_id:
        new_fields[DataPipelineConsts.SUBNET_ID_FIELD] = subnet_id
    if new_fields:
        add_default_object_fields(pipeline_objects, new_fields)
