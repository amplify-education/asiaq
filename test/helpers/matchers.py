"""
Some helpers for matching values in unit tests
"""


class MatchAnything(object):
    """Helper class to use with assertions that can match any value"""
    def __eq__(self, other):
        return True
