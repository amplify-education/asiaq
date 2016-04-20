"""
Module for encapsulating a pipeline
"""

import csv


def pipelines_from_file(pipeline_definition_filename):
    '''Given a filename of csv file containining pipeline info,
       returns list of Pipeline objects representing contents of the file.
    '''
    with open(pipeline_definition_filename, "r") as f:
        reader = csv.DictReader(f)
        pipelines = [Pipeline(line) for line in reader]
    return pipelines


class Pipeline(dict):
    '''Class encapsulating a pipeline.
       This class looks and acts just like a dict with some additional functions specific to a pipeline.

       An example pipeline format:

       {  "sequence": 1,
          "hostclass": "mhcdiscosomething",
          "desired_size": 1,
          "instance_type": "m1.large",
          "extra_space": None,
          "extra_disk": None,
          "iops": None,
          "smoke_test": "true",
          "ami": None,
          "min_size": None,
          "max_size": None,
          "termination_policies": None,
          "chaos": "yes"
        }
    '''

    def __init__(self, *args, **kwargs):
        super(Pipeline, self).__init__(*args, **kwargs)

    def copy(self):
        return Pipeline(super(Pipeline, self).copy())

    ####################################
    # functions specific to a pipeline #
    ####################################

    def _val_as_recurrence_map(self, key, sentinel=''):
        size = self.get(key)
        if not size:
            return {sentinel: None}
        else:
            return {sentinel: int(size)} if str(size).isdigit() else {
                part.split('@')[1]: int(part.split('@')[0])
                for part in str(size).split(':')}

    def recurrence_map_as_min_int_or_none(self, key):
        return min(self._val_as_recurrence_map(key).values())

    def recurrence_map_as_max_int_or_none(self, key):
        return max(self._val_as_recurrence_map(key).values())
