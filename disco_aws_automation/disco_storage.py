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

import boto

from .resource_helper import wait_for_state, TimeoutError
from .exceptions import VolumeError

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

    def __init__(self, connection=None):
        self.connection = connection if connection else boto.connect_ec2()

    def is_ebs_optimized(self, instance_type):
        '''Returns true if the instance type is EBS Optimized'''
        return instance_type in EBS_OPTIMIZED

    def get_ephemeral_disk_count(self, instance_type):
        '''Returns number of ephemeral disks available for each instance type'''
        try:
            return EPHEMERAL_DISK_COUNT[instance_type]
        except KeyError:
            logging.warning("EPHEMERAL_DISK_COUNT needs to be updated with this new instance type %s",
                            instance_type)
            return 0

    def get_latest_snapshot(self, hostclass):
        '''Returns latests snapshot that exists for a hostclass, or None if none exists.'''
        snapshots = self.connection.get_all_snapshots(filters={'tag:hostclass': hostclass})
        return max(snapshots, key=lambda snapshot: snapshot.start_time) if snapshots else None

    def wait_for_snapshot(self, snapshot):
        '''Wait for a snapshot to become available'''
        try:
            wait_for_state(snapshot, 'completed', state_attr='status', timeout=TIME_BEFORE_SNAP_WARNING)
        except TimeoutError:
            logging.warning("Waiting for snapshot to become available...")
            wait_for_state(snapshot, 'completed', state_attr='status')
            logging.warning("... done.")

    def create_snapshot_bdm(self, snapshot, iops):
        '''Create a Block Device Mapping for a Snapshot'''
        device = boto.ec2.blockdevicemapping.EBSBlockDeviceType(
            snapshot_id=snapshot.id, size=snapshot.volume_size, delete_on_termination=True)
        if iops:
            device.volume_type = PROVISIONED_IOPS_VOLUME_TYPE
            device.iops = iops
        return device

    def configure_storage(self,
                          ami_id=None,
                          extra_space=None,
                          extra_disk=None,
                          iops=None,
                          ephemeral_disk_count=0):
        ''' Cofigures block device mapping for root partition, extra disk if it's used,
        and the ephemeral disks. Note that EBS volume mapping is no longer being created
        here. The logic for attaching EBS volume has been moved to /etc/asiaq/init/ebs-start.sh '''
        # Pylint thinks this function has too many local variables
        # pylint: disable=R0914

        # disk names only go up to /dev/sdy. /dev/sdz is reserved for EBS volume
        disk_names = ['/dev/sd' + chr(ord('a') + i) for i in range(0, 25)]
        if ami_id:
            ami = self.connection.get_image(ami_id)
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
        zones = self.connection.get_all_zones()
        if not zones:
            raise VolumeError("No availability zones found.  Can't create temporary volume.")
        else:
            zone = zones[0]

            def _destroy_volume(volume, raise_error_on_failure=False):
                if self.connection.delete_volume(volume_id=volume.id):
                    logging.info("Destroyed temporary volume %s", volume.id)
                elif raise_error_on_failure:
                    raise VolumeError("Couldn't destroy temporary volume %s", volume.id)
                else:
                    logging.error("Couldn't destroy temporary volume %s", volume.id)

            try:
                volume = self.connection.create_volume(size=size, zone=zone)
                logging.info("Created temporary volume %s in zone %s.", volume.id, zone.name)
                wait_for_state(volume, 'available', state_attr='status')
                snapshot = volume.create_snapshot()
                snapshot.add_tag('hostclass', hostclass)
                logging.info("Created snapshot %s from volume %s.", snapshot.id, volume.id)
            except Exception:
                _destroy_volume(volume)
                raise
            else:
                _destroy_volume(volume, raise_error_on_failure=True)

    def get_snapshots(self, hostclasses):
        """
        Lists all EBS snapshots associated with a hostclass, sorted by hostclass name and start_time

        :param hostclasses if not None, restrict results to specific hostclasses
        """
        snapshots = self.connection.get_all_snapshots(filters={'tag-key': 'hostclass'})
        if hostclasses:
            snapshots = [snap for snap in snapshots if snap.tags['hostclass'] in hostclasses]
        return sorted(snapshots, key=lambda snapshot: (snapshot.tags['hostclass'], snapshot.start_time))

    def delete_snapshot(self, snapshot_id):
        '''Delete a snapshot by snapshot_id'''
        if self.connection.delete_snapshot(snapshot_id=snapshot_id):
            logging.info("Deleted snapshot %s.", snapshot_id)
        else:
            logging.error("Couldn't delete snapshot %s.")

    def cleanup_ebs_snapshots(self, keep_last_n):
        """
        Removes all but the latest n snapshots for each hostclass

        :param keep_last_n:  The number of snapshots to keep per hostclass.  Must be non-zero.
        """
        if keep_last_n <= 0:
            raise ValueError("You must keep at least one snapshot.")
        else:
            snapshots = self.connection.get_all_snapshots(filters={'tag-key': 'hostclass'})
            snapshots_dict = defaultdict(list)
            for snapshot in snapshots:
                snapshots_dict[snapshot.tags['hostclass']].append(snapshot)
            for hostclass_snapshots in snapshots_dict.values():
                snapshots_to_delete = sorted(hostclass_snapshots,
                                             key=lambda snapshot: snapshot.start_time)[:-keep_last_n]
                for snapshot in snapshots_to_delete:
                    self.delete_snapshot(snapshot.id)
