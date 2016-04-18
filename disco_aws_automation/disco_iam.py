"""
Code to manage IAM roles, groups, and policies.
"""
from __future__ import print_function
import json
import os
import os.path
import urllib
import urllib2
from collections import defaultdict
import logging
from datetime import datetime
import boto
import boto.iam
import boto3
import botocore

from . import read_config
from .disco_aws_util import is_truthy


IAM_USER_POLICY_DIR = "iam/user_policies"
IAM_INSTANCE_POLICY_DIR = "iam/instance_policies"
IAM_GROUP_DIR = "iam/group_membership"
IAM_ARPD_PATH = "iam/federation/AssumeRolePolicyDocument.iam"
IAM_EXT = ".iam"
TR_EXT = ".tr"
GROUP_EXT = ".grp"
GROUP_PREFIX = "disco"
IAM_SECTION = "iam"


class DiscoIAM(object):
    '''Class orchestrating identity and access management on AWS (IAM)'''

    def __init__(self, config=None, environment=None, boto2_connection=None, boto3_connection=None):
        if config:
            self._config = config
        else:
            self._config = read_config()

        self._boto2_connection = boto2_connection
        self._boto3_connection = boto3_connection

        self._environment = environment

        self._now = datetime.utcnow().isoformat('T').replace(':', '.')

    @property
    def connection(self):
        """
        Lazily creates boto2 IAM connection
        """
        if not self._boto2_connection:
            self._boto2_connection = boto.connect_iam()
        return self._boto2_connection

    @property
    def boto3_iam(self):
        """
        Lazily creates boto3 IAM connection
        """
        if not self._boto3_connection:
            self._boto3_connection = boto3.client('iam')
        return self._boto3_connection

    def set_environment(self, environment):
        """Sets the environment"""
        self._environment = environment

    def option(self, key):
        """Fetch a configuration option"""
        if self._environment:
            env_key = "{0}@{1}".format(key, self._environment)
            if self._config.has_option(IAM_SECTION, env_key):
                return self._config.get(IAM_SECTION, env_key)
        if self._config.has_option(IAM_SECTION, key):
            return self._config.get(IAM_SECTION, key)
        return None

    def option_list(self, key):
        """Fetch a configuration option as a list"""
        value = self.option(key)
        return value.split() if value else []

    def get_certificate_arn(self, dns_name):
        """Returns a Server Certificate ARN from IAM given the DNS name"""
        certs = self.boto3_iam.list_server_certificates()["ServerCertificateMetadataList"]
        cert = [cert['Arn'] for cert in certs if cert['ServerCertificateName'] == dns_name]
        return cert[0] if cert else None

    def list_groups(self):
        '''Lists IAM User Groups'''
        return [
            group.group_name
            for group in self.connection.get_all_groups().list_groups_response.list_groups_result.groups
        ]

    def create_group(self, group_name):
        '''Creates an IAM User Group'''
        self.connection.create_group(group_name)

    def remove_group(self, group_name):
        '''Deletes an IAM User Group (this removes users and policies from group first)'''
        logging.debug("Removing group %s.", group_name)
        for user in self.list_group_members(group_name):
            self.remove_user_from_group(user, group_name)
        for policy in self.list_group_policies(group_name):
            self.remove_group_policy(group_name, policy)
        self.connection.delete_group(group_name)

    def list_group_policies(self, group_name):
        '''Lists all policies attached to an IAM User Group'''
        return self.connection.get_all_group_policies(
            group_name).list_group_policies_response.list_group_policies_result.policy_names

    def get_group_policy(self, group_name, policy_name):
        """Returns one of a group's IAM policies by name"""
        resp = self.connection.get_group_policy(group_name, policy_name).get_group_policy_response
        return urllib.unquote(resp.get_group_policy_result.policy_document)

    def remove_group_policy(self, group_name, policy_name):
        """Removes one of a group's IAM policies by name"""
        self.connection.delete_group_policy(group_name, policy_name)

    def _format_policy_name(self, policy_name):
        return policy_name + '_' + self._now

    def set_group_policy(self, group_name, policy_name, policy_file):
        """Sets one of a groups IAM policies by name"""
        logging.debug("Applying policy %s to group %s from %s.", policy_name, group_name, policy_file)
        with open(policy_file) as infile:
            policy = infile.read()
        policy_json = json.dumps(json.loads(policy), indent=4)  # indentation is important
        self.connection.put_group_policy(
            group_name, self._format_policy_name(policy_name), policy_json)

    def list_users(self):
        '''List IAM Users'''
        return [
            user.user_name
            for user in self.connection.get_all_users().list_users_response.list_users_result.users
        ]

    def print_users(self):
        '''Pretty Prints IAM Users to standard output'''
        users = self.list_users()
        if not users:
            return
        fmt = "{0:<" + str(max([len(user) for user in users])) + "} {1}"
        for user in users:
            groups = self.connection.get_groups_for_user(
                user).list_groups_for_user_response.list_groups_for_user_result.groups
            groups_str = ",".join(sorted([group.group_name for group in groups]))
            print((fmt.format(user, groups_str)))

    def create_user(self, user_name):
        '''Creates an IAM User'''
        logging.debug("Creating user %s.", user_name)
        self.connection.create_user(user_name)

    def remove_user(self, user_name):
        '''Deletes an IAM User'''
        logging.debug("Removing user %s.", user_name)
        for key in self.list_access_keys(user_name):
            self.remove_access_key(user_name, key.access_key_id)
        iam = boto3.resource('iam')
        user = iam.User(user_name)
        attached_policies = user.attached_policies.all()
        for policy in attached_policies:
            user.detach_policy(PolicyArn=policy.arn)
        login_profile = iam.LoginProfile(user_name)
        try:
            login_profile.delete()
        except botocore.exceptions.ClientError as error:
            logging.debug("%s doesnt have a login profile. Error Response: %s", user_name, error.response)
        self.connection.delete_user(user_name)

    def list_user_groups(self, user_name):
        '''Lists groups that IAM User is a member of'''
        groups_response = self.connection.get_groups_for_user(user_name)
        groups = groups_response.list_groups_for_user_response.list_groups_for_user_result.groups
        return [group.group_name for group in groups]

    def add_user_to_group(self, user_name, group_name):
        '''Adds an IAM User to a group'''
        self.connection.add_user_to_group(group_name, user_name)

    def remove_user_from_group(self, user_name, group_name):
        '''Removes an IAM User from a group'''
        self.connection.remove_user_from_group(group_name, user_name)

    def list_access_keys(self, user_name):
        '''Lists the AWS access keys attached to an IAM user'''
        keys = self.connection.get_all_access_keys(
            user_name).list_access_keys_response.list_access_keys_result.access_key_metadata
        return [key for key in keys]

    def create_access_key(self, user_name):
        '''Creates an AWS access key for an IAM user'''
        access_key = self.connection.create_access_key(
            user_name).create_access_key_response.create_access_key_result
        print("[Credentials]")
        print(("aws_access_key_id = {0}".format(access_key.access_key_id)))
        print(("aws_secret_access_key = {0}".format(access_key.secret_access_key)))

    def activate_access_key(self, user_name, access_key_id):
        '''Activates an AWS access key for an IAM user'''
        self.connection.update_access_key(access_key_id, 'Active', user_name)

    def deactivate_access_key(self, user_name, access_key_id):
        '''Deactivates an AWS access key for an IAM user'''
        self.connection.update_access_key(access_key_id, 'Inactive', user_name)

    def remove_access_key(self, user_name, access_key_id):
        '''Deletes an AWS access key from an IAM user'''
        logging.debug("Removing %s's key %s", user_name, access_key_id)
        self.connection.delete_access_key(access_key_id, user_name)

    # TODO refactor instance profile functions
    # 1. There is at most one role per instance profile which wasn't known by us when
    #    we wrote these functions.
    # 2. Some of the function names bear little relation to what the functions actually do.
    # 3. These do not use the snake case function naming convention.
    def listinstanceprofiles(self):
        '''Lists all instance profiles (each contains one instance role of the same name)'''
        profiles = self.connection.list_instance_profiles()
        return [
            profile.instance_profile_name
            for profile in (
                profiles.list_instance_profiles_response.list_instance_profiles_result.instance_profiles
            )
        ]

    def list_roles_instance_profiles(self, role):
        '''Lists the 0 or 1 instance roles associated with an instance profile'''
        profiles = self.connection.list_instance_profiles_for_role(role)
        return [
            profile.role_name
            for profile in (
                profiles.list_instance_profiles_for_role_response
                .list_instance_profiles_for_role_result.instance_profiles
            )
        ]

    def getinstanceprofile(self, instance_profile_name):
        '''Returns instance role associated with the instance profile name'''
        response = self.connection.get_instance_profile(instance_profile_name)
        # despite common English sense, .roles returns a single element
        role = response.get_instance_profile_response.get_instance_profile_result.roles
        return role if role else None

    def createinstanceprofile(self, instance_profile_name):
        '''Creates an instance profile'''
        self.connection.create_instance_profile(instance_profile_name)

    def removeinstanceprofile(self, instance_profile_name):
        '''Deletes an instance profile'''
        self.connection.delete_instance_profile(instance_profile_name)

    def listroles(self):
        """
        Return all roles with deserialized Assume Role Policy Document
        """
        roles = self.connection.list_roles().list_roles_response.list_roles_result.roles
        for role in roles:
            role.assume_role_policy_document = AssumeRolePolicyDocument(role.assume_role_policy_document)
        return roles

    def createrole(self, role_name, arpd=None):
        '''Creates an IAM Role'''
        logging.debug("Creating role %s", role_name)
        self.connection.create_role(role_name, arpd)

    def removerole(self, role_name):
        '''Deletes an IAM Role and any linked policies or instance profile'''
        for policy in self.listrolepolicies(role_name):
            self.removerolepolicy(role_name, policy)
        for profile in self.list_roles_instance_profiles(role_name):
            self.removerolefrominstanceprofile(role_name, profile)
            self.removeinstanceprofile(profile)
        self.connection.delete_role(role_name)

    def listrolepolicies(self, role_name):
        '''Lists policies attached to an IAM Role'''
        response = self.connection.list_role_policies(role_name)
        return [
            policy_name
            for policy_name in response.list_role_policies_response.list_role_policies_result.policy_names
        ]

    def getrolepolicy(self, role_name, policy_name):
        """Return a role's IAM policy"""
        response = self.connection.get_role_policy(role_name, policy_name)
        policy = response.get_role_policy_response.get_role_policy_result.policy_document
        return urllib.unquote(policy)

    def createrolepolicy(self, role_name, policy_name, policy_file):
        '''Creates an IAM Role Policy given an input file containing the appropriate json'''
        logging.debug("Applying policy %s to role %s from %s.", policy_name, role_name, policy_file)
        with open(policy_file) as infile:
            policy = infile.read()
        policy_json = json.dumps(json.loads(policy), indent=4)  # indentation is important
        self.connection.put_role_policy(role_name, self._format_policy_name(policy_name), policy_json)

    def removerolepolicy(self, role_name, policy_name):
        '''Deletes an IAM role policy'''
        self.connection.delete_role_policy(role_name, policy_name)

    def addroletoinstanceprofile(self, role_name, instance_profile_name):
        '''Adds an IAM Role to an IAM Instance Profile'''
        self.connection.add_role_to_instance_profile(instance_profile_name, role_name)

    def removerolefrominstanceprofile(self, role_name, instance_profile_name):
        '''Removes an IAM Role to an IAM Instance Profile'''
        self.connection.remove_role_from_instance_profile(instance_profile_name, role_name)

    def decode_message(self, message):
        '''Decodes an any encrypted AWS Error message'''
        from boto.sts import STSConnection
        sts_connection = STSConnection()
        print("---------- Decoded message ----------")
        print((sts_connection.decode_authorization_message(message).decoded_message))

    def account_id(self):
        """
        Current Account ID
        """
        # return self.connection.get_user().get_user_response.get_user_result.user.arn.split(":")[4]
        return boto3.resource('iam').CurrentUser().arn.split(":")[4]

    def reapply_user_policies(self):
        '''Reapplies all IAM and federated user policies from configuration in IAM_USER_POLICY_DIR'''
        policies = self._list_role_configs(IAM_USER_POLICY_DIR)
        self.reapply_user_groups(policies)
        self.reapply_trust_roles(policies)

    def _list_role_configs(self, directory):
        policy_files = os.listdir(directory)
        return [policy[:-len(IAM_EXT)] for policy in policy_files if policy.endswith(IAM_EXT)]

    def _list_roles_by_type(self):
        roles = self.listroles()
        federated_roles = [
            role.role_name for role in roles
            if role.assume_role_policy_document.is_federated()
        ]
        unfederated_roles = [
            role.role_name for role in roles
            if not role.assume_role_policy_document.is_federated()
        ]
        return federated_roles, unfederated_roles

    def _prune_role_policies(self, role_name, keep_policy):
        existing_policies = (set(self.listrolepolicies(role_name)) -
                             set([self._format_policy_name(keep_policy)]))
        for policy in existing_policies:
            self.removerolepolicy(role_name, policy)

    def _cleanup_roles(self, old_roles, updated_roles):
        deleted_roles = []
        for role in old_roles:
            if role not in updated_roles:
                self.removerole(role)
                logging.debug("Cleanup Role(%s)", role)
                deleted_roles.append(role)
        return deleted_roles

    def _get_federated_trust_relationship_json(self):
        with open(IAM_ARPD_PATH) as arpd_file:
            arpd = json.load(arpd_file)
        try:
            arpd["Statement"][0]["Principal"]["Federated"] = self.list_saml_providers()[0].arn
        except (KeyError, IndexError):
            raise RuntimeError("Failed to look up provider ARN. Make sure SAML provider is configured.")
        return json.dumps(arpd, indent=4)  # indentation is important

    def _get_trust_relationship_json(self, policy):
        tr_filename = "{0}/{1}{2}".format(IAM_USER_POLICY_DIR, policy, TR_EXT)
        if not os.path.isfile(tr_filename):
            return None
        with open(tr_filename) as tr_file:
            json_data = json.load(tr_file)
            return json.dumps(json_data, indent=4)  # indentation is important

    def _create_role_name(self, role_prefix, policy, naked_roles):
        if policy in naked_roles:
            return policy
        parts = []
        if role_prefix:
            parts.append(role_prefix)
        if self._environment:
            parts.append(self._environment)
        return "_".join(parts + [policy])

    # Allow >15 variables
    # pylint: disable=R0914
    def reapply_trust_roles(self, all_policies):
        '''
        Creates and updates roles which are assumed via a trust.

        The trust may be either federated trust (SSO), or a specific trust ".tr" policy
        document defined for a particular role.

        These are not instance roles which have an associated instance profile.
        '''
        naked_roles = self.option_list("naked_roles")
        role_prefix = self.option("role_prefix")
        policy_blacklist = self.option_list("policy_blacklist")

        federated_roles, unfederated_roles = self._list_roles_by_type()
        existing_roles = set(federated_roles) | set(unfederated_roles)

        try:
            federated_trust = self._get_federated_trust_relationship_json()
        except IOError:
            federated_trust = None
            logging.debug("Not federating trust, no trust document found at %s", IAM_ARPD_PATH)

        policies = set(all_policies) - set(policy_blacklist)

        updated_roles = []

        for policy in policies:
            role_name = self._create_role_name(role_prefix, policy, naked_roles)
            specific_trust = self._get_trust_relationship_json(policy)
            trust = specific_trust if specific_trust else federated_trust
            if role_name in existing_roles:
                if trust:
                    self.connection.update_assume_role_policy(role_name, trust)
            else:
                self.createrole(role_name, trust)
            self.createrolepolicy(
                role_name, policy,
                "{0}/{1}{2}".format(IAM_USER_POLICY_DIR, policy, IAM_EXT)
            )
            self._prune_role_policies(role_name, keep_policy=policy)
            updated_roles.append(role_name)

        deleted_roles = self._cleanup_roles(federated_roles, updated_roles)
        logging.debug("Updated federated user roles: %s.", updated_roles)
        logging.debug("Deleted federated user roles: %s.", deleted_roles)
        return (updated_roles, deleted_roles)

    def reapply_instance_policies(self):
        '''
        Creates and updates roles which are assumed by instances.

        The roles always begin with "instance_" and have an associated instance
        profile of the same name.
        '''
        policies = self._list_role_configs(IAM_INSTANCE_POLICY_DIR)
        role_prefix = "instance"

        federated_roles, unfederated_roles = self._list_roles_by_type()
        instance_roles = [role for role in unfederated_roles if role.startswith(role_prefix)]

        updated_roles = []
        for policy in policies:
            role_name = "_".join([role_prefix, policy])
            if role_name in instance_roles:
                self._prune_role_policies(role_name, keep_policy=policy)
                # TODO recreate the instance profile or make sure it exists.
            elif role_name in federated_roles:
                self.removerole(role_name)
                self.createrole(role_name)
                self.createinstanceprofile(role_name)
                self.addroletoinstanceprofile(role_name, role_name)
            else:
                self.createrole(role_name)
                self.createinstanceprofile(role_name)
                self.addroletoinstanceprofile(role_name, role_name)
            self.createrolepolicy(
                role_name, policy,
                "{0}/{1}{2}".format(IAM_INSTANCE_POLICY_DIR, policy, IAM_EXT)
            )
            updated_roles.append(role_name)

        deleted_roles = self._cleanup_roles(instance_roles, updated_roles)
        logging.debug("Updated instance roles: %s.", updated_roles)
        logging.debug("Deleted instance roles: %s.", deleted_roles)
        return (updated_roles, deleted_roles)

    def _prune_group_policies(self, group_name, keep_policy):
        existing_policies = (set(self.list_group_policies(group_name)) -
                             set([self._format_policy_name(keep_policy)]))
        for existing_policy in existing_policies:
            self.remove_group_policy(group_name, existing_policy)

    def reapply_user_groups(self, policies):
        '''Updates IAM User Groups from configuration (not including group membership)'''
        groups = self.list_groups()

        updated_groups = []
        for policy in policies:
            group_name = "{0}_{1}".format(GROUP_PREFIX, policy)
            if group_name not in groups:
                self.create_group(group_name)
            self.set_group_policy(
                group_name, policy,
                "{0}/{1}{2}".format(IAM_USER_POLICY_DIR, policy, IAM_EXT)
            )
            if group_name in groups:
                self._prune_group_policies(group_name, policy)

            updated_groups.append(group_name)

        deleted_groups = []
        for group in groups:
            if group not in updated_groups:
                self.remove_group(group)
                deleted_groups.append(group)

        logging.debug("Updated policies on groups: %s.", updated_groups)
        logging.debug("Deleted groups: %s.", deleted_groups)
        return (updated_groups, deleted_groups)

    def list_group_members(self, group):
        '''Returns list of IAM Users in IAM Group'''
        return [
            user.user_name
            for user in self.connection.get_group(group).get_group_response.get_group_result.users
        ]

    def _list_users_in_config(self, environment):
        users = os.listdir("/".join([IAM_GROUP_DIR, environment]))
        return [user[:-len(GROUP_EXT)] for user in users if user.endswith(GROUP_EXT)]

    def reapply_group_members(self):
        '''Updates IAM User Group membership from configuration'''
        users = set(self._list_users_in_config(self._environment))
        existing_users = set(self.list_users())

        # Create users before we attempt to add them to groups
        for user in users.difference(existing_users):
            logging.debug("Creating user %s.", user)
            self.create_user(user)

        # Update group members
        groups = defaultdict(set)
        for user in users:
            with open("{0}/{1}/{2}{3}".format(IAM_GROUP_DIR, self._environment, user, GROUP_EXT)) as userfile:
                usergroups = userfile.read().split()
            for group in usergroups:
                groups["{0}_{1}".format(GROUP_PREFIX, group)].add(user)

        for group in groups.keys() + self.list_groups():
            existing_members = set(self.list_group_members(group))
            for user in groups[group].difference(existing_members):
                logging.debug("Adding user %s to group %s", user, group)
                self.add_user_to_group(user, group)
            for user in existing_members.difference(groups[group]):
                logging.debug("Removing user %s from group %s", user, group)
                self.remove_user_from_group(user, group)

        # Delete users after they've been purged from groups
        for user in existing_users.difference(users):
            self.remove_user(user)

        # Delete groups without users
        if is_truthy(self.option("prune_empty_groups")):
            empty_groups = [group
                            for group in self.list_groups()
                            if not self.list_group_members(group)]
            for group in empty_groups:
                self.remove_group(group)
            logging.debug("Deleted empty groups: %s.", empty_groups)

    def create_saml_provider(self):
        """
        Create SAML providers from configuration
        """
        name = self.option("saml_provider_name")
        url = self.option("saml_provider_url")
        if not name or not url:
            logging.debug("No SAML provider")
            return None

        metadata_response = urllib2.urlopen(url)
        federation_metadata = metadata_response.read()
        self.connection.create_saml_provider(federation_metadata, name)

        logging.debug("Created SAML provider: %s.", name)
        return name

    def list_saml_providers(self):
        """
        List all SAML providers
        """
        providers = self.connection.list_saml_providers()
        return providers.list_saml_providers_response.list_saml_providers_result.saml_provider_list

    def delete_saml_providers(self):
        """
        Delete all SAML providers
        """

        deleted = []
        for provider in self.list_saml_providers():
            deleted.append(provider.arn)
            self.connection.delete_saml_provider(provider.arn)
        logging.debug("Deleted SAML providers: %s.", deleted)
        return deleted


class AssumeRolePolicyDocument(object):
    """
    Assume Role Policy Document of a role.
    """

    def __init__(self, document):
        self.document = json.loads(urllib.unquote(document))

    def is_federated(self):
        """Returns true iff a role is federated with Microsoft Active Directory"""
        try:
            return "Federated" in self.document["Statement"][0]["Principal"]
        except (KeyError, IndexError):
            pass
        return False
