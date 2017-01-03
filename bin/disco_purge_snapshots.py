#!/usr/bin/env python
"""
Disco Snapshot Purger.

When AMIs are created or Volumes are copied AWS creates an object called a
snapshot. It is a reference to the new volume. Deleting the corresponding
vsdolume does not delete the snapshot pointer and so they can build up.

This script cleans up snapshots that we no longer need. Including snapshots
that:
    - Created by CreateImage process but AMI is no longer available
    - have no tags

Usage:
    disco_purge_snapshots.py [options]

Options:
    -h --help                 Show this screen.
    --debug                   Log in debug level.
    --stray-ami               Purge only snapshots created by CreateImage
    --no-metadata             Purge only snapshots with no tags
    --old                     Purge only old snapshots (100 days) [DEPRECATED: use --keep-days instead]
    --keep-days DAYS          Delete snapshots older than this number of days
    --keep-num NUM            Keep at least this number of snapshots per hostclass per env
    --dry-run                 Only print what will be done
"""

from __future__ import print_function
import re
from datetime import datetime
import sys

import boto
from boto.exception import EC2ResponseError
from docopt import docopt
import iso8601
import pytz

from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_logging import configure_logging

OLD_IMAGE_DAYS = 100
DEFAULT_KEEP_LAST = 5
NOW = datetime.now(pytz.UTC)


def run():
    """
    Main
    """
    args = docopt(__doc__)

    configure_logging(args["--debug"])

    # If no options are set, we assume user wants all of 'em.
    arg_options = ["--stray-ami", "--no-metadata",
                   "--keep-days", OLD_IMAGE_DAYS,
                   "--keep-num", DEFAULT_KEEP_LAST]
    if not any([args[option] for option in arg_options if option in args]):
        args = docopt(__doc__, argv=arg_options)

    _ignore, failed_to_purge = purge_snapshots(args)
    if failed_to_purge:
        sys.exit(1)


def purge_snapshots(options):
    """
    Purge snapshots we consider no longer worth keeping
    """
    ec2_conn = boto.connect_ec2()
    snap_pattern = re.compile(
        r"Created by CreateImage\(i-[a-f0-9]+\) for ami-[a-f0-9]+"
    )
    ami_pattern = re.compile(r"ami-[a-f0-9]+")

    images_by_id = {
        image.id: image for image in ec2_conn.get_all_images(owners=['self'])
    }

    snaps_to_purge = []
    failed_to_purge = []
    snapshot_dict = {}

    for snap in ec2_conn.get_all_snapshots(owner='self'):
        if snap_pattern.search(snap.description):
            # snapshots for existing AMIs can't be deleted
            # get the AMI id from the description if there is one
            image_id = ami_pattern.search(snap.description).group(0)

            # skip snapshots that are in use by AMIs
            if image_id in images_by_id:
                continue

            # if the snapshot is not in use by an AMI but has an AMI id then its a stray snapshot
            elif options["--stray-ami"]:
                print("Deleting stray ami snapshot: {0}".format(snap.id))
                snaps_to_purge.append(snap)
                continue

        if options["--no-metadata"] and not snap.description and not snap.tags:
            print("Deleting no-metadata snapshot: {0}".format(snap.id))
            snaps_to_purge.append(snap)
            continue

        # build a dict of hostclass+environment to a list of snapshots
        # use this dict for the --keep-num option to know how many snapshots are there for each hostclass
        if snap.tags and snap.tags.get('hostclass') and snap.tags.get('env'):
            key_name = snap.tags.get('hostclass') + '_' + snap.tags.get('env')
            hostclass_snapshots = snapshot_dict.setdefault(key_name, [])
            hostclass_snapshots.append(snap)

        if options["--old"] or options["--keep-days"]:
            old_days = int(options.get('--keep-days') or OLD_IMAGE_DAYS)

            snap_date = iso8601.parse_date(snap.start_time)
            snap_days_old = (NOW - snap_date).days
            if snap_days_old > old_days:
                print("Deleting old ({1} > {2} days) snapshot: {0}".format(
                    snap.id, snap_days_old, old_days))
                snaps_to_purge.append(snap)
                continue

        print(
            "skipping snapshot: {0} description: {1} tags: {2}"
            .format(snap.id, snap.description, snap.tags)
        )

    if options.get('--keep-num'):
        snaps_to_purge = remove_kept_snapshots(snaps_to_purge, int(options.get('--keep-num')), snapshot_dict)

    if not options["--dry-run"]:
        for snap in snaps_to_purge:
            try:
                snap.delete()
            except EC2ResponseError:
                failed_to_purge.append(snap)
                print("Failed to purge snapshot: {0}".format(snap.id))

    return (snaps_to_purge, failed_to_purge)


def remove_kept_snapshots(snaps_to_purge, keep_count, snapshot_dict):
    """
    Return a new list of snapshots to purge after making sure at least "keep_count"
    snapshots are kept for each hostclass in each environment
    """
    if keep_count < 1:
        raise ValueError("The number of snapshots to keep must be greater than 1 for --keep-num")
    snaps_to_keep = []
    for hostclass_snapshots in snapshot_dict.values():
        keep_for_hostclass = sorted(hostclass_snapshots,
                                    key=lambda snap: iso8601.parse_date(snap.start_time))[-keep_count:]
        snaps_to_keep.extend(keep_for_hostclass)

        snap_ids = ', '.join([snap.id for snap in keep_for_hostclass])
        hostclass = keep_for_hostclass[0].tags['hostclass']
        env = keep_for_hostclass[0].tags['env']

        print(
            "Keeping last %s snapshots (%s) for hostclass %s in environment %s" %
            (keep_count, snap_ids, hostclass, env)
        )

    # remove the snapshots we plan to keep from purge list
    return [snap for snap in snaps_to_purge if snap not in snaps_to_keep]


if __name__ == "__main__":
    run_gracefully(run)
