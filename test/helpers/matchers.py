"""
Collection of classes to help matching values in unit tests
"""


class MatchAnything(object):
    """Helper class to use with assertions that can match any value"""

    def __eq__(self, other):
        return True


class MatchClass(object):
    """Helper class to use with assertions that matches values that are instances of a class"""

    def __init__(self, clazz):
        self.clazz = clazz

    def __eq__(self, other):
        return isinstance(other, self.clazz)
