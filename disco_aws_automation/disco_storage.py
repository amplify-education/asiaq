"""
Anything related to configuring AWS instance storage goes here.
This includes both ephemeral disks (instance store volumes) and
EBS backed volumes. See
http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/block-device-mapping-concepts.html
for details.

This module also handles EBS snapshot management.  We use EBS
snapshots to backup hostclasses with persistent EBS storage
(just Jenkins right now).
"""

from collections import defaultdict
import logging
from datetime import datetime, timedelta
import dateutil.parser as dateparser
import boto

from .resource_helper import wait_for_state, TimeoutError
from .exceptions import VolumeError
from .resource_helper import throttled_call

TIME_BEFORE_SNAP_WARNING = 5
BASE_AMI_SIZE_GB = 8  # Disk space per instance, in GB, excluding extra_space.
PROVISIONED_IOPS_VOLUME_TYPE = "io1"  # http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSVolumeTypes.html
# see http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/InstanceStorage.html
EPHEMERAL_DISK_COUNT = {
    "c1.medium": 1,
    "c1.xlarge": 4,
    "c3.large": 2,
    "c3.xlarge": 2,
    "c3.2xlarge": 2,
    "c3.4xlarge": 2,
    "c3.8xlarge": 2,
    "c4.large": 0,
    "c4.xlarge": 0,
    "c4.2xlarge": 0,
    "c4.4xlarge": 0,
    "c4.8xlarge": 0,
    "cc2.8xlarge": 4,
    "cg1.4xlarge": 2,
    "cr1.8xlarge": 2,
    "d2.xlarge": 3,
    "d2.2xlarge": 6,
    "d2.4xlarge": 12,
    "d2.8xlarge": 36,
    "g2.2xlarge": 1,
    "g2.8xlarge": 2,
    "hi1.4xlarge": 2,
    "hs1.8xlarge": 24,
    "i2.xlarge": 1,
    "i2.2xlarge": 2,
    "i2.4xlarge": 4,
    "i2.8xlarge": 8,
    "m1.small": 1,
    "m1.medium": 1,
    "m1.large": 2,
    "m1.xlarge": 4,
    "m2.xlarge": 1,
    "m2.2xlarge": 1,
    "m2.4xlarge": 2,
    "m3.medium": 1,
    "m3.large": 1,
    "m3.xlarge": 2,
    "m3.2xlarge": 2,
    "m4.large": 0,
    "m4.xlarge": 0,
    "m4.2xlarge": 0,
    "m4.4xlarge": 0,
    "m4.10xlarge": 0,
    "r3.large": 1,
    "r3.xlarge": 1,
    "r3.2xlarge": 1,
    "r3.4xlarge": 1,
    "r3.8xlarge": 2,
    "t1.micro": 0,
    "t2.nano": 0,
    "t2.micro": 0,
    "t2.small": 0,
    "t2.medium": 0,
    "t2.large": 0,
}

# see http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EBSOptimized.html
EBS_OPTIMIZED = [
    "c1.xlarge",
    "c3.xlarge",
    "c3.2xlarge",
    "c3.4xlarge",
    "c4.large",
    "c4.xlarge",
    "c4.2xlarge",
    "c4.4xlarge",
    "c4.8xlarge",
    "d2.xlarge",
    "d2.2xlarge",
    "d2.4xlarge",
    "d2.8xlarge",
    "g2.2xlarge",
    "i2.xlarge",
    "i2.2xlarge",
    "i2.4xlarge",
    "m1.large",
    "m1.xlarge",
    "m2.2xlarge",
    "m2.4xlarge",
    "m3.xlarge",
    "m3.2xlarge",
    "m4.large",
    "m4.xlarge",
    "m4.2xlarge",
    "m4.4xlarge",
    "m4.10xlarge",
    "r3.xlarge",
    "r3.2xlarge",
    "r3.4xlarge"
]


class DiscoStorage(object):
    """
    Wrapper class to handle all DiscoAWS storage functions
    """

    def __init__(self, environment_name, connection=None):
        self.connection = connection if connection else boto.connect_ec2()
        self.environment_name = environment_name

    def is_ebs_optimized(self, instance_type):
        """Returns true if the instance type is EBS Optimized"""
        return instance_type in EBS_OPTIMIZED

    def get_ephemeral_disk_count(self, instance_type):
        """Returns number of ephemeral disks available for each instance type"""
        try:
            return EPHEMERAL_DISK_COUNT[instance_type]
        except KeyError:
            logging.warning("EPHEMERAL_DISK_COUNT needs to be updated with this new instance type %s",
                            instance_type)
            return 0

    def get_latest_snapshot(self, hostclass):
        """Returns latests snapshot that exists for a hostclass, or None if none exists."""
        snapshots = throttled_call(self.connection.get_all_snapshots,
                                   filters={'tag:hostclass': hostclass,
                                            'tag:env': self.environment_name})
        return max(snapshots, key=lambda snapshot: snapshot.start_time) if snapshots else None

    def wait_for_snapshot(self, snapshot):
        """Wait for a snapshot to become available"""
        try:
            wait_for_state(snapshot, 'completed', state_attr='status', timeout=TIME_BEFORE_SNAP_WARNING)
        except TimeoutError:
            logging.warning("Waiting for snapshot to become available...")
            wait_for_state(snapshot, 'completed', state_attr='status')
            logging.warning("... done.")

    def create_snapshot_bdm(self, snapshot, iops):
        """Create a Block Device Mapping for a Snapshot"""
        device = boto.ec2.blockdevicemapping.EBSBlockDeviceType(
            snapshot_id=snapshot.id, size=snapshot.volume_size, delete_on_termination=True)
        if iops:
            device.volume_type = PROVISIONED_IOPS_VOLUME_TYPE
            device.iops = iops
        return device

    def configure_storage(self,
                          hostclass,
                          ami_id=None,
                          extra_space=None,
                          extra_disk=None,
                          iops=None,
                          ephemeral_disk_count=0,
                          map_snapshot=True):
        """Alter block device to destroy the volume on termination and add any extra space"""
        # Pylint thinks this function has too many local variables
        # pylint: disable=R0914

        # We map disk names starting at /dev/sda, but aws shifts everything after /dev/sda
        # to the right four characters, i.e /dev/sdb becomes /dev/sdf, /dev/sdc becomes /dev/sde
        # and so on.
        # TODO  Figure out how to stop this from happening
        disk_names = ['/dev/sd' + chr(ord('a') + i) for i in range(0, 26)]
        if ami_id:
            ami = throttled_call(self.connection.get_image, ami_id)
            if not ami:
                raise VolumeError("Cannot locate AMI to base the BDM of. Is it available to the account?")
            disk_names[0] = '/dev/sda' if (ami and ami.block_device_mapping and
                                           '/dev/sda' in ami.block_device_mapping) else ami.root_device_name
        # ^ See http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/block-device-mapping-concepts.html
        current_disk = 0
        bdm = boto.ec2.blockdevicemapping.BlockDeviceMapping()

        # Map root partition
        sda = boto.ec2.blockdevicemapping.EBSBlockDeviceType()
        sda.delete_on_termination = True
        if extra_space:
            sda.size = BASE_AMI_SIZE_GB + extra_space  # size in Gigabytes
        bdm[disk_names[current_disk]] = sda
        logging.debug("mapped %s to root partition", disk_names[current_disk])
        current_disk += 1

        # Map the latest snapshot for this hostclass
        if map_snapshot:
            latest = self.get_latest_snapshot(hostclass)
            if latest:
                self.wait_for_snapshot(latest)
                current_name = disk_names[current_disk]
                bdm[current_name] = self.create_snapshot_bdm(latest, iops)
                logging.debug("mapped %s to snapshot %s", current_name, latest.id)
                current_disk += 1

        # Map extra disk
        if extra_disk:
            extra = boto.ec2.blockdevicemapping.EBSBlockDeviceType()
            extra.delete_on_termination = True
            extra.size = extra_disk  # size in Gigabytes
            if iops:
                extra.volume_type = PROVISIONED_IOPS_VOLUME_TYPE
                extra.iops = iops
            bdm[disk_names[current_disk]] = extra
            logging.debug("mapped %s to extra disk", disk_names[current_disk])
            current_disk += 1

        # Map an ephemeral disk
        for eph_index in range(0, ephemeral_disk_count):
            eph = boto.ec2.blockdevicemapping.BlockDeviceType()
            eph.ephemeral_name = 'ephemeral{0}'.format(eph_index)
            bdm[disk_names[current_disk]] = eph
            logging.debug("mapped %s to ephemeral disk %s", disk_names[current_disk], eph_index)
            current_disk += 1

        return bdm

    def create_ebs_snapshot(self, hostclass, size):
        """
        Creates an EBS snapshot in the first listed availability zone.

        Note that this snapshot doesn't contain a filesystem.  Your hostclass
        init must do this before mounting the volume created from this snapshot.

        :param hostclass:  The hostclass that uses this snapshot
        :param size:  The size of the snapshot in GB
        """
        zones = throttled_call(self.connection.get_all_zones)
        if not zones:
            raise VolumeError("No availability zones found.  Can't create temporary volume.")
        else:
            zone = zones[0]

            def _destroy_volume(volume, raise_error_on_failure=False):
                if throttled_call(self.connection.delete_volume, volume_id=volume.id):
                    logging.info("Destroyed temporary volume %s", volume.id)
                elif raise_error_on_failure:
                    raise VolumeError("Couldn't destroy temporary volume %s", volume.id)
                else:
                    logging.error("Couldn't destroy temporary volume %s", volume.id)

            try:
                volume = throttled_call(self.connection.create_volume, size=size, zone=zone)
                logging.info("Created temporary volume %s in zone %s.", volume.id, zone.name)
                wait_for_state(volume, 'available', state_attr='status')
                snapshot = volume.create_snapshot()
                snapshot.add_tag('hostclass', hostclass)
                snapshot.add_tag('env', self.environment_name)
                logging.info("Created snapshot %s from volume %s.", snapshot.id, volume.id)
            except Exception:
                _destroy_volume(volume)
                raise
            else:
                _destroy_volume(volume, raise_error_on_failure=True)

    def get_snapshots(self, hostclasses=None):
        """
        Lists all EBS snapshots associated with a hostclass, sorted by hostclass name and start_time

        :param hostclasses if not None, restrict results to specific hostclasses
        """
        snapshots = throttled_call(self.connection.get_all_snapshots,
                                   filters={'tag-key': 'hostclass',
                                            'tag:env': self.environment_name})
        if hostclasses:
            snapshots = [snap for snap in snapshots if snap.tags['hostclass'] in hostclasses]
        return sorted(snapshots, key=lambda snapshot: (snapshot.tags['hostclass'], snapshot.start_time))

    def delete_snapshot(self, snapshot_id):
        """Delete a snapshot by snapshot_id"""

        snapshots = throttled_call(self.connection.get_all_snapshots,
                                   snapshot_ids=[snapshot_id],
                                   filters={'tag:env': self.environment_name})
        if not snapshots:
            logging.error("Snapshot ID %s does not exist in environment %s",
                          snapshot_id, self.environment_name)
            return

        if throttled_call(self.connection.delete_snapshot, snapshot_id=snapshot_id):
            logging.info("Deleted snapshot %s.", snapshot_id)
        else:
            logging.error("Couldn't delete snapshot %s.")

    def cleanup_ebs_snapshots(self, keep_last_n, keep_last_days=None):
        """
        Removes all snapshots for each hostclass in the environment.
        Guarantees that at least keep_last_n snapshots will be kept.
        keep_last_days may also be specified to keep recently created snapshots in addition to the last n

        :param keep_last_n:  The minimum number of snapshots to keep per hostclass.  Must be non-zero.
        :param keep_last_days:  If specified, any snapshot younger than this will be kept
        """
        if keep_last_n <= 0:
            raise ValueError("You must keep at least one snapshot.")
        else:
            snapshots = self.get_snapshots()
            snapshots_dict = defaultdict(list)
            for snapshot in snapshots:
                snapshots_dict[snapshot.tags['hostclass']].append(snapshot)
            for hostclass_snapshots in snapshots_dict.values():
                snapshots_to_delete = sorted(hostclass_snapshots,
                                             key=lambda snapshot: snapshot.start_time)[:-keep_last_n]

                for snapshot in snapshots_to_delete:
                    snapshot_date = dateparser.parse(snapshot.start_time)

                    if keep_last_days:
                        now = self._get_date_now(timezone=snapshot_date.tzinfo)
                        date_cutoff = now - timedelta(days=keep_last_days)
                        if snapshot_date >= date_cutoff:
                            logging.debug(
                                "Not deleting snapshot %s because it is less than %s days old",
                                snapshot.id, keep_last_days
                            )
                            continue
                    self.delete_snapshot(snapshot.id)

    def _get_date_now(self, timezone):
        """a private method for returning the current date to make mocking easier"""
        return datetime.now(tz=timezone)
