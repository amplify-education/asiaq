"""Binaries"""
from __future__ import print_function

from collections import defaultdict

import sys


def print_table(rows, headers=None, space_between_columns=4):
    """
    Convenience method for printing a list of dictionary objects into a table. Automatically sizes the
    columns to be the maximum size of any entry in the dictionary, and adds additional buffer whitespace.

    Params:
        rows -                  A list of dictionaries representing a table of information, where keys are the
                                headers of the table. Ex. { 'Name': 'John', 'Age': 23 }

        headers -               A list of the headers to print for the table. Must be a subset of the keys of
                                the dictionaries that compose the row. If a header isn't present or it's value
                                has a falsey value, the value printed is '-'.

        space_between_columns - The amount of space between the columns of text. Defaults to 4.
    """
    columns_to_sizing = defaultdict(int)
    format_string = ''

    headers = headers or rows[0].keys()

    for row in rows:
        for header in headers:
            value = row.get(header, '-')
            # fix unicode errors by encoding to ascii and replacing all non-ascii characters with "?"
            if isinstance(value, unicode):
                value = value.encode('ascii', 'replace')
                row[header] = value
            columns_to_sizing[header] = max(len(str(value)), columns_to_sizing[header])

    for header in headers:
        column_size = max(columns_to_sizing[header], len(header)) + space_between_columns
        format_string += '{' + header + ':<' + str(column_size) + '}'

    print(format_string.format(**{key: key for key in headers}), file=sys.stderr)

    for row in rows:
        defaulted_row = {header: row.get(header) or '-' for header in headers}
        print(format_string.format(**defaulted_row))
