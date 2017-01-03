"""
Code to manage users and groups on AWS hosts, as well as storing and retrieving
said account credentials to/from S3.
"""
from ConfigParser import ConfigParser
import subprocess
import os
import logging
from pwd import getpwnam
from grp import getgrnam
from tempfile import mkstemp
from boto.exception import S3ResponseError as S3ResponseError

from .disco_aws_util import is_truthy
from .exceptions import AccountError

logger = logging.getLogger(__name__)
USER_OPTION_FLAGS = {
    "home": "-d",
    "password": "-p",
    "groups": "-G",
    "id": "-u",
    "shell": "-s",
}
REQUIRED_OPTIONS = ["id"]
MIN_ID = 2000  # Minimum User/Group ID that we should assign


class Group(object):
    """ Group account """

    def __init__(self, name, config):
        self.name = name
        if config.has_option("group", "id"):
            self.account_id = int(config.get("group", "id"))
        else:
            raise AccountError(
                "Option id is missing in group config {0}."
                .format(name)
            )

    def __str__(self):
        return "Group:{0}:{1}".format(self.name, self.account_id)

    def exists(self):
        """ True if this group is already exists under unix system """
        try:
            getgrnam(self.name)
            return True
        except KeyError:
            return False

    def install(self):
        """ add group to unix system """
        command = ["groupmod"] if self.exists() else ["groupadd"]
        command.append("-g{0}".format(self.account_id))
        command.append(self.name)
        if subprocess.call(command) != 0:
            raise AccountError(
                "Failed to create group account {0}"
                .format(self.name)
            )


class User(object):
    """ User account """

    def __init__(self, name, config):
        self.name = name
        self.options = {}
        self.options["ssh_keys"] = []

        for key, value in config.items("user"):
            if key.startswith("ssh_key"):
                self.options["ssh_keys"].append(value)
            else:
                self.options[key] = value

        if not set(REQUIRED_OPTIONS).issubset(self.options):
            raise AccountError(
                "Required Option (one of {0}) is missing in config."
                .format(REQUIRED_OPTIONS)
            )

    def __str__(self):
        return "User:{0}:{1}".format(self.name, self.account_id)

    def exists(self):
        """ True if this user is already exists under unix system """
        try:
            getpwnam(self.name)
            return True
        except KeyError:
            return False

    def _create_account(self):
        """ Add or update user account """
        if self.exists():
            command = ["usermod"]
        else:
            command = ["useradd"]

        for field, flag in USER_OPTION_FLAGS.iteritems():
            if field in self.options:
                command.append("{0}{1}".format(flag, self.options[field]))
        if is_truthy(self.options.get("active", "yes")):
            # passing blank parameter to -e disables expiration
            command.append("-e")
            command.append("")
        else:
            # expire the account
            command.append("-e1970-01-01")

        command.append("-g{0}".format(self.group_id))

        # Username must be last option
        command.append(self.name)

        return subprocess.call(command) == 0

    @property
    def home_dir(self):
        """ Find home directory of user """
        try:
            return getpwnam(self.name).pw_dir
        except KeyError:
            raise AccountError("No user {0}.".format(self.name))

    @property
    def group_id(self):
        """ Find default group id of user """
        try:
            return getgrnam(self.name).gr_gid
        except KeyError:
            raise AccountError("No group for {0}.".format(self.name))

    @property
    def account_id(self):
        """ UNIX user id of user """
        return int(self.options["id"])

    def _install_ssh_keys(self):
        ssh_dir = "{0}/.ssh".format(self.home_dir)
        if not os.access(ssh_dir, os.F_OK):
            os.makedirs(ssh_dir)
        authorized_keys_file = "{0}/authorized_keys".format(ssh_dir)
        with open(authorized_keys_file, "a") as auth_fd:
            for key in self.options["ssh_keys"]:
                auth_fd.write("{0}\n".format(key))

        for path in ssh_dir, authorized_keys_file:
            os.chown(path, self.account_id, self.group_id)
            os.chmod(path, 0700)

    def install(self):
        """ Add user to unix system and install ssh key """
        if not self._create_account():
            raise AccountError(
                "Failed to create user account {0}"
                .format(self.name)
            )
        self._install_ssh_keys()


class S3AccountBackend(object):
    """ Access account information stored in s3 """

    def __init__(self, bucket):
        """ Initialize with DiscoS3Bucket to retrieve data from """
        self.bucket = bucket

    def _get_accounts(self, account_type, account_class):
        """ Get accounts from s3 """
        accounts = []
        for account in self.bucket.list("accounts/{0}s/".format(account_type)):
            account_name = account.key[account.key.rfind("/") + 1:]
            if not account_name:
                continue
            try:
                config = self.bucket.load_config(account)
                if account_type not in config.sections():
                    raise AccountError("unknown file format")
                accounts.append(account_class(account_name, config))
            except (S3ResponseError, AccountError) as err:
                logger.info("User info unavailable for %s: %s", account_name, err)
        return accounts

    def list_users(self):
        """List all users"""
        return [obj.name.replace("accounts/users/", "")
                for obj in self.bucket.list("accounts/users/")]

    def list_groups(self):
        """List all groups"""
        return [obj.name.replace("accounts/groups/", "")
                for obj in self.bucket.list("accounts/groups/")]

    def refresh_group(self, group):
        """Refresh a single group, to assure it doesn't go to Glacier"""
        self.add_account(group, self.get_group_config(group))

    def refresh_groups(self):
        """Refresh all group buckets"""
        map(self.refresh_group, iter(self.list_groups()))

    def _next_id(self, account_type, account_class, min_id=MIN_ID):
        ids = [account.account_id
               for account in self._get_accounts(account_type, account_class)]
        for id_num in range(min_id, len(ids) + min_id + 1):
            if id_num not in ids:
                return id_num

    def _config_account_type(self, config):
        """ Return user/group depending on type of config """
        if config.has_section("user"):
            return "user"
        elif config.has_section("group"):
            return "group"
        else:
            raise AccountError("Unknown account type.")

    def _get_account_config(self, account_type, name):
        key = self.bucket.bucket.get_key(
            "accounts/{0}/{1}"
            .format(account_type, name)
        )
        return self.bucket.load_config(key)

    def get_user_config(self, name):
        """ Returns user configuration for the specified user name """
        return self._get_account_config("users", name)

    def get_group_config(self, name):
        """ Returns group configuration for the specified group name """
        return self._get_account_config("groups", name)

    def add_account(self, account_name, config):
        """ Add / Update the account to s3 """
        config_type = self._config_account_type(config)
        key_name = "accounts/{0}s/{1}".format(config_type, account_name)
        key = self.bucket.bucket.new_key(key_name)
        self.bucket.save_config(key, config)

    def users(self):
        """ Returns list containing a User object for each user """
        return self._get_accounts("user", User)

    def groups(self):
        """ Returns list containing a Group object for each group """
        return self._get_accounts("group", Group)

    def next_user_id(self, min_id=MIN_ID):
        """ Next available system user id """
        return self._next_id("user", User, min_id)

    def next_group_id(self, min_id=MIN_ID):
        """ Next available system group id """
        return self._next_id("group", Group, min_id)

    def install_all(self):
        """ Install all user and group accounts from s3 to unix """
        # We aggregate exceptions and re-throw them at the end. This way if one
        # account ID cannot be updated (e.g. account UID cannot be changed
        # while process is running with that UID) the other accounts won't be
        # skipped
        exceptions = []
        for accounts in self.groups, self.users:
            for account in accounts():
                try:
                    account.install()
                except AccountError as error:
                    exceptions.append(error)
        if exceptions:
            raise AccountError(exceptions)

    def new_user_config(self, password):
        """ Returns a ConfigParser with a new user configuration """
        user_config = ConfigParser()
        section = "user"
        user_config.add_section(section)
        user_config.set(section, "password", password)
        user_config.set(section, "ssh_key_0", "Insert SSH key")
        user_config.set(section, "shell", "/bin/bash")
        user_config.set(section, "groups", "users,disco_operators")
        user_config.set(section, "active", "yes")
        user_config.set(section, "id", self.next_user_id())
        return user_config

    def new_group_config(self):
        """ Returns a ConfigParser with a new group configuration """
        group_config = ConfigParser()
        group_config.add_section("group")
        group_config.set("group", "id", self.next_group_id())
        return group_config

    def edit_account_config(self, config, **kwargs):
        """ Launches an editor to edit a user or group configuration """
        account_type = self._config_account_type(config)

        if not kwargs:  # interactive mode
            # save config to file
            temp_fd, temp_filename = mkstemp(suffix=".ini", text=True)
            try:
                temp_file = os.fdopen(temp_fd, "w")
                config.write(temp_file)
                temp_file.close()

                # open config in editor
                editor = os.environ.get('EDITOR', 'vi')
                subprocess.call([editor, temp_filename])

                # fetch config from file
                new_config = ConfigParser()
                new_config.read(temp_filename)
            finally:
                os.remove(temp_filename)
        else:
            new_config = config
            for key in kwargs:
                new_config.set(account_type, key, kwargs[key])

        # Sanity check resulting config
        if account_type == "user":
            User("foo", new_config)
        elif account_type == "group":
            Group("foo", new_config)

        return new_config
