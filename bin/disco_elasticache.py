#!/usr/bin/env python
"""
Manages ElastiCache

Usage:
    disco_elasticache.py [--debug] [--env ENV] list
    disco_elasticache.py [--debug] [--env ENV] listsnapshots [--cluster CLUSTER]
    disco_elasticache.py [--debug] [--env ENV] update [--cluster CLUSTER [--snapshot SNAPSHOT]]
    disco_elasticache.py [--debug] [--env ENV] update [--cluster CLUSTER [--latestsnapshot]]
    disco_elasticache.py [--debug] [--env ENV] delete --cluster CLUSTER [--wait]
    disco_elasticache.py (-h | --help)

Commands:
    list          List all cache clusters
    listsnapshots List snapshots
    update        Update cache clusters
    delete        Delete a cache cluster

Options:
    -h --help           Show this screen
    --debug             Log in debug level
    --env ENV           Environment name (VPC name)
    --cluster CLUSTER   Name of cluster
    --wait              Wait until command completes (may take multiple minutes)
    --snapshot SNAPSHOT Name of the snapshot
    --latestsnapshot    Use latest available snapshot for cluster
"""
from __future__ import print_function
import sys
from docopt import docopt
from disco_aws_automation import DiscoElastiCache, DiscoVPC, DiscoAWS, read_config
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging


def run():
    """Parses command line and dispatches the commands"""
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    config = read_config()

    env = args.get("--env") or config.get("disco_aws", "default_environment")
    vpc = DiscoVPC.fetch_environment(environment_name=env)
    if not vpc:
        print("Environment does not exist: {}".format(env))
        sys.exit(1)

    aws = DiscoAWS(config, env)
    disco_elasticache = DiscoElastiCache(vpc, aws=aws)

    if args['list']:
        for cluster in disco_elasticache.list():
            size = 'N/A'
            if cluster['Status'] == 'available':
                size = len(cluster['NodeGroups'][0]['NodeGroupMembers'])
            print("{:<25} {:12} {:>5}".format(cluster['Description'], cluster['Status'], size))

    elif args['listsnapshots']:
        print_snapshots(disco_elasticache, args['--cluster'])

    elif args['update']:
        if args['--cluster']:
            snapshot_name = args.get('--snapshot')
            if args['--latestsnapshot']:
                snapshot = disco_elasticache.get_latest_snapshot(args['--cluster'])
                if not snapshot:
                    print('No latest snapshot for cluster "%s" found' % args['--cluster'])
                    sys.exit(1)
                snapshot_name = snapshot['SnapshotName']
            disco_elasticache.update(args['--cluster'], snapshot_name)
        else:
            disco_elasticache.update_all()

    elif args['delete']:
        disco_elasticache.delete(args['--cluster'], wait=args['--wait'])


def print_snapshots(disco_elasticache, cluster_name):
    """print all snapshots or for a specific cluster"""
    rows = []
    for snapshot_data in disco_elasticache.list_snapshots(cluster_name):
        cluster_id = snapshot_data['CacheClusterId']
        name = snapshot_data['SnapshotName']
        status = snapshot_data['SnapshotStatus']
        source = snapshot_data['SnapshotSource']
        for snapshot in snapshot_data['NodeSnapshots']:
            cache_size = snapshot['CacheSize']
            create_time = snapshot['SnapshotCreateTime']
            rows.append((cluster_id, name, cache_size, create_time, status, source))
    for row in sorted(rows, key=lambda x: x[3], reverse=True):
        print("{} {:25} {:>6} {} {:10} {}".format(*row))

if __name__ == "__main__":
    run_gracefully(run)
