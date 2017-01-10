#!/usr/bin/env python
"""
Manipulate disco user accounts. We store accounts in S3 and install them to
system on boot

Usage:
    disco_accounts.py [--debug] [--env ENV | --bucket BUCKET] install
    disco_accounts.py [--debug] [--env ENV | --bucket BUCKET] (addgroup | editgroup) --name NAME
    disco_accounts.py [--debug] [--env ENV | --bucket BUCKET] adduser [--name NAME] [--password PASS]
    disco_accounts.py [--debug] [--env ENV | --bucket BUCKET] edituser [--name NAME] [--active ACTIVE]
    disco_accounts.py [--debug] hashpassword
    disco_accounts.py [--debug] (listgroups | listusers)
    disco_accounts.py (-h | --help)

Options:
    -h --help       Show this screen
    --debug         Log in debug level.
    --env ENV       The environment/vpc name (first bucket in environment is used)
    --bucket BUCKET The bucket name
    --name NAME     The account name
    --password PASS The account password (normally you should omit this flag and use the interactive prompt)
    --active ACTIVE Updates the "active" field for this user account. ACTIVE := (yes | no)

Commands:
    install         Fetch accounts from S3 and install them
    listusers       List all users
    listgroups      List all groups
    adduser         Create new user account (interactive mode if invoked without optional parameters)
    addgroup        Create new group account (interactive mode)
    edituser        Edit user account (interactive mode if invoked without optional parameters)
    editgroup       Edit group account (interactive mode)
    hashpassword    Hash password to linux compat hash format
"""

from __future__ import print_function
import sys
import os
from getpass import getpass

from docopt import docopt

from disco_aws_automation import S3AccountBackend, DiscoS3Bucket, DiscoVPC
from disco_aws_automation.disco_aws_util import run_gracefully
from disco_aws_automation.disco_config import read_config
from disco_aws_automation.disco_logging import configure_logging


def get_password_from_console():
    """Reads a password from the console"""
    password1 = getpass()
    password2 = getpass()
    if password1 != password2:
        print("!Password does not match!")
        sys.exit(2)
    return password1


def password_hash(password=None):
    """Computes a sha512 password hash from a password"""
    from passlib.hash import sha512_crypt
    password = password or get_password_from_console()
    return sha512_crypt.encrypt(password)


def run():
    """Parses command line and dispatches the commands"""
    config = read_config()

    args = docopt(__doc__)

    configure_logging(args["--debug"])

    if args["hashpassword"]:
        print(password_hash())
        sys.exit(0)

    bucket_name = args.get("--bucket") or DiscoVPC.get_credential_buckets_from_env_name(
        config, args["--env"])[0]
    s3_accounts = S3AccountBackend(DiscoS3Bucket(bucket_name))

    if args["install"]:
        s3_accounts.install_all()
    elif args["adduser"]:
        username = args["--name"] or os.environ.get("USER")
        user_template = s3_accounts.new_user_config(password_hash(args["--password"]))
        group_config = s3_accounts.new_group_config()
        user_config = s3_accounts.edit_account_config(user_template)
        s3_accounts.add_account(username, user_config)
        s3_accounts.add_account(username, group_config)
    elif args["addgroup"]:
        group_config = s3_accounts.new_group_config()
        s3_accounts.add_account(args["--name"], group_config)
    elif args["edituser"]:
        username = args["--name"] or os.environ.get("USER")
        user_config = s3_accounts.get_user_config(username)
        kwargs = {"active": args["--active"]} if args["--active"] else {}
        user_config = s3_accounts.edit_account_config(user_config, **kwargs)
        s3_accounts.add_account(username, user_config)
        s3_accounts.refresh_groups()
    elif args["editgroup"]:
        # there is nothing to edit for a group.. but..
        group_config = s3_accounts.get_group_config(args["--name"])
        group_config = s3_accounts.edit_account_config(group_config)
        s3_accounts.add_account(args["--name"], group_config)
    elif args["listgroups"]:
        print("\n".join(s3_accounts.list_groups()))
    elif args["listusers"]:
        print("\n".join(s3_accounts.list_users()))


if __name__ == "__main__":
    run_gracefully(run)
