#!/usr/bin/env python
"""
Copies configuration files to the destination host and applies appropriate
ownership and permissions.
"""

import argparse
import logging
import os
import shutil
import errno
import pwd
import grp
import subprocess

logger = logging.getLogger(__name__)

HOSTCLASS_FILE_PATH = "/opt/wgen/etc/hostclass"
METADATA_FILE_NAME = "acfg.metadata"


def copy_tree(source, destination, hostclasses=None, dryrun=False):
    """Copies a file tree rooted at source to destination.
    Files that contain '~' are only copied if string after ~ matches hostclass and files
    not containing '~' are only copied if a hostclass specific version does not exist.

    This reads the hostclass from a hostclass file located at HOSTCLASS_FILE_PATH. If no
    such file exists, this raises an exception. The files will also be renamed, stripping ~
    and hostclass string after it. Permissions and file ownership are applied from
    source/METADATA_FILE_NAME, if it exists.

    For special cases (e.g. operating-system specific phase1 files), hostclasses may instead
    be passed as a list in the `hostclassses` argument.  If this argument is present, the content
    of the hostclass file is ignored, and the passed-in list is used as a priority-ordered list of
    virtual hostclasses to find the files for (so if the list is ['centos6_phase1', 'mhcgeneric'],
    files and permissions marked with either of those hostclasses will be included, but in case of
    conflict, 'centos6_phase1' will trump 'mhcgeneric').

    :param source:  the source tree root
    :param destination:  The destination tree root
    :param hostclasses:  A priority-ordered list of hostclasses to use instead the one in HOSTCLASS_FILE_PATH
    :param dryrun:  If this is true copy_tree() will log as normal but not actually copy anything.
    :raise HostclassFileNotFound: if no hostclass file was found
    """
    if not hostclasses:
        if os.path.exists(HOSTCLASS_FILE_PATH):
            with open(HOSTCLASS_FILE_PATH) as hostclass_file:
                hostclasses = [hostclass_file.readline().strip()]
        else:
            raise HostclassFileNotFound()

    _copy_files(source, destination, hostclasses, dryrun)
    _apply_metadata(source, destination, hostclasses, dryrun)


def _copy_files(source, destination, hostclasses, dryrun=False):
    # TODO Break this into smaller functions
    # pylint: disable=R0914,R0912

    for path, dirnames, filenames in os.walk(source):
        destpath = os.path.join(destination, os.path.relpath(path, source))

        for dirname in dirnames:
            spath = os.path.join(path, dirname)
            dpath = os.path.join(destpath, dirname)
            logger.info("make directory %s", dpath)
            if not dryrun:
                if not os.path.isdir(dpath):
                    os.makedirs(dpath)
                shutil.copystat(spath, dpath)

        sources = set()
        destinations = set()
        files = []

        src_for_file = {}
        for filename in filenames:
            if filename == METADATA_FILE_NAME:
                continue
            (realname, _, hostclass) = filename.partition("~")
            if hostclass and hostclass not in hostclasses:
                # this file is not relevant
                continue
            if realname not in src_for_file:
                src_for_file[realname] = filename
            else:
                (_, _, previous_hostclass) = src_for_file[realname].partition("~")
                if _higher_priority(hostclass, previous_hostclass, hostclasses):
                    src_for_file[realname] = filename

        # Add the host specific files first
        for destname, srcname in src_for_file.items():
            src = os.path.join(path, srcname)
            dst = os.path.join(destpath, destname)
            sources.add(src)
            destinations.add(dst)
            files += [(src, dst)]

        # Tell user what is being skipped
        for filename in filenames:
            src_file = os.path.join(path, filename)
            if src_file not in sources:
                logger.info("skipping: %s", os.path.realpath(src_file))

        # Tell user what is being copied, copy unless this is a dry run
        for (spath, dpath) in files:
            logger.info("copying %s to %s", os.path.realpath(spath), dpath)
            if not dryrun:
                shutil.copy(spath, dpath)


def _apply_metadata(source, destination, hostclasses, dryrun=False):
    # Pylint thinks this function has too many local variables
    # pylint: disable=R0914
    metadata_file_path = os.path.join(source, METADATA_FILE_NAME)
    if not os.path.exists(metadata_file_path):
        logger.warning("Metadata file not found: %s, no permissions to apply.", metadata_file_path)
        return

    logger.info("Applying metadata from %s to files in %s", metadata_file_path, destination)
    hostclass_for_path = {}
    with open(metadata_file_path, 'r') as metadata_file:
        for line in metadata_file.readlines():
            file_metadata = line.partition('#')[0].strip()  # ignore comments and blank lines
            if not file_metadata:
                continue
            try:
                file_entry, permissions, owner, group = file_metadata.split()
            except Exception:
                raise ValueError("Not enough values to unpack on line: {0}".format(file_metadata))

            (file_entry_path, _, hostclass) = file_entry.partition("~")

            if hostclass and hostclass not in hostclasses:
                logger.info("skipping permissions: %s", file_entry)
                continue

            # file_entry_path will be absolute: strip the leading slash, then look in the
            # destination directory (which may be "/") for it.
            filename = os.path.join(destination, file_entry_path[1:])
            if not os.path.exists(filename):
                logger.warning("skipping permissions (file not found): %s", filename)
                continue

            if filename not in hostclass_for_path:
                hostclass_for_path[filename] = hostclass
                logger.info("setting ownership of %s to %s:%s", filename, owner, group)
            else:
                if _higher_priority(hostclass, hostclass_for_path[filename], hostclasses):
                    hostclass_for_path[filename] = hostclass
                    logger.info("OVERRIDING ownership of %s to %s:%s", filename, owner, group)
                else:
                    logger.info("Skipping permissions from hostclass %s on %s", hostclass, filename)
                    continue

            if not dryrun:
                uid, gid = get_or_create_ids(owner, group)
                os.chown(filename, uid, gid)

            logger.info("setting permissions of %s to %s", filename, permissions)
            if not dryrun:
                os.chmod(filename, int(permissions, 8))


def _higher_priority(new_hostclass, old_hostclass, all_hostclasses):
    if new_hostclass:
        if old_hostclass:
            return all_hostclasses.index(new_hostclass) < all_hostclasses.index(old_hostclass)
        else:
            return True
    else:
        return False


class HostclassFileNotFound(EnvironmentError):
    """
    An exception indicating that the hostclass file couldn't be found.
    """
    def __init__(self):
        super(HostclassFileNotFound, self).__init__(
            errno.ENOENT,
            "Couldn't find hostclass file {0}".format(HOSTCLASS_FILE_PATH))


def get_or_create_ids(username, groupname):
    """
    Get the UID and GID for a user and group, creating the user and group if necessary.
    Users are created with no login shell: if they need a shell, downstream init scripts
    should update it.
    """
    try:
        gid = grp.getgrnam(groupname).gr_gid
    except KeyError:
        logger.info("Creating group %s", groupname)
        subprocess.call(['/usr/sbin/groupadd', '-f', groupname])
        gid = grp.getgrnam(groupname).gr_gid
    try:
        uid = pwd.getpwnam(username).pw_uid
    except KeyError:
        logger.info("Creating user %s", username)
        subprocess.call(['/usr/sbin/adduser',
                         '-r',
                         '--gid', str(gid),
                         '--shell', '/sbin/nologin',
                         username])
        uid = pwd.getpwnam(username).pw_uid
    return uid, gid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="""
    Tool for copying trees of files. Files that contain '~' are only copied if
    the string after ~ matches the hostclass (taken from command line options,
    or from /opt/wgen/etc/hostclass if no option is supplied). The files will
    also be renamed, stripping the "~somehostclass" suffix if present.
    Permissions and file ownership are applied from the acfg.metadata file at
    the root of the source tree.
    """)
    parser.add_argument("source", help="Source location overlay directory")
    parser.add_argument("destination", help="Destination of where to overlay source directory over")
    parser.add_argument('--dry', action='store_const', const=True, default=False, help='Dry run only')
    parser.add_argument('--hostclass', action='append', dest='hostclasses', metavar='HOSTCLASS',
                        help='A hostclass (possibly virtual) for which to select files. (Repeatable.)')

    logger.setLevel(logging.INFO)

    args = parser.parse_args()
    copy_tree(args.source, args.destination, args.hostclasses, args.dry)
