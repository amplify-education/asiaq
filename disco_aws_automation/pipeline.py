"""
Module for encapsulating a pipeline
"""

import csv

from disco_aws_automation.disco_aws_util import is_truthy


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
          "min_size": None,
          "desired_size": 1,
          "max_size": None,
          "instance_type": "m1.large",
          "extra_disk": None,
          "extra_space": None,
          "iops": None,
          "smoke_test": "true",
          "ami": None,
          "deployable": "true",
          "termination_policies": None,
          "chaos": "yes"
        }

        sequence -- the instance boot sequence number
        hostclass -- the hostclass name of instance
        min_size -- the minimum size of the autoscaling group
        desired_size -- the currently desired size of for the autoscaling group
        max_size -- the maximum size of the autoscaling group
        instance_type -- the Amazon instance type, m3.large, t2.small, etc.
        extra_disk -- the number of GB to allocate in an additional disk
        extra_space -- the number of extra GB to allocate on the root disk
        iops -- the number of provision IOPS to request for the additional disk
        smoke_test -- If yes ensure instance passes smoke test before continuing on starting next sequence
        ami -- specific AMI to use instead of latest tested AMI for hostclass
        deployable -- if true we can replace an instance with a newer one
        termination_policies -- policies to control which instances auto scaling terminates
        chaos -- when true we want these instances to be terminatable by the chaos process
    '''

    def __init__(self, *args, **kwargs):
        super(Pipeline, self).__init__(*args, **kwargs)

    def copy(self):
        return Pipeline(super(Pipeline, self).copy())

    #####################################################################################
    # Helper functions for getting pipeline data stored in this dict.                   #
    # Will perform tranformations when appropriate.                                     #
    # If you want the raw values, then just access the data through the dict interface. #
    #####################################################################################

    def sequence(self):
        ''' required '''
        return int(self.__getitem__("sequence"))

    def hostclass(self):
        ''' required '''
        return self.__getitem__("hostclass")

    def min_size(self):
        ''' :return: recurrence map as min int or none '''
        return min(self._val_as_recurrence_map("min_size").values())

    def desired_size(self):
        ''' :return: recurrence map as max int or none '''
        return max(self._val_as_recurrence_map("desired_size").values())

    def max_size(self):
        ''' :return: recurrence map as max int or none '''
        return max(self._val_as_recurrence_map("max_size").values())

    def instance_type(self):
        return self.get("instance_type")

    def extra_space(self):
        return int(self.get("extra_space")) if self.has_key("extra_space") else None

    def extra_disk(self):
        return int(self.get("extra_disk")) if self.has_key("extra_disk") else None

    def smoke_test(self):
        return is_truthy(self.get("smoke_test", "false"))

    def ami(self):
        return self.get("ami")

    def iops(self):
        return int(self.get("iops")) if self.has_key("iops") else None

    def chaos(self):
        return is_truthy(self.get("chaos", "false"))

    def termination_policies(self):
        return self.get("termination_policies").split() if self.has_key("termination_policies") else None

    def _val_as_recurrence_map(self, key, sentinel=''):
        size = self.get(key)
        if not size:
            return {sentinel: None}
        else:
            return {sentinel: int(size)} if str(size).isdigit() else {
                part.split('@')[1]: int(part.split('@')[0])
                for part in str(size).split(':')}
