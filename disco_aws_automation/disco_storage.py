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

import boto3

from .resource_helper import (
    TimeoutError,
    get_boto3_paged_results,
    wait_for_state_boto3,
    tag2dict,
    dict_to_boto3_tags
)
from .exceptions import VolumeError
from .resource_helper import throttled_call

logger = logging.getLogger(__name__)

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
    "r4.large": 0,
    "r4.xlarge": 0,
    "r4.2xlarge": 0,
    "r4.4xlarge": 0,
    "r4.8xlarge": 0,
    "r4.16xlarge": 0,
    "t1.micro": 0,
    "t2.nano": 0,
    "t2.micro": 0,
    "t2.small": 0,
    "t2.medium": 0,
    "t2.large": 0,
    "t2.xlarge": 0,
    "t2.2xlarge": 0,
    "x1.16xlarge": 1,
    "x1.32xlarge": 2
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
    "r3.4xlarge",
    "r4.large",
    "r4.xlarge",
    "r4.2xlarge",
    "r4.4xlarge",
    "r4.8xlarge",
    "r4.16xlarge"
]


class DiscoStorage(object):
    """
    Wrapper class to handle all DiscoAWS storage functions
    """

    def __init__(self, environment_name, ec2_client=None):
        self.environment_name = environment_name
        self.ec2_client = ec2_client or boto3.client('ec2')

    def is_ebs_optimized(self, instance_type):
        """Returns true if the instance type is EBS Optimized"""
        return instance_type in EBS_OPTIMIZED

    def get_ephemeral_disk_count(self, instance_type):
        """Returns number of ephemeral disks available for each instance type"""
        try:
            return EPHEMERAL_DISK_COUNT[instance_type]
        except KeyError:
            logger.warning("EPHEMERAL_DISK_COUNT needs to be updated with this new instance type %s",
                           instance_type)
            return 0

    def get_latest_snapshot(self, hostclass):
        """Returns latests snapshot that exists for a hostclass, or None if none exists."""
        snapshots = get_boto3_paged_results(
            self.ec2_client.describe_snapshots,
            Filters=[{
                'Name': 'tag:hostclass',
                'Values': [hostclass]
            }, {
                'Name': 'tag:env',
                'Values': [self.environment_name]
            }],
            results_key='Snapshots'
        )

        return max(snapshots, key=lambda snapshot: snapshot['StartTime']) if snapshots else None

    def wait_for_snapshot(self, snapshot):
        """Wait for a snapshot to become available"""
        try:
            wait_for_state_boto3(
                self.ec2_client.describe_snapshots,
                params_dict={
                    'SnapshotIds': [snapshot['SnapshotId']]
                },
                resources_name='Snapshots',
                expected_state='completed',
                state_attr='State',
                timeout=TIME_BEFORE_SNAP_WARNING
            )
        except TimeoutError:
            logger.warning("Waiting for snapshot to become available...")
            wait_for_state_boto3(
                self.ec2_client.describe_snapshots,
                params_dict={
                    'SnapshotIds': [snapshot['SnapshotId']]
                },
                resources_name='Snapshots',
                expected_state='completed',
                state_attr='State'
            )
            logger.warning("... done.")

    def create_snapshot_bdm(self, snapshot, iops):
        """Create a Block Device Mapping for a Snapshot"""
        device = {
            'Ebs': {
                'SnapshotId': snapshot['SnapshotId'],
                'VolumeSize': snapshot['VolumeSize'],
                'DeleteOnTermination': True,
            }
        }
        if iops:
            device['VolumeType'] = PROVISIONED_IOPS_VOLUME_TYPE
            device['Iops'] = iops
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
            amis = throttled_call(
                self.ec2_client.describe_images,
                Filters=[{
                    'Name': 'image-id',
                    'Values': [ami_id]
                }]
            ).get('Images', [])
            if not amis:
                raise VolumeError("Cannot locate AMI to base the BDM of. Is it available to the account?")
            ami = amis[0]
            block_device_mappings = ami.get('BlockDeviceMappings', [])
            mappings_by_name = {}
            for mapping in block_device_mappings:
                mappings_by_name[mapping['DeviceName']] = mapping

            disk_names[0] = '/dev/sda' if '/dev/sda' in mappings_by_name else ami['RootDeviceName']
        # ^ See http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/block-device-mapping-concepts.html
        current_disk = 0
        bdm = []

        # Map root partition
        sda = {
            'DeviceName':  disk_names[current_disk],
            'Ebs': {
                'DeleteOnTermination': True
            }
        }
        if extra_space:
            sda['Ebs']['VolumeSize'] = BASE_AMI_SIZE_GB + extra_space  # size in Gigabytes
        bdm.append(sda)
        logger.debug("mapped %s to root partition", disk_names[current_disk])
        current_disk += 1

        # Map the latest snapshot for this hostclass
        if map_snapshot:
            latest = self.get_latest_snapshot(hostclass)
            if latest:
                self.wait_for_snapshot(latest)
                current_name = disk_names[current_disk]
                snapshot_bdm = self.create_snapshot_bdm(latest, iops)
                snapshot_bdm['DeviceName'] = current_name
                bdm.append(snapshot_bdm)
                logger.debug("mapped %s to snapshot %s", current_name, latest['SnapshotId'])
                current_disk += 1

        # Map extra disk
        if extra_disk:
            extra = {
                'DeviceName': disk_names[current_disk],
                'Ebs': {
                    'DeleteOnTermination': True,
                    'VolumeSize': extra_disk
                }
            }
            if iops:
                extra['Ebs']['VolumeType'] = PROVISIONED_IOPS_VOLUME_TYPE
                extra['Ebs']['VolumeSize'] = iops

            bdm.append(extra)
            logger.debug("mapped %s to extra disk", disk_names[current_disk])
            current_disk += 1

        # Map an ephemeral disk
        for eph_index in range(0, ephemeral_disk_count):
            eph = {
                'DeviceName': disk_names[current_disk],
                'VirtualName': 'ephemeral{0}'.format(eph_index)
            }
            bdm.append(eph)
            logger.debug("mapped %s to ephemeral disk %s", disk_names[current_disk], eph_index)
            current_disk += 1

        return bdm

    def create_ebs_snapshot(self, hostclass, size, product_line, encrypted=True):
        """
        Creates an EBS snapshot in the first listed availability zone.

        Note that this snapshot doesn't contain a filesystem.  Your hostclass
        init must do this before mounting the volume created from this snapshot.

        :param hostclass:  The hostclass that uses this snapshot
        :param size:  The size of the snapshot in GB
        :param product_line: The productline that the hostclass belongs to
        :param encrypted:  Boolean whether snapshot is encrypted
        """
        zones = throttled_call(
            self.ec2_client.describe_availability_zones
        ).get('AvailabilityZones', [])
        if not zones:
            raise VolumeError("No availability zones found.  Can't create temporary volume.")
        else:
            zone = zones[0]

            def _destroy_volume(volume, raise_error_on_failure=False):
                try:
                    throttled_call(
                        self.ec2_client.delete_volume,
                        VolumeId=volume['VolumeId']
                    )
                    logger.info("Destroyed temporary volume %s", volume['VolumeId'])
                except Exception:
                    if raise_error_on_failure:
                        raise VolumeError("Couldn't destroy temporary volume {}".format(volume['VolumeId']))
                    else:
                        logger.error("Couldn't destroy temporary volume %s", volume['VolumeId'])

            volume = None
            try:
                volume = throttled_call(
                    self.ec2_client.create_volume,
                    Size=size,
                    AvailabilityZone=zone['ZoneName'],
                    Encrypted=encrypted
                )
                logger.info("Created temporary volume %s in zone %s.", volume['VolumeId'], zone['ZoneName'])
                wait_for_state_boto3(
                    self.ec2_client.describe_volumes,
                    params_dict={
                        'VolumeIds': [volume['VolumeId']]
                    },
                    resources_name='Volumes',
                    expected_state='available',
                    state_attr='State'
                )
                snapshot = throttled_call(
                    self.ec2_client.create_snapshot,
                    VolumeId=volume['VolumeId'],
                    TagSpecifications=[{
                        'ResourceType': 'snapshot',
                        'Tags': [{
                            'Key': 'hostclass',
                            'Value': hostclass
                        }, {
                            'Key': 'env',
                            'Value': self.environment_name
                        }, {
                            'Key': 'productline',
                            'Value': product_line
                        }]
                    }]
                )
                logger.info("Created snapshot %s from volume %s.", snapshot['SnapshotId'], volume['VolumeId'])
            except Exception:
                if volume:
                    _destroy_volume(volume)
                raise
            else:
                _destroy_volume(volume, raise_error_on_failure=True)

    def get_snapshots(self, hostclasses=None):
        """
        Lists all EBS snapshots associated with a hostclass, sorted by hostclass name and start_time

        :param hostclasses if not None, restrict results to specific hostclasses
        """
        snapshots = get_boto3_paged_results(
            self.ec2_client.describe_snapshots,
            Filters=[{
                'Name': 'tag-key',
                'Values': ['hostclass']
            }, {
                'Name': 'tag:env',
                'Values': [self.environment_name]
            }],
            results_key='Snapshots'
        )
        if hostclasses:
            snapshots = [snap for snap in snapshots if tag2dict(snap['Tags'])['hostclass'] in hostclasses]
        return sorted(snapshots, key=lambda snap: (tag2dict(snap['Tags'])['hostclass'], snap['StartTime']))

    def delete_snapshot(self, snapshot_id):
        """Delete a snapshot by snapshot_id"""
        snapshots = get_boto3_paged_results(
            self.ec2_client.describe_snapshots,
            SnapshotIds=[snapshot_id],
            Filters=[{
                'Name': 'tag:env',
                'Values': [self.environment_name]
            }],
            results_key='Snapshots'
        )

        if not snapshots:
            logger.error("Snapshot ID %s does not exist in environment %s",
                         snapshot_id, self.environment_name)
            return

        try:
            throttled_call(
                self.ec2_client.delete_snapshot,
                SnapshotId=snapshot_id
            )
            logger.info("Deleted snapshot %s.", snapshot_id)
        except Exception:
            logger.error("Couldn't delete snapshot %s.")

    def cleanup_ebs_snapshots(self, keep_last_n):
        """
        Removes all but the latest n snapshots for each hostclass

        :param keep_last_n:  The number of snapshots to keep per hostclass.  Must be non-zero.
        """
        if keep_last_n <= 0:
            raise ValueError("You must keep at least one snapshot.")
        else:
            snapshots = self.get_snapshots()
            snapshots_dict = defaultdict(list)
            for snapshot in snapshots:
                snapshots_dict[tag2dict(snapshot['Tags'])['hostclass']].append(snapshot)
            for hostclass_snapshots in snapshots_dict.values():
                snapshots_to_delete = sorted(hostclass_snapshots,
                                             key=lambda snapshot: snapshot['StartTime'])[:-keep_last_n]
                for snapshot in snapshots_to_delete:
                    self.delete_snapshot(snapshot['SnapshotId'])

    def take_snapshot(self, volume_id, snapshot_tags=None):
        """Takes a snapshot of an attached volume"""
        volume = throttled_call(
            self.ec2_client.describe_volumes,
            VolumeIds=[volume_id]
        ).get('Volumes', [])[0]

        if 'Attachments' in volume and volume['Attachments'][0].get('InstanceId'):
            instance = throttled_call(
                self.ec2_client.describe_instances,
                InstanceIds=[volume['Attachments'][0]['InstanceId']]
            ).get('Reservations')[0]['Instances'][0]

            instance_tags = tag2dict(instance['Tags'])
            tags = {'hostclass': instance_tags['hostclass'],
                    'env': instance_tags['environment'],
                    'productline': instance_tags['productline']}
            if snapshot_tags:
                tags.update(snapshot_tags)
        else:
            raise RuntimeError("The volume specified is not attched to an instance. "
                               "Snapshotting that is not supported.")

        snapshot = throttled_call(
            self.ec2_client.create_snapshot,
            VolumeId=volume['VolumeId'],
            TagSpecifications={
                'ResourceType': 'snapshot',
                'Tags': dict_to_boto3_tags(tags)
            }
        )
        return snapshot['SnapshotId']

    def get_snapshot_from_id(self, snapshot_id):
        """For a given snapshot id return the boto2 snapshot object"""
        return throttled_call(
            self.ec2_client.describe_snapshots,
            SnapshotIds=[snapshot_id]
        ).get('Snapshots', [])[0]
