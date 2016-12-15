"""
Rudimentary templating for our hostclass configuration
Right now only substitutes the template name and hostclass
"""
# String is not fully deprecated http://www.logilab.org/ticket/2481
from string import Template  # pylint: disable=W0402
import os

from .disco_config import normalize_path
from .disco_constants import HOSTCLASS_PREFIX

TEMPLATES = [
    'init/template.sh',
]


class HostclassTemplating(object):
    '''namespace for hostclass templating functions'''

    @staticmethod
    def create_from_template(hostclass, template_conf_name):
        '''
        Copies the template_conf_name file to a file after making the following templating actions:
        * Replace 'template' in the file name with the hostclass
        * Replace '$TEMPLATED_NAME' in the file contents with the hostclass sans HOSTCLASS_PREFIX
        * Replace '$TEMPLATED_HOSTCLASS' in the file contents with the hostclass
        '''
        dest_conf_name = template_conf_name.replace('template', hostclass)
        prefix_len = len(HOSTCLASS_PREFIX)
        replacements = dict(TEMPLATED_NAME=hostclass[prefix_len:],
                            TEMPLATED_HOSTCLASS=hostclass)
        with open(template_conf_name, "r+") as input_file:
            with open(dest_conf_name, "w+") as output_file:
                template = Template(input_file.read())
                output = template.safe_substitute(replacements)
                output_file.write(output)
        os.chmod(dest_conf_name, os.stat(template_conf_name).st_mode)

    @staticmethod
    def create_hostclass(hostclass):
        '''
        Creates a base hostclass configuration to modify to your needs.
        '''
        if not hostclass.startswith(HOSTCLASS_PREFIX):
            raise ValueError(
                "hostclass must start with {0}.".format(HOSTCLASS_PREFIX))
        for template_conf_name in map(normalize_path, TEMPLATES):
            HostclassTemplating.create_from_template(hostclass, template_conf_name)
