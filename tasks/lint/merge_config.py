"""
merge lint files
"""
import sys
import re
from configobj import ConfigObj


def main(args):
    """
    Loads two config files and merges the second (user) into the first (defaults)
    Writes the merged config into the path passed in the arguments list
    """
    base_config_file = args[0]
    user_config_file = args[1]
    merged_config_file = args[2]

    base_config = ConfigObj(base_config_file)
    user_config = ConfigObj(user_config_file)

    # merge the files, with user items taking precedence
    base_config.merge(user_config)

    # change the path and rewrite the file
    base_config.filename = merged_config_file
    base_config.write()

    # clean up the lint file
    with open(merged_config_file, 'r') as f:
        file_data = f.read()

    # the configobj pads things with spaces that pylint doesn't like
    file_data = re.sub("\s=\s", "=", file_data)
    file_data = re.sub(",\s", ",", file_data)
    file_data = re.sub('=""', "=", file_data)

    with open(merged_config_file, 'w') as f:
        f.write(file_data)

if __name__ == '__main__':
    main(sys.argv[1:])
