#!/usr/bin/env python
"""
Manages ElastiCache

Usage:
    disco_elasticache.py [--debug] [--env ENV] list
    disco_elasticache.py [--debug] [--env ENV] update [--cluster CLUSTER]
    disco_elasticache.py [--debug] [--env ENV] delete --cluster CLUSTER [--wait]
    disco_elasticache.py (-h | --help)

Commands:
    list      List all cache clusters
    update    Update cache clusters
    delete    Delete a cache cluster

Options:
    -h --help           Show this screen
    --debug             Log in debug level
    --env ENV           Environment name (VPC name)
    --cluster CLUSTER   Name of cluster
    --wait              Wait until command completes (may take multiple minutes)
"""
from __future__ import print_function
import sys
from docopt import docopt
from disco_aws_automation import DiscoElastiCache, DiscoVPC, DiscoAWS
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
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
            print("{0:<25} {1:5} {2:>5}".format(cluster['Description'],
                                                cluster['Status'],
                                                size))
    elif args['update']:
        if args['--cluster']:
            disco_elasticache.update(args['--cluster'])
        else:
            disco_elasticache.update_all()

    elif args['delete']:
        disco_elasticache.delete(args['--cluster'], wait=args['--wait'])


if __name__ == "__main__":
    run_gracefully(run)
