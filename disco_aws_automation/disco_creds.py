"""
S3 bucket accessor code
"""
import logging
from ConfigParser import ConfigParser
from StringIO import StringIO

from boto.s3.connection import S3Connection
from boto.exception import S3ResponseError

from disco_aws_automation.exceptions import S3WritingError
from .resource_helper import check_written_s3

SSH_PRIVATE_KEY_BUCKET_PREFIX = 'private_keys/ssh/'

logger = logging.getLogger(__name__)


class DiscoS3Bucket(object):
    """
    S3 Bucket accessor object. The connection to the bucket is lazily initialized.
    Subsequent requests on the same object reuse the connection.
    """
    def __init__(self, bucket_name):
        self._bucket_name = bucket_name
        self._bucket = None  # lazily initialiazed

    @property
    def bucket(self):
        '''Lazily initialized boto.s3.bucket instance'''
        if not self._bucket:
            connection = S3Connection()  # by default will use access key id and secret from ~/.boto
            self._bucket = connection.get_bucket(self._bucket_name)
            logger.debug("established connection to bucket '%s'", self._bucket_name)
        return self._bucket

    def listkeys(self, prefix_keys):
        '''Returns all keys in bucket matching the prefix'''
        return [k.name for k in self.bucket.get_all_keys(prefix=prefix_keys)]

    def get_key(self, key_name):
        '''Returns the content of a bucket as a string'''
        key = self.bucket.get_key(key_name)
        if key:
            return key.get_contents_as_string()
        else:
            raise KeyError("%s does not exist in bucket %s" % (key_name, self.bucket.name))

    def key_exists(self, key_name):
        '''Returns True if a key exists in the bucket'''
        return self.bucket.get_key(key_name)

    def load_config(self, key):
        """Deserializes ConfigParser from the contents of the specified key's value"""
        config = ConfigParser()
        contents = StringIO()
        key.get_contents_to_file(contents)
        contents.seek(0)
        config.readfp(contents)
        contents.close()
        return config

    def save_config(self, key, config):
        """Serializes ConfigParser to the specified key's value"""
        contents = StringIO()
        config.write(contents)
        contents.seek(0)
        try:
            bytes_written = key.set_contents_from_file(contents)
        except S3ResponseError as err:
            raise S3WritingError("Failed to write to {}: {}".format(key, err.message))
        check_written_s3(key, contents.len, bytes_written)
        contents.close()

    def set_key(self, key_name, key_value):
        """Sets the contents of a key to a the specified value"""
        k = self.bucket.new_key(key_name)
        bytes_written = k.set_contents_from_string(key_value, encrypt_key=True)
        check_written_s3(key_name, len(key_value), bytes_written)

    def get_contents_to_file(self, key_name, file_name):
        """Writes the contents of a key's value to the specified file"""
        k = self.bucket.get_key(key_name)
        k.get_contents_to_filename(file_name)

    def delete_key(self, key_name):
        """Removes a key from the bucket"""
        return self.bucket.delete_key(key_name)

    def list(self, *args, **kwargs):
        """
        Lists keys in the bucket (returns BucketListResultSet)

        Accepts same params as boto.s3.bucket.list
        """
        return self.bucket.list(*args, **kwargs)
