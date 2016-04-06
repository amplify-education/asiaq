#!/usr/bin/env python
"""
Manages ElasticSearch

Usage:
    disco_elasticsearch.py [--debug] list [--endpoint]
    disco_elasticsearch.py [--debug] [--env ENV] create
    disco_elasticsearch.py [--debug] [--env ENV] update
    disco_elasticsearch.py [--debug] [--env ENV] delete
    disco_elasticsearch.py (-h | --help)

Commands:
    list      List all elasticsearch domains
    create    Creates an elasticsearch domain
    update    Update elasticsearch domain configuration
    delete    Delete an elasticsearch domain

Options:
    -h --help           Show this screen
    --debug             Log in debug level
    --endpoint          Display elasticsearch service endpoint
    --env ENV           Environment name (build, ci, etc.)
"""
from __future__ import print_function
from docopt import docopt
from disco_aws_automation import DiscoES, DiscoAWS, read_config
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging


def run():
    """Parses command line and dispatches the commands"""
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    config = read_config()
    env = args['--env']
    aws = DiscoAWS(config, env)
    disco_elasticsearch = DiscoES(config, aws)

    if args['list']:
        for domain in disco_elasticsearch.list():
            if args['--endpoint']:
                try:
                    endpoint = disco_elasticsearch.get_endpoint(domain)
                except KeyError:
                    endpoint = None
                print('{0:20}\t{1}'.format(domain, endpoint))
            else:
                if not disco_elasticsearch._describe_es_domain(domain)['DomainStatus']['Deleted']:
                    print(domain)

    elif args['create']:
        disco_elasticsearch.create()

    elif args['update']:
        disco_elasticsearch.update()

    elif args['delete']:
        disco_elasticsearch.delete()

if __name__ == "__main__":
    run_gracefully(run)