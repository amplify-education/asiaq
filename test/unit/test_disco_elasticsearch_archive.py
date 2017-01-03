"""
Tests of disco_elasticache_archive
"""
import datetime

from unittest import TestCase
from mock import MagicMock, call
from disco_aws_automation import DiscoESArchive
from test.helpers.patch_disco_aws import get_mock_config


ENVIRONMENT = "foo"
CLUSTER_NAME = "logs"
REPOSITORY_NAME = "s3"
REGION = "us-west-2"
ES_ARCHIVE_ROLE = "es_archive"
ES_ARCHIVE_ROLE_ARN = "arn:aws:iam::640972706174:role/es_archive"
BUCKET_NAME = "{0}.es-{1}-archive.{2}".format(REGION, CLUSTER_NAME, ENVIRONMENT)
MOCK_POLICY_TEXT = 'mock policy text'
TOTAL_SIZE = 6000

REPOSITORY_CONFIG = {
    "type": REPOSITORY_NAME,
    "settings": {
        "bucket": BUCKET_NAME,
        "region": REGION,
        "role_arn": ES_ARCHIVE_ROLE_ARN}}

MOCK_AWS_CONFIG_DEFINITION = {
    "disco_aws": {
        "default_environment": ENVIRONMENT,
    }
}

MOCK_ES_CONFIG_DEFINITION = {
    "foo:logs": {
        "archive_threshold": ".9",
        "archive_max_shards": "60",
        "archive_index_prefix_pattern": ".*",
        "archive_repository": REPOSITORY_NAME,
        "archive_role": ES_ARCHIVE_ROLE}}


def _create_mock_disco_es():
    disco_es = MagicMock()
    disco_es.list.return_value = [{'route_53_endpoint': 'es-logs-foo.aws.wgen.net',
                                   'internal_name': CLUSTER_NAME}]

    return disco_es


def _create_mock_disco_iam():
    disco_iam = MagicMock()
    disco_iam.get_role_arn.return_value = ES_ARCHIVE_ROLE_ARN

    return disco_iam


# pylint: disable=unused-argument
def _create_mock_es_client(indices, indices_health, snapshots, total_bytes):
    es_client = MagicMock()
    es_client.indices = MagicMock()

    def _mock_cat_indices(**args):
        if args != {'h': 'index,pri,rep,store.size', 'bytes': 'b'}:
            raise RuntimeError("Invalid arguments: {0}. This method mocks the indices method of "
                               "elasticsearch.client.CatClient and only returns the name, size "
                               "(in bytes), primary shards and replication factor of the "
                               "indices.".format(args))

        stats_str = ""
        for index in indices:
            stats_str += index['index'] + " " + str(index['pri']) + " " + \
                str(index['rep']) + " " + \
                (str(index['size']) if index['size'] else "") + "\n"

        return stats_str

    def _mock_indices_health(**args):
        return {'indices': indices_health}

    def _mock_cluster_stats():
        bytes_used = 0
        shards = 0
        for index in indices:
            bytes_used += index['size'] if index['size'] else 0
            shards += index['pri'] * (index['rep'] + 1)

        return {"nodes": {"fs": {"total_in_bytes": total_bytes,
                                 "free_in_bytes": total_bytes - bytes_used}},
                "indices": {"shards": {"total": shards}}}

    def _mock_snapshot_get(repository, snapshot):
        if snapshot == "_all":
            snapshot_names = [snap for snap in snapshots.keys()]
        else:
            snapshot_names = snapshot.split(',')

        return {'snapshots': [{'snapshot': snap, 'state': snapshots[snap]['state']}
                              for snap in snapshots.keys()
                              if snap in snapshot_names]}

    def _mock_snapshot_create(repository, snapshot, body, wait_for_completion):
        snapshots[snapshot] = {'state': 'SUCCESS'}

    es_client.cat = MagicMock()
    es_client.cat.indices.side_effect = _mock_cat_indices

    es_client.cluster = MagicMock()
    es_client.cluster.health.side_effect = _mock_indices_health
    es_client.cluster.stats.side_effect = _mock_cluster_stats

    es_client.snapshot = MagicMock()
    es_client.snapshot.get.side_effect = _mock_snapshot_get
    es_client.snapshot.create.side_effect = _mock_snapshot_create
    es_client.snapshot.restore = MagicMock()

    return es_client


class DiscoESArchiveTests(TestCase):
    """Test DiscoEDArchive"""

    def setUp(self):
        config_aws = get_mock_config(MOCK_AWS_CONFIG_DEFINITION)
        config_es = get_mock_config(MOCK_ES_CONFIG_DEFINITION)

        # Config back end objects
        today = datetime.date.today().strftime('%Y.%m.%d')
        self._indices = [
            {'index': 'foo-2016.06.01', 'size': 1000, 'pri': 5, 'rep': 1},
            {'index': 'foo-2016.06.02', 'size': 2000, 'pri': 5, 'rep': 1},
            {'index': 'foo-2016.06.03', 'size': 1000, 'pri': 5, 'rep': 1},
            # Adding an index without a size property to simulate a red index
            {'index': 'foo-2016.06.04', 'size': None, 'pri': 5, 'rep': 1},
            {'index': 'foo-2016.06.05', 'size': 1000, 'pri': 5, 'rep': 1},
            {'index': 'foo-' + today, 'size': 401, 'pri': 5, 'rep': 1}
            # Current used size is 5401, one more than the 0.9 * 6000 (threshold * TOTAL_SIZE)
        ]
        self._indices_health = {
            'foo-2016.06.01': {'status': 'green'},
            'foo-2016.06.02': {'status': 'green'},
            'foo-2016.06.03': {'status': 'green'},
            'foo-2016.06.04': {'status': 'red'},
            'foo-2016.06.05': {'status': 'green'},
            'foo-' + today: {'status': 'green'}
        }
        self._snapshots = {
            'foo-2016.06.01': {'state': 'SUCCESS'},
            'foo-2016.06.02': {'state': 'SUCCESS'},
            'foo-2016.06.03': {'state': 'FAILED'},
            'foo-2016.06.06': {'state': 'SUCCESS'},
            'foo-2016.06.07': {'state': 'SUCCESS'},
        }

        self._disco_es = _create_mock_disco_es()

        # Setting up the DiscoESArchive object
        self._es_archive = DiscoESArchive(environment_name=None,
                                          cluster_name=CLUSTER_NAME,
                                          config_aws=config_aws,
                                          config_es=config_es,
                                          disco_es=self._disco_es,
                                          disco_iam=_create_mock_disco_iam())
        self._es_archive._region = REGION
        self._es_archive._es_client = _create_mock_es_client(
            self._indices, self._indices_health, self._snapshots, TOTAL_SIZE)

        # Mocking out loading policy json files
        self._es_archive._load_policy_json = MagicMock()
        self._es_archive._load_policy_json.return_value = MOCK_POLICY_TEXT

        # Setting up S3 client
        self._es_archive._s3_client = MagicMock()
        self._es_archive._s3_client.list_buckets.return_value = {
            'Buckets': [{'Name': BUCKET_NAME}]
        }

    def test_archive(self):
        """Verify that ES archiving works"""

        # Calling archive for testing
        snap_stats = self._es_archive.archive()

        # Begins verifications
        self.assertEqual(set(snap_stats['existed']),
                         set(['foo-2016.06.01', 'foo-2016.06.02']))
        self.assertEqual(set(snap_stats['skipped']),
                         set(['foo-2016.06.04']))
        self.assertEqual(set(snap_stats['SUCCESS']),
                         set(['foo-2016.06.03', 'foo-2016.06.05']))

        self._es_archive._es_client.snapshot.create_repository.assert_called_once_with(
            REPOSITORY_NAME, REPOSITORY_CONFIG)
        self._es_archive._es_client.snapshot.delete.assert_called_once_with(
            repository=REPOSITORY_NAME, snapshot='foo-2016.06.03')

        expected_create_calls = [call(repository=REPOSITORY_NAME,
                                      snapshot='foo-2016.06.03',
                                      body={"indices": 'foo-2016.06.03',
                                            "settings": {"role_arn": ES_ARCHIVE_ROLE_ARN}},
                                      wait_for_completion=True),
                                 call(repository=REPOSITORY_NAME,
                                      snapshot='foo-2016.06.05',
                                      body={"indices": 'foo-2016.06.05',
                                            "settings": {"role_arn": ES_ARCHIVE_ROLE_ARN}},
                                      wait_for_completion=True)]
        self._es_archive._es_client.snapshot.create.assert_has_calls(expected_create_calls)

    def test_archive_creating_s3_bucket(self):
        """Verify that error is raised if S3 bucket is not available"""
        # Setting up S3 client
        self._es_archive._s3_client = MagicMock()
        self._es_archive._s3_client.list_buckets.return_value = {
            'Buckets': []
        }

        # Calling archive for testing
        with self.assertRaises(RuntimeError):
            self._es_archive.archive()

    def test_groom(self):
        """Verify that ES groom operation works"""
        # Calling groom for testing
        self._es_archive.groom()

        self._es_archive.es_client.indices.delete.assert_called_once_with(index='foo-2016.06.01')

    def test_groom_no_delete(self):
        """Verify no delete due to used space below threshold"""
        # Cause used space to drop below threshold
        self._indices[0]['size'] = 999

        # Calling groom for testing
        self._es_archive.groom()

        self._es_archive.es_client.indices.delete.assert_not_called()

    def test_groom_max_shards_exceeded(self):
        """Verify that delete occurs because max number of shards is exceeded"""
        # Cause max shards to be exceeded
        self._indices[0]['size'] = 999
        self._indices[0]['pri'] = 6

        # Calling groom for testing
        self._es_archive.groom()

        self._es_archive.es_client.indices.delete.assert_called_once_with(index='foo-2016.06.01')

    def test_restore(self):
        """Verify that ES restore operation works"""
        # Calling restore for testing
        self._es_archive.restore('2016.06.01', '2016.06.07')

        expected_restore_calls = [
            call(
                repository=REPOSITORY_NAME,
                snapshot='foo-2016.06.06',
                wait_for_completion=True
            ),
            call(
                repository=REPOSITORY_NAME,
                snapshot='foo-2016.06.07',
                wait_for_completion=True
            )
        ]

        self._es_archive.es_client.snapshot.restore.assert_has_calls(expected_restore_calls)

    def test_restore_date_query(self):
        """Verify that date range query works correctly in ES restore operation"""
        # Calling restore for testing

        self._es_archive.restore('2016.06.01', '2016.06.06')

        self._es_archive.es_client.snapshot.restore.assert_called_once_with(
            repository=REPOSITORY_NAME,
            snapshot='foo-2016.06.06',
            wait_for_completion=True
        )
