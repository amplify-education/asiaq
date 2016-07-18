"""
AMI bake code."""
from __future__ import print_function
from ConfigParser import NoOptionError
from collections import OrderedDict, defaultdict
from subprocess import check_output
import datetime
import logging
import getpass
import re
import time
from os import path

import boto
import boto.ec2
import boto.exception
import dateutil.parser
from pytz import UTC

from . import normalize_path, read_config
from .resource_helper import wait_for_sshable, keep_trying, wait_for_state
from .disco_storage import DiscoStorage
from .disco_remote_exec import DiscoRemoteExec, SSH_DEFAULT_OPTIONS
from .disco_vpc import DiscoVPC
from .exceptions import CommandError, AMIError, WrongPathError, EarlyExitException
from .disco_constants import DEFAULT_CONFIG_SECTION
from .disco_aws_util import is_truthy

AMI_NAME_PATTERN = re.compile(r"^\w+\s(?:[0-9]+\s)?[0-9]{10,50}")
AMI_TAG_LIMIT = 10


class DiscoBake(object):
    """Class orchestrating baking in AWS"""

    def __init__(self, config=None, connection=None, use_local_ip=False):
        """
        :param config: Configuration object to use.
        :param connection: Boto ec2 connection to use.
        :param use_local_ip: Use local ip of instances for remote exec instead of public.
        """
        if config:
            self._config = config
        else:
            self._config = read_config()

        self.connection = connection or boto.connect_ec2()

        self.disco_storage = DiscoStorage(self.connection)

        self._project_name = self._config.get("disco_aws", "project_name")

        self._disco_remote_exec = None  # lazily initialized
        self._vpc = None  # lazily initialized

        self._use_local_ip = use_local_ip
        self._final_stage = None

    @property
    def vpc(self):
        """Bake VPC"""
        if not self._vpc:
            environment_name = self._config.get("bake", "bakery_environment")
            self._vpc = DiscoVPC.fetch_environment(environment_name=environment_name)
        return self._vpc

    @property
    def disco_remote_exec(self):
        '''Lazily creates a remote execution class'''
        if not self._disco_remote_exec:
            self._disco_remote_exec = DiscoRemoteExec(
                self.vpc.get_credential_buckets(self._project_name))
        return self._disco_remote_exec

    @staticmethod
    def time_diff_in_hours(now, old_time):
        '''Returns the difference between two times in hours (floored)'''
        if not now or not old_time:
            return None
        time_diff = now - old_time
        return int(time_diff.total_seconds() / 60 / 60)

    def pretty_print_ami(self, ami, age_since_when=None, in_prod=False):
        '''Prints an a pretty AMI description to the standard output'''
        name = ami.name
        age_since_when = age_since_when or datetime.datetime.utcnow()
        creation_time = self.get_ami_creation_time(ami)

        if ami.name and AMI_NAME_PATTERN.match(ami.name):
            name = self.ami_hostclass(ami)

        output = "{0:12} {1:<19} {2:<35} {3:<12} {4:<8} {5:<15} {6:<5}".format(
            ami.id,
            str(creation_time),
            name,
            ami.state,
            ami.tags.get("stage", "-"),
            ami.tags.get("productline", "-"),
            DiscoBake.time_diff_in_hours(age_since_when, creation_time),
        )

        if in_prod:
            output += "     prod" if self.is_prod_ami(ami) else " non-prod"

        print(output)

    def option(self, key):
        '''Returns an option from the [bake] section of the disco_aws.ini config file'''
        return self._config.get("bake", key)

    def option_default(self, key, default=None):
        '''Returns an option from the [bake] section of the disco_aws.ini config file'''
        try:
            return self._config.get("bake", key)
        except NoOptionError:
            return default

    def hc_option(self, hostclass, key):
        '''
        Returns an option from the [hostclass] section of the disco_aws.ini config file if it is set,
        otherwise it returns that value from the [bake] section if it is set,
        otherwise it returns that value from the DEFAULT_CONFIG_SECTION if it is set.
        '''
        if self._config.has_option(hostclass, key):
            return self._config.get(hostclass, key)
        elif self._config.has_option("bake", key):
            return self.option(key)
        else:
            return self._config.get(DEFAULT_CONFIG_SECTION, "default_{0}".format(key))

    def hc_option_default(self, hostclass, key, default=None):
        """Fetch a hostclass configuration option if it exists, otherwise return value passed in as default"""
        try:
            return self.hc_option(hostclass, key)
        except NoOptionError:
            return default

    def repo_instance(self):
        """ Return active repo instance, else none """
        # TODO Fix the circular dep between DiscoAWS and DiscoBake so we don't have to do this
        from .disco_aws import DiscoAWS

        aws = DiscoAWS(self._config, self.option("bakery_environment"))
        filters = {"tag:hostclass": self.option("repo_hostclass"), "instance-state-name": "running"}
        instances = aws.instances(filters)
        return instances[0] if instances else None

    def remotecmd(self, instance, *args, **kwargs):
        """
        remotecmd accepts a boto instance followed by a list containing a string
        of all arguments, starting with the program to run.

        remotecmd optionally accepts three additional named arguments:

        stdin -- the bytes to send into program input
        nothrow -- when True the method will not throw if the program returns a non-zero result.
        log_on_error -- when True, command output will be logged at the error level on non-zero result.

        In addition to these explicit arguments, this method will redirect the
        subprocesses's stderr to stdout, and capture stdout.  If the logging level
        is set to debug, it will log the captured output.

        Returns a tuple of (return_code, captured_output).

        examples:

        self.remotecmd(inst, ['cat - > /tmp/myfile'], stdin='my content')

        ret, out = self.remotecmd(inst, ['ls -l /etc'], nothrow=True)

        """

        address = instance.private_ip_address if self._use_local_ip else instance.ip_address
        if not address:
            raise CommandError("No ip address available for sshing.")
        kwargs["user"] = kwargs.get("user", "root")
        return self.disco_remote_exec.remotecmd(address, *args, **kwargs)

    def is_repo_ready(self):
        """ True if repo is up"""
        return self.repo_instance()

    def init_phase(self, phase, instance, hostclass):
        """
        Runs the init script for a particular phase
        (i.e. phase1.sh for base bake and phase2.sh for hostclass specific bake).
        """
        logging.info("Phase %s config", phase)

        wait_for_sshable(self.remotecmd, instance)
        self.copy_aws_data(instance)
        self.invoke_host_init(instance, hostclass, "phase{0}.sh".format(phase))

    def invoke_host_init(self, instance, hostclass, script):
        """
        Executes an init script that was dropped off by disco_aws_data.
        Hostclass and Hostname are passed in as arguments
        """
        logging.info("Running remote init script %s.", script)
        script = "{0}/init/{1}".format(self.option("data_destination"), script)

        repo = self.repo_instance()
        if not repo:
            # hack, we insert a comment into /etc/hosts instead of ip.
            repo_ip = "#None"
        else:
            repo_ip = self.repo_instance().private_ip_address

        self.remotecmd(instance, [script, hostclass, repo_ip], log_on_error=True, forward_agent=True)

    def ami_stages(self):
        """ Return list of configured ami stages"""
        return self.option("ami_stages").split()

    @property
    def final_stage(self):
        """
        Name of final AMI promotion stage
        """
        self._final_stage = self._final_stage or self.ami_stages()[-1]
        return self._final_stage

    def promote_ami_to_production(self, ami):
        '''
        Share this AMI with the production accounts
        '''
        for prod_account in self.option("prod_account_ids").split():
            logging.warning("Permitting %s to be launched by prod account %s", ami.id, prod_account)
            ami.set_launch_permissions(prod_account)

    def promote_latest_ami_to_production(self, hostclass):
        """
        Promote youngest ami of latest stage to production
        """
        ami = self.find_ami(stage=self.final_stage, hostclass=hostclass)
        self.promote_ami_to_production(ami)

    def is_prod_ami(self, ami):
        """
        True if ami has been granted prod launch permission. To all prod accounts.
        """
        try:
            launch_permissions = ami.get_launch_permissions()
        except boto.exception.EC2ResponseError:
            # Most likely we failed to lookup launch_permissions because its
            # not our AMI. So we assume its not prod executable. This is an
            # incorrect assumption in prod but there we don't care.
            return False
        image_account_ids = [
            account[0]
            for account in launch_permissions.values()
        ]
        prod_accounts = self.option("prod_account_ids").split()
        if prod_accounts and set(prod_accounts) - set(image_account_ids):
            return False
        return True

    def promote_ami(self, ami, stage):
        '''
        Change the stage tag of an AMI.
        '''
        if stage not in self.ami_stages():
            raise AMIError("Unknown ami stage: {0}, check config option 'ami_stage'".format(stage))
        self._tag_ami(ami, {"stage": stage})

    def get_image(self, ami_id):
        """
        Returns an AMI object given an AMI ID.

        Raises an AMIError if we can't find the image
        """
        try:
            return self.connection.get_image(ami_id)
        except:
            raise AMIError("Could not locate image {0}.".format(ami_id))

    def copy_aws_data(self, instance):
        """
        Copies all the files in this repo to the destination instance.
        """
        logging.info("Copying discoaws data.")
        config_data_destination = self.option("data_destination")
        asiaq_data_destination = self.option("data_destination") + "/asiaq"
        self.remotecmd(instance, ["mkdir", "-p", asiaq_data_destination])
        self._rsync(instance,
                    normalize_path(self.option("config_data_source")),
                    config_data_destination,
                    user="root")
        # Ensure there is a trailing / for rsync to do the right thing
        asiaq_data_source = re.sub(r'//$', '/', normalize_path(self.option("asiaq_data_source")) + "/")
        self._rsync(instance,
                    asiaq_data_source,
                    asiaq_data_destination,
                    user="root")

    def _rsync(self, instance, *args, **kwargs):
        address = instance.private_ip_address if self._use_local_ip else instance.ip_address
        return self.disco_remote_exec.rsync(address, *args, **kwargs)

    def _get_phase1_ami_id(self, hostclass):
        phase1_ami = self.find_ami(self.ami_stages()[-1], self.hc_option(hostclass, "phase1_ami_name"))
        if not phase1_ami:
            raise AMIError("Couldn't find phase 1 ami.")
        return phase1_ami.id

    def _enable_root_ssh(self, instance):
        # Pylint wants us to name the exceptions, but we want to ignore all of them
        # pylint: disable=W0702
        ssh_args = SSH_DEFAULT_OPTIONS + ["-tt"]
        for user in ["ubuntu", "centos"]:
            try:
                self.remotecmd(
                    instance,
                    [
                        "sudo mv /home/{0}/.ssh/authorized_keys /root/.ssh/authorized_keys; "
                        "sudo chown root:root /root/.ssh/authorized_keys".format(user)
                    ],
                    user=user, ssh_options=ssh_args)
                break
            except:
                logging.debug(
                    "OS specific: moving %s user ssh keys to root",
                    user
                )

    def bake_ami(self, hostclass, no_destroy, source_ami_id=None, stage=None):
        # Pylint thinks this function has too many local variables and too many statements and branches
        # pylint: disable=R0914, R0915, R0912
        """
        Boot an AMI run init, and create a new ami.

        If hostclass is None then the phase is 1. If hostclass is not None then the default
        phase will be the one specified in the bake section but this can be overridden for
        a hostclass by specifying an explicit phase.

        If no_destroy is True then the instance used to perform baking is not terminated at the end.
        """
        config_path = normalize_path(self.option("config_data_source") + "/discoroot")
        if not path.exists(config_path):
            raise WrongPathError(
                "Cannot locate data files relative to current working directory: %s"
                "Ensure that you are baking from root of disco_aws_automation repo." % config_path
            )

        phase = int(self.hc_option(hostclass, "phase")) if hostclass else 1

        if phase == 1:
            base_image_name = hostclass if hostclass else self.option("phase1_ami_name")
            source_ami_id = source_ami_id or self.hc_option(base_image_name, 'bake_ami')
            hostclass = self.option("phase1_hostclass")
            logging.info("Creating phase 1 AMI named %s based on upstream AMI %s",
                         base_image_name, source_ami_id)
        else:
            source_ami_id = source_ami_id or self._get_phase1_ami_id(hostclass)
            base_image_name = hostclass
            logging.info("Creating phase 2 AMI for hostclass %s based on phase 1 AMI %s",
                         base_image_name, source_ami_id)

        image_name = "{0} {1}".format(base_image_name, int(time.time()))

        if hostclass not in self.option("no_repo_hostclasses").split() and not self.is_repo_ready():
            raise Exception("A {0} must be running to bake {1}"
                            .format(self.option("repo_hostclass"), hostclass))

        interfaces = self.vpc.networks["tunnel"].create_interfaces_specification(public_ip=True)

        image = None
        # Don't map the snapshot on bake.  Bake scripts shouldn't need the snapshotted volume.
        reservation = self.connection.run_instances(
            source_ami_id,
            block_device_map=self.disco_storage.configure_storage(
                ami_id=source_ami_id),
            instance_type=self.option("bakery_instance_type"),
            key_name=self.option("bake_key"),
            network_interfaces=interfaces)
        instance = reservation.instances[0]
        try:
            keep_trying(10, instance.add_tag, "hostclass", "bake_{0}".format(hostclass))
        except Exception:
            logging.exception("Setting hostclass during bake failed. Ignoring error.")

        try:
            wait_for_sshable(self.remotecmd, instance)
            self._enable_root_ssh(instance)
            self.init_phase(phase, instance, hostclass)

            if no_destroy:
                raise EarlyExitException("--no-destroy specified, skipping shutdown to allow debugging")

            logging.debug("Stopping instance")
            # We delete the authorized keys so there is no possibility of using the bake key
            # for root login in production and we shutdown via the shutdown command to make
            # sure the snapshot is of a clean filesystem that won't trigger fsck on start.
            # We use nothrow to ignore ssh's 255 exit code on shutdown of centos7
            self.remotecmd(instance, ["rm -Rf /root/.ssh/authorized_keys ; shutdown now -h"], nothrow=True)
            wait_for_state(instance, u'stopped', 300)
            logging.info("Creating snapshot from instance")

            # Check whether or not enhanced networking should be enabled for this hostclass
            enhanced_networking = self.hc_option_default(hostclass, "enhanced_networking", "false")
            # This is the easiest way to accomplish this without significantly rewriting things.
            # This attribute will be copied over the the AMI when it is created, and doesn't appear
            # to cause any problems.
            if is_truthy(enhanced_networking):
                logging.info("Setting enhanced networking attribute")
                self.connection.modify_instance_attribute(instance.id, "sriovNetSupport", "simple")

            image_id = instance.create_image(image_name, no_reboot=True)
            image = keep_trying(60, self.connection.get_image, image_id)

            stage = stage or self.ami_stages()[0]

            productline = self.hc_option_default(hostclass, "product_line", None)

            DiscoBake._tag_ami_with_metadata(image, stage, source_ami_id, productline)

            wait_for_state(image, u'available',
                           int(self.hc_option_default(hostclass, "ami_available_wait_time", "600")))
            logging.info("Created %s AMI %s", image_name, image_id)
        except EarlyExitException as early_exit:
            logging.info(str(early_exit))
        except:
            logging.exception("Snap shot failed. See trace below.")
            raise
        finally:
            if not no_destroy:
                instance.terminate()
            else:
                logging.info("Examine instance command: ssh root@%s",
                             instance.ip_address or instance.private_ip_address)

        return image

    @staticmethod
    def _tag_ami_with_metadata(ami, stage, source_ami_id, productline=None):
        """
        Tags an AMI with the stage, source_ami, the branch/git-hash of disco_aws_automation,
        and the productline if provided
        """
        tag_dict = OrderedDict()
        tag_dict['stage'] = stage
        tag_dict['source_ami'] = source_ami_id
        tag_dict['baker'] = getpass.getuser()
        tag_dict['version-asiaq'] = DiscoBake._git_ref()

        if productline:
            tag_dict['productline'] = productline

        DiscoBake._tag_ami(ami, tag_dict)

    @staticmethod
    def _tag_ami(ami, tag_dict):
        """
        Adds a dict of tags to an AMI with retries
        """
        for tag_name in tag_dict.keys():
            logging.info('Adding tag %s with value %s to ami', tag_name, tag_dict[tag_name])
            keep_trying(10, ami.add_tag, tag_name, tag_dict[tag_name])

    @staticmethod
    def _old_amis_by_days(amis, max_days):
        max_seconds = datetime.timedelta(days=max_days).total_seconds()
        oldest_timestamp = int(time.time() - max_seconds)
        return set([ami for ami in amis if DiscoBake.ami_timestamp(ami) < oldest_timestamp])

    @staticmethod
    def _ami_sort_key(ami):
        '''
        This returns a sort key that can be sorted lexographically
        and end up sorted by hostclass and then creation time.
        '''
        keys = ami.name.split()
        return "{0} {1:012d}".format(keys[0], int(keys[1]))

    @staticmethod
    def _old_amis_by_count(amis, max_count):
        amis_sorted_by_creation_time_desc = sorted(
            amis, key=DiscoBake._ami_sort_key, reverse=True)
        return set(amis_sorted_by_creation_time_desc[max_count:])

    def get_amis(self, image_ids=None, filters=None):
        """
        Returns images owned by a trusted account (including ourselves)
        """
        trusted_accounts = list(set(self.option_default("trusted_account_ids", "").split()) | set(['self']))
        return self.connection.get_all_images(
            image_ids=image_ids, owners=trusted_accounts, filters=filters)

    def cleanup_amis(self, restrict_hostclass, product_line, stage, min_age, min_count, dry_run):
        """
        Deletes oldest AMIs so long as they are older than min_age and there
        are at least min_count AMIs remaining in the hostclass.

        If restrict_hostclass is None then this will iterate over all hostclasses,
        else it will only cleanup the amis in the matching hostclass.

        If product_line is not None, then this will only iterate over amis tagged
        with that specific productline.

        """
        # Pylint complains that this function has one too many local variables.  But deleting any
        # would make it less readable, so...
        # pylint: disable=R0914
        now = datetime.datetime.utcnow()

        filters = {"tag:stage": stage}

        if product_line:
            filters["tag:productline"] = product_line

        amis = self.connection.get_all_images(owners=['self'], filters=filters)
        ami_map = defaultdict(list)
        for ami in amis:
            if AMI_NAME_PATTERN.match(ami.name):
                ami_map[DiscoBake.ami_hostclass(ami)].append(ami)

        for hostclass, amis in ami_map.iteritems():
            if restrict_hostclass and hostclass != restrict_hostclass:
                continue

            by_days = DiscoBake._old_amis_by_days(amis, min_age)
            by_count = DiscoBake._old_amis_by_count(amis, min_count)
            to_delete = by_days.intersection(by_count)

            if to_delete:
                logging.info("Deleting %s AMIs: %s", hostclass, to_delete)
                for ami in to_delete:
                    self.pretty_print_ami(ami, now)

            if not dry_run:
                orphan_snapshot_ids = []
                for ami in to_delete:
                    orphan_snapshot_ids.extend([bdm.snapshot_id for bdm in ami.block_device_mapping.values()
                                                if bdm.snapshot_id])
                    ami.deregister()

                # Delete snapshots of all the images we deleted
                for orphan_snapshot_id in orphan_snapshot_ids:
                    keep_trying(10, self.connection.delete_snapshot, orphan_snapshot_id)

    def list_amis_by_instance(self, instances=None):
        """
        Fetch the AMI object from which the instance was started from indexed by instance id
        :param instances: instances whose AMI objects we need

        Return a dict of AMI's indexed by instance id
        """
        ami_dict = defaultdict(list)
        for instance in instances:
            ami_dict[instance.image_id].append(instance.id)
        return {instance_id: image for image in self.get_amis(ami_dict.keys())
                for instance_id in ami_dict[image.id]}

    def list_amis(self, ami_ids=None, instance_ids=None, stage=None, product_line=None,
                  state=None, hostclass=None):
        """
        Fetch all AMI's filtered by supplied args
        :param amis:  AMI ids to filter by
        :param instance_ids:  ID's of instances whose AMI's we should filter by
        :param stage: Stage to filter by
        :param product_line: Product line to filter by
        :param state: State to filter by
        :param hostclass: Hostclass to filter by

        Return a list of matching AMI's
        """
        if instance_ids:
            instances = self.instances(instance_ids=instance_ids)
            instance_amis = set([instance.image_id for instance in instances])
            if ami_ids:
                ami_ids = list(instance_amis.intersection(ami_ids))
            else:
                ami_ids = list(instance_amis)
        return self.ami_filter(self.get_amis(ami_ids), stage, product_line, state, hostclass)

    def list_stragglers(self, days=1, stage=None):
        """
        Returns a dictionary where keys represent hostclass for which AMIs have not been
        been recently promoted from first stage. The value is last unpromoted AMI for the
        hostclass, which is best candidate for promotion. Value will be none if no
        unpromoted AMIs exist.

        Arguments:
        Days    -- How recently the AMI should have been promoted in days. (default '1')
        Stage   -- Minimum stage to which the AMI should have been promoted.
                   (default 'None', the second stage of promotion)
        """
        amis = self.get_amis()
        hostclasses = set([DiscoBake.ami_hostclass(ami) for ami in amis])
        first_stage = self.ami_stages()[0]
        stage = stage or self.ami_stages()[1]
        cutoff_time = int(time.time()) - days * 60 * 60 * 24
        stragglers = dict()
        for hostclass in hostclasses:
            latest_promoted = self.find_ami(stage, hostclass)
            if not latest_promoted or DiscoBake.ami_timestamp(latest_promoted) < cutoff_time:
                latest = self.find_ami(first_stage, hostclass)
                stragglers[hostclass] = latest
        return stragglers

    def delete_ami(self, ami):
        """
        Delete an AMI
        """
        logging.info("Deleting AMI %s", ami)
        self.connection.deregister_image(ami, delete_snapshot=True)

    def get_snapshots(self, ami):
        """Returns a snapshot object for an AMI object

        If an AMI maps multiple block devices, one is chosen without any specific ordering.
        """
        snapshot_ids = [value.snapshot_id for _key, value in ami.block_device_mapping.iteritems()]
        ids = [snap for snap in snapshot_ids if snap]
        try:
            return self.connection.get_all_snapshots(snapshot_ids=ids)
        except boto.exception.EC2ResponseError:
            return []

    def get_ami_creation_time_from_snapshots(self, ami):
        """Returns age of newest snapshot attached to an AMI"""
        snapshots = self.get_snapshots(ami)
        start_times = [dateutil.parser.parse(snapshot.start_time) for snapshot in snapshots]
        return max(start_times).replace(tzinfo=None) if start_times else None

    def get_ami_creation_time(self, ami):
        """Returns AMI creation time using least costly method that works, None if none works"""
        if not ami:
            return None
        return (DiscoBake.extract_ami_creation_time_from_ami_name(ami) or
                self.get_ami_creation_time_from_snapshots(ami))

    @staticmethod
    def ami_timestamp(ami):
        """Return creation timestamp from ami name, returns 0 if one is not found"""
        try:
            return int(ami.name.split()[-1])
        except (ValueError, IndexError):
            return 0

    @staticmethod
    def extract_ami_creation_time_from_ami_name(ami):
        """Returns creation time from AMI name, returns None if it isn't there"""
        seconds_since_epoch = DiscoBake.ami_timestamp(ami)
        if not seconds_since_epoch:
            return None
        else:
            timestamp = datetime.datetime.fromtimestamp(seconds_since_epoch, tz=UTC)  # our timestamp is UTC
            timestamp_naive = timestamp.replace(tzinfo=None)  # but the rest of the code expects no tz
            return timestamp_naive

    @staticmethod
    def ami_hostclass(ami):
        """Return hostclass/ami-type from ami"""
        return ami.name.split()[0]

    def ami_filter(self, amis, stage=None, product_line=None, state=None, hostclass=None):
        """
        Returns a filtered subset of amis. Optionally filtered by their productline,
        stage, state, and hostclass.
        """
        filtered_amis = []
        for ami in amis:
            filters = [
                not stage or ami.tags.get("stage", None) == stage,
                not product_line or ami.tags.get("productline", None) == product_line,
                not state or ami.state == state,
                not hostclass or self.ami_hostclass(ami) == hostclass]
            if all(filters):
                filtered_amis.append(ami)
        return filtered_amis

    def find_ami(self, stage, hostclass=None, ami_id=None, product_line=None):
        """
        Find latest AMI of compatible stage, filtered on AMI's hostclass, id, or product_line
        Note that id overrides stage, product_line, and hostclass options.
        """

        if ami_id:
            amis = self.get_amis([ami_id])
            return amis[0] if amis else None
        elif hostclass:
            filters = {}
            filters["name"] = "{0} *".format(hostclass)
            amis = self.get_amis(filters=filters)
            logging.debug("AMI search for %s found %s", filters, amis)
            amis = self.ami_filter(amis, stage, product_line)
            return max(amis, key=self.ami_timestamp) if amis else None
        else:
            raise ValueError("Must specify either hostclass or AMI")

    @staticmethod
    def _git_ref():
        """
        Returns a string containing the current branch and git hash
        """
        branch = check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).strip()
        githash = check_output(['git', 'rev-parse', '--short', 'HEAD']).strip()
        return '%s-%s' % (branch, githash)
