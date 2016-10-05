"""
Manage archiving of ES clusters
"""

from collections import defaultdict
from time import time, sleep
import datetime
import logging
import re

import boto3
from boto3.session import Session
from elasticsearch import (
    Elasticsearch,
    NotFoundError,
    RequestsHttpConnection,
    TransportError,
)
from requests_aws4auth import AWS4Auth

from . import read_config, DiscoElasticsearch, DiscoIAM
from .disco_aws_util import is_truthy
from .exceptions import TimeoutError
from .disco_constants import ES_CONFIG_FILE
from .resource_helper import keep_trying

logger = logging.getLogger(__name__)

ES_SNAPSHOT_FINAL_STATES = ['SUCCESS', 'FAILED']
SNAPSHOT_WAIT_TIMEOUT = 60 * 20
SNAPSHOT_POLL_INTERVAL = 60
OPS_TIMEOUT = 60 * 5


# Some methods in elasticsearch use annotations to process keyword arguments
# pylint: disable=unexpected-keyword-arg
class DiscoESArchive(object):
    """
    Implements archiving, grooming and restoring of ES clusters
    """
    def __init__(self, environment_name, cluster_name, config_aws=None,
                 config_es=None, disco_es=None,
                 disco_iam=None):
        self.config_aws = config_aws or read_config()
        self.config_es = config_es or read_config(ES_CONFIG_FILE)

        if environment_name:
            self.environment_name = environment_name.lower()
        else:
            self.environment_name = self.config_aws.get("disco_aws", "default_environment")
        self.cluster_name = cluster_name

        self._index_prefix_pattern = self.get_es_option('archive_index_prefix_pattern')
        self._repository_name = self.get_es_option('archive_repository')

        self._host = None
        self._es_client = None
        self._region = None
        self._s3_client = None
        self._s3_bucket_name = None
        self._disco_es = disco_es
        self._disco_iam = disco_iam

    @property
    def host(self):
        """
        Return ES cluster hostname
        """
        if not self._host:
            es_domains = self.disco_es.list()

            try:
                host = [es_domain["route_53_endpoint"]
                        for es_domain in es_domains
                        if es_domain.get("internal_name") == self.cluster_name][0]
            except IndexError:
                raise RuntimeError("Unable to find ES cluster: {}".format(self.cluster_name))

            use_ssl = is_truthy(self.get_es_option('api_use_ssl'))
            self._host = {'host': host, 'port': 443 if use_ssl else 80}
        return self._host

    @property
    def region(self):
        """
        Current region, based of boto connection.
        """
        if not self._region:
            self._region = Session().region_name
        return self._region

    @property
    def role_arn(self):
        """
        Return aws role_arn
        """
        es_archive_role = self.get_es_option('archive_role')
        arn = self.disco_iam.get_role_arn(es_archive_role)
        if not arn:
            raise RuntimeError("Unable to find ARN for role: {0}".format(es_archive_role))

        return arn

    @property
    def es_client(self):
        """
        Return authenticated ElasticSearch connection object
        """
        if not self._es_client:
            session = Session()
            credentials = session.get_credentials()
            aws_auth = AWS4Auth(
                credentials.access_key,
                credentials.secret_key,
                self.region,
                'es',
                session_token=credentials.token
            )

            self._es_client = Elasticsearch(
                [self.host],
                http_auth=aws_auth,
                use_ssl=is_truthy(self.get_es_option('api_use_ssl')),
                timeout=OPS_TIMEOUT,  # without this, requests would timeout after 10 seconds
                verify_certs=is_truthy(self.get_es_option('api_verify_certs')),
                connection_class=RequestsHttpConnection,
            )
        return self._es_client

    @property
    def disco_es(self):
        """
        Returns a DiscoElasticsearch object
        """
        if not self._disco_es:
            self._disco_es = DiscoElasticsearch(self.environment_name)

        return self._disco_es

    @property
    def disco_iam(self):
        """
        Returns a DiscoIAM object
        """
        if not self._disco_iam:
            self._disco_iam = DiscoIAM()

        return self._disco_iam

    @property
    def s3_client(self):
        """
        Returns a boto3 S3 client object
        """
        if not self._s3_client:
            self._s3_client = boto3.client("s3")

        return self._s3_client

    @property
    def s3_bucket_name(self):
        """
        Returns name of the S3 bucket that is used for archiving
        """
        if not self._s3_bucket_name:
            self._s3_bucket_name = "{0}.es-{1}-archive.{2}".format(
                self.region, self.cluster_name,
                self.environment_name)

        return self._s3_bucket_name

    def _bytes_to_free(self, cluster_stats, threshold):
        """
        Number of bytes disk space threshold is exceeded by. In other
        words, number of bytes of data that needs to be deleted to drop
        below threshold.
        threshold range 0-1
        """
        total_bytes = cluster_stats["nodes"]["fs"]["total_in_bytes"]
        free_bytes = cluster_stats["nodes"]["fs"]['free_in_bytes']
        need_bytes = total_bytes * (1 - threshold)
        logger.debug("disk utilization : %i%%", (total_bytes - free_bytes) * 100 / total_bytes)
        return need_bytes - free_bytes

    def _shards_to_delete(self, cluster_stats, max_shards):
        """
        Return number of shards to delete in order to keep the number of shards in the
        cluster under max_shards.
        """
        return cluster_stats['indices']['shards']['total'] - max_shards

    def _indices_to_delete(self, threshold, max_shards):
        """
        Return list of indices that can be deleted so that used disk space would get
        below the threshold and the number of shards would be under max_shards.
        Oldest first.
        """
        cluster_stats = self.es_client.cluster.stats()

        bytes_to_free = self._bytes_to_free(cluster_stats, threshold)
        shards_to_delete = self._shards_to_delete(cluster_stats, max_shards)

        logger.debug("Need to free %i bytes.", bytes_to_free)
        logger.debug("Need to delete %i shards.", shards_to_delete)
        if bytes_to_free <= 0 and shards_to_delete <= 0:
            return []

        index_stats = self._get_all_indices_stats()
        indices_to_delete = []
        freed_size = 0
        deleted_shards = 0
        for index_stat in index_stats:
            if freed_size >= bytes_to_free and deleted_shards >= shards_to_delete:
                break

            if self.snapshot_state(index_stat['index']) == 'SUCCESS':
                indices_to_delete.append(index_stat['index'])
                freed_size += index_stat['size']
                deleted_shards += index_stat['shards']

        return indices_to_delete

    def _archivable_indices(self):
        """ Return list of indices that are older than yesterday's date """
        # We don't want to return today's index because it might not be complete yet.
        # We don't want to return yesterday's index because that ensures we don't run into
        # the time zone issue dealing with finding today's date.
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y.%m.%d')

        return [index['index']
                for index in self._get_all_indices_stats()
                if index['index'][-10:] < yesterday_str]

    def _all_index_names(self):
        """ Return list of all indices """
        return [index['index']
                for index in self._get_all_indices_stats()]

    def _get_all_indices_stats(self):
        """
        Return list of all the indices, their sizes and their numbers of shards
        sorted by the date from oldest to latest
        """
        cat_stats = self.es_client.cat.indices(bytes='b', h='index,pri,rep,store.size')
        indices_stats = []
        for stats_line in cat_stats.split('\n'):
            if stats_line:
                stats = stats_line.split()

                # It appears that store.size is not returned when the index is red.
                # Defaulting the index size to 0 when that happens
                index_size = int(stats[3]) if len(stats) == 4 else 0

                indices_stats.append({'index': str(stats[0]),
                                      'size': index_size,
                                      'shards': int(stats[1]) * (int(stats[2]) + 1)})

        dated_index_pattern = re.compile(
            self._index_prefix_pattern + r'-[\d]{4}(\.[\d]{2}){2}$'
        )

        indices_stats = [index_stats
                         for index_stats in sorted(indices_stats, key=lambda k: k['index'][-10:])
                         if re.match(dated_index_pattern, index_stats['index'])]

        return indices_stats

    def _green_indices(self):
        """
        Return list of indexes that are in green state (healthy)
        """
        index_health = self.es_client.cluster.health(level='indices')
        return [
            index
            for index in index_health['indices'].keys()
            if index_health['indices'][index]['status'] == 'green'
        ]

    def _create_repository(self):
        """
        Creates a s3 type repository where archives can be stored
        and retrieved.
        """

        # Make sure S3 bucket used for archival is available.
        buckets = self.s3_client.list_buckets()['Buckets']
        if self.s3_bucket_name not in [bucket['Name'] for bucket in buckets]:
            raise RuntimeError("Couldn't find S3 bucket (%s) for archiving cluster (%s).",
                               self.s3_bucket_name,
                               self.cluster_name)

        repository_config = {
            "type": self._repository_name,
            "settings": {
                "bucket": self.s3_bucket_name,
                "region": self.region,
                "role_arn": self.role_arn,
            }
        }

        logger.info("Creating ES snapshot repository: %s", repository_config)
        try:
            self.es_client.snapshot.create_repository(self._repository_name, repository_config)
        except TransportError:
            try:
                self.es_client.snapshot.get_repository(self._repository_name)
                # This can happen when repo is being used to make snapshot but we
                # attempt to re-create it.
                logger.warning(
                    "Error on creating ES snapshot respository %s, "
                    "but one already exists, ingnoring",
                    self._repository_name
                )
            except:
                logger.error(
                    "Could not create archive repository. "
                    "Make sure bucket %s exists and IAM policy allows es to access bucket. "
                    "repository config: %s",
                    self.s3_bucket_name,
                    repository_config,
                )
                raise

    def get_es_option(self, option):
        """Returns appropriate configuration for the current environment"""
        section = "{}:{}".format(self.environment_name, self.cluster_name)

        if self.config_es.has_option(section, option):
            return self.config_es.get(section, option)
        elif self.config_es.has_option('defaults', option):
            # Get option from defaults section if it's not found in the cluster's section
            return self.config_es.get('defaults', option)

        raise RuntimeError("Could not find option, %s, in either the %s and the defaults sections "
                           "of the Disco ElasticSearch config.",
                           option, section)

    def archive(self, dry_run=False):
        """
        Archive all the indices, other than the latest one, that have not already been archived.
        Archiving an index doesn't include deleting it from the cluster.
        """
        # Initialize snapshot states for return
        snap_states = defaultdict(list)
        snap_states['SUCCESS'] = []

        indices = self._archivable_indices()
        green_indices = self._green_indices()
        green_archivable = set(indices) & set(green_indices)
        ungreen_archivable = set(indices) - set(green_indices)
        if ungreen_archivable:
            logger.error(
                "Skipping archiving of following unhealthy indexes: %s",
                ", ".join(ungreen_archivable)
            )
        if not green_archivable:
            logger.warning("No indices to archive.")
            return snap_states

        self._create_repository()

        snap_states['skipped'] = list(ungreen_archivable)

        for index in green_archivable:
            snap_state = self.snapshot_state(index)
            if snap_state == 'FAILED':
                logger.info(
                    "Deleting the falied snapshot for index (%s) so that it can be archived again.",
                    index)
                if not dry_run:
                    self.es_client.snapshot.delete(repository=self._repository_name,
                                                   snapshot=index)
            elif snap_state != 'unknown':
                logger.info(
                    'Index (%s) was already archived.',
                    index
                )
                snap_states['existed'].append(index)
                continue

            logger.info("Archiving index: %s", index)
            if not dry_run:
                try:
                    self.es_client.snapshot.create(
                        repository=self._repository_name,
                        snapshot=index,
                        body={
                            "indices": index,
                            "settings": {
                                "role_arn": self.role_arn
                            }
                        },
                        wait_for_completion=True
                    )
                except TransportError:
                    # This happens when the snapshot is taking a while to complete. Based on
                    # observations, it's about 60 seconds.
                    pass

                snap_state = self._wait_for_snapshot(index)

                if snap_state != 'SUCCESS':
                    self.es_client.snapshot.delete(self._repository_name, index)

                snap_states[snap_state].append(index)
            else:
                # During dry run, assume all snapshots are created successfully
                snap_states['SUCCESS'].append(index)

        return snap_states

    def groom(self, dry_run=False):
        """
        Delete enough indices from the cluster such that used disk space would get below
        the threshold and the number of shards would be under the specified max number
        of shards
        """
        threshold = float(self.get_es_option("archive_threshold"))
        if threshold > 1.0 or threshold <= .0:
            raise RuntimeError("ElasticSearch archive threshold must be between 0 and 1.0.")

        max_shards = int(self.get_es_option("archive_max_shards"))
        if max_shards <= 0:
            raise RuntimeError("ElasticSearch max shards must be greater than 0.")

        logger.info(
            "Using archive threshold: %s, and max shards: %s",
            threshold, max_shards
        )
        indices_to_delete = self._indices_to_delete(threshold, max_shards)

        if indices_to_delete:
            for index in indices_to_delete:
                logger.info(
                    "Deleting index (%s) from cluster (%s).",
                    index, self.cluster_name
                )
                if not dry_run:
                    self.es_client.indices.delete(index=index)
        else:
            logger.info("No need to delete any indices.")

    def snapshots(self, snapshots=None):
        """
        List snapshots their state & etc.
        """
        snaps = self.es_client.snapshot.get(self._repository_name, snapshots or '_all')
        return snaps['snapshots']

    def snapshot_state(self, snapshot):
        """
        Return state of specified snapshot or 'unknown'.
        """
        try:
            for snap in self.snapshots(snapshot):
                if snap['snapshot'] == snapshot:
                    return snap['state']
        except NotFoundError:
            pass
        return 'unknown'

    def _wait_for_snapshot(self, snapshot):
        """
        Wait for specified snapshots to complete snapshotting process.
        """
        max_time = time() + SNAPSHOT_WAIT_TIMEOUT

        # Keep trying because sometimes TransportError could be thrown if request is taking too long
        snap_state = keep_trying(SNAPSHOT_WAIT_TIMEOUT, self.snapshot_state, snapshot)

        while snap_state not in ES_SNAPSHOT_FINAL_STATES:
            logger.info(
                "Snapshot (%s) has not reached final states. Going to wait for %s seconds.",
                snapshot, SNAPSHOT_POLL_INTERVAL
            )
            sleep(SNAPSHOT_POLL_INTERVAL)
            now = time()
            if now > max_time:
                raise TimeoutError(
                    "Timed out ({0}s) waiting for {1} to enter final state."
                    .format(max_time - now, snapshot)
                )
            snap_state = keep_trying(SNAPSHOT_WAIT_TIMEOUT, self.snapshot_state, snapshot)

        return snap_state

    def restore(self, begin_date, end_date, dry_run=False):
        """
        Bring back indices within the specified date range (inclusive) from archive to ES cluster
        """
        self._create_repository()
        date_pattern = re.compile(r'^[\d]{4}(\.[\d]{2}){2}$')
        if not re.match(date_pattern, begin_date):
            raise RuntimeError("Invalid begin date (yyyy.mm.dd): {0}".format(begin_date))
        if not re.match(date_pattern, end_date):
            raise RuntimeError("Invalid end date (yyyy.mm.dd): {0}".format(end_date))

        snapshots = []
        failed_snapshots = []
        for snap in self.snapshots():
            if snap['snapshot'][-10:] >= begin_date and snap['snapshot'][-10:] <= end_date:
                if snap['state'] == 'SUCCESS':
                    snapshots.append(snap['snapshot'])
                else:
                    failed_snapshots.append(snap['snapshot'])

        logger.debug("Snapshots within the specified date range: %s", snapshots)
        if failed_snapshots:
            logger.warning(
                "Failed snapshots were found within the specified date range: %s",
                failed_snapshots
            )

        if not snapshots:
            logger.info("No snapshots within date range are found.")
            return

        existing_indices = self._all_index_names()
        logger.debug(
            "Existing indices in the cluster: %s",
            existing_indices
        )
        snapshots = set(snapshots) - set(existing_indices)
        if not snapshots:
            logger.info("All snapshots within date range are already present as indices.")
            return

        for snap in snapshots:
            logger.info("Restoring snapshot: %s", snap)
            if not dry_run:
                self.es_client.snapshot.restore(
                    repository=self._repository_name,
                    snapshot=snap,
                    wait_for_completion=True
                )
