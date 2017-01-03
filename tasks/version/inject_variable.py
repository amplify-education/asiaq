#!/usr/bin/env python
"""
Extracts version-injection logic from version.rake into a free-standing python
executable, so we don't duplicate the logic but we can also use it in situations
other than what the rake task assumes (e.g. in an rpm build scriptlet).

Since it proved immediately useful to have it do so, this script also supports
setting arbitrary string variables using an option syntax similar to rpmbuild
or mock ('-D "varname new-value"').
"""

import re
import fileinput
from argparse import ArgumentParser


def _variable_search_regex(variable_name):
    return re.compile(r"(%s\s+=\s+['\"])([^'\"]*)(['\"])" % variable_name)


def _replacement_pattern(replacement_content):
    return r'\g<1>%s\g<3>' % replacement_content


def _main():
    parser = ArgumentParser()
    parser.add_argument("-r", "--rpm-version", action="store", dest="rpm_version",
                        help="RPM version to inject")
    parser.add_argument("-g", "--git-hash", action="store", dest="git_hash", help="git commit hash to inject")
    parser.add_argument("-D", "--define", action="append", dest="arbitrary_tuples",
                        help=("Arbitary variable/value pairs, separated on the first space "
                              "(sort of like rpmbuild)."))
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()

    filters = []
    if args.rpm_version:
        filters.append((_variable_search_regex("__rpm_version__"), _replacement_pattern(args.rpm_version)))
    if args.git_hash:
        filters.append((_variable_search_regex("__git_hash__"), _replacement_pattern(args.git_hash)))
    if args.arbitrary_tuples:
        for input_tuple in args.arbitrary_tuples:
            (variable_name, new_value) = input_tuple.split(None, 1)
            filters.append((_variable_search_regex(variable_name), _replacement_pattern(new_value)))

    for line in fileinput.input(files=args.files, inplace=1):
        for pattern, replacement in filters:
            line = pattern.sub(replacement, line)
        print line,  # not a typo: the terminal comma tells python not to print a newline


if '__main__' == __name__:
    _main()
