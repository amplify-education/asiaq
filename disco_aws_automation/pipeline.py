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
    '''Class encapsulating a pipeline with some additional helper functions specific to a pipeline.

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
          "integration_test": "testscriptparams",
          "ami": None,
          "deployable": "true",
          "termination_policies": None,
          "chaos": "yes"
        }
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

    def get_sequence(self):
        ''' required.
            :return: int, the instance boot sequence number.
        '''
        return int(self.__getitem__("sequence"))

    def get_hostclass(self):
        ''' required.
            :return: string, the hostclass name of the instance.
        '''
        return self.__getitem__("hostclass")

    def get_min_size(self):
        ''' :return: int, min_size as min int or None. For example:
                     - no value, will return: None
                     - simple int value of 5 will return: 5
                     - timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: 2
        '''
        return min(self.get_min_size_as_recurrence_map().values())

    def get_min_size_as_recurrence_map(self):
        ''' :return: dict, min_size as a recurrence map. Take a look at _get_size_as_recurrence_map(). '''
        return self._size_as_recurrence_map(self.get("min_size"))

    def get_desired_size(self):
        ''' :return: int, desired_size as max int or None. For example:
                     - no value, will return: None
                     - simple int value of 5 will return: 5
                     - timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: 24
        '''
        return max(self.get_desired_size_as_recurrence_map().values())

    def get_desired_size_as_recurrence_map(self):
        ''' :return: dict, desired_size as a recurrence map. Take a look at _get_size_as_recurrence_map(). '''
        return self._size_as_recurrence_map(self.get("desired_size"))

    def get_max_size(self):
        ''' :return: int, max_size as max int or None. For example:
                     - no value, will return: None
                     - simple int value of 5 will return: 5
                     - timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: 24
        '''
        return max(self.get_max_size_as_recurrence_map().values())

    def get_max_size_as_recurrence_map(self):
        ''' :return: dict, max_size as a recurrence map. Take a look at _get_size_as_recurrence_map(). '''
        return self._size_as_recurrence_map(self.get("max_size"))

    def get_instance_type(self):
        ''' :return: string, the instance_type or None if not set. '''
        return self.get("instance_type")

    def get_extra_disk(self):
        ''' :return: int, size in GB for additional disk or None if not set. '''
        return int(self.get("extra_disk")) if self.__contains__("extra_disk") else None

    def get_extra_space(self):
        ''' :return: int, size in GB for additional root disk or None if not set. '''
        return int(self.get("extra_space")) if self.__contains__("extra_space") else None

    def get_iops(self):
        ''' :return: int, number of IOPS to request for the additional disk or None if not set '''
        return int(self.get("iops")) if self.__contains__("iops") else None

    def get_smoke_test(self):
        ''' :return: boolean, if True, ensure instance passes smoke test before continuing on starting
                     next sequence.  Defaults to False.
        '''
        return is_truthy(self.get("smoke_test", "false"))

    def get_integration_test(self):
        ''' :return: string, value(s) to send to the integration test script or None if not set. '''
        return self.get("integration_test")

    def get_ami(self):
        ''' :return: string, id of the specific AMI to use instead of latest tested AMI for hostclass
                     or None if not set.
        '''
        return self.get("ami")

    def get_deployable(self):
        ''' :return: boolean, if True we can replace an instance with a newer one.  Defaults to False. '''
        return is_truthy(self.get("deployable", "false"))

    def get_chaos(self, default_val=False):
        ''' :return: boolean, when True we want these instances to be terminatable by the chaos process.
                     Defaults to val of param default_val.
        '''
        return is_truthy(self.get("chaos")) if self.__contains__("chaos") else default_val

    def get_termination_policies(self):
        ''' :return: list of string, policies to control which instances auto scaling terminates. '''
        return self.get("termination_policies").split() if self.__contains__("termination_policies") else None

    @staticmethod
    def _size_as_recurrence_map(size):
        ''' :return: dict, size as "recurrence" map. For example:
                     - no value, will return: {None: None}
                     - simple int value of 5 will return: {None: 5}
                     - timed interval(s), like "2@0 22 * * *:24@0 10 * * *", will return: {'0 10 * * *': 24,
                                                                                           '0 22 * * *': 2}
        '''
        if not size:
            return {None: None}

        if str(size).isdigit():
            return {None: int(size)}
        else:
            return {part.split('@')[1]: int(part.split('@')[0])
                    for part in str(size).split(':')}
