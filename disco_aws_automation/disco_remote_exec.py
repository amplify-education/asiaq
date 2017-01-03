"""
ssh and rsync code
"""
from __future__ import print_function
import logging
import subprocess
import os
import stat
import shutil
import tempfile
import socket

from boto.exception import S3ResponseError

from .disco_creds import DiscoS3Bucket, SSH_PRIVATE_KEY_BUCKET_PREFIX
from .exceptions import CommandError

logger = logging.getLogger(__name__)

SSH_DEFAULT_OPTIONS = ["-oBatchMode=yes", "-oStrictHostKeyChecking=no", '-oUserKnownHostsFile=/dev/null']


class DiscoRemoteExec(object):
    """
    Wrapper class for ssh and rsync.  Adds ssh keys to the user's ssh-agent on
    initialization.
    """

    def __init__(self, credential_buckets):
        DiscoRemoteExec.add_ssh_keys(credential_buckets)

    @staticmethod
    def add_ssh_keys_in_bucket(tempdir, bucket_name):
        """
        Grabs all the private ssh keys we have access to in the named bucket and
        adds them to our ssh keychain.
        """
        try:
            s3_bucket = DiscoS3Bucket(bucket_name)
        except S3ResponseError:
            # It is ok if the keys don't exist, but log something to debug
            logger.info("Found no ssh keys at %s", SSH_PRIVATE_KEY_BUCKET_PREFIX)
            return

        for key in s3_bucket.listkeys(SSH_PRIVATE_KEY_BUCKET_PREFIX):
            keyfile = tempdir + '/' + key
            # write the ssh key to a temporary file
            try:
                s3_bucket.get_contents_to_file(key, keyfile)
                os.chmod(keyfile, os.stat(keyfile).st_mode & (stat.S_IREAD | stat.S_IWRITE))
                if os.system("ssh-add {0} 2> /dev/null".format(keyfile)) != 0:
                    raise CommandError("Failed to add {0} key".format(keyfile))
                logger.debug("Added ssh key %s", key)
            except S3ResponseError:
                logger.info("Failed to add ssh key %s", key)

    @staticmethod
    def add_ssh_keys(credential_buckets):
        """
        Grabs private ssh keys from each of our credential buckets and adds
        them to our ssh keychain.
        """
        try:
            # Create temporary directory for the ssh key files
            tempdir = tempfile.mkdtemp()
            full_temp_path = tempdir + '/' + SSH_PRIVATE_KEY_BUCKET_PREFIX
            if not os.path.exists(full_temp_path):
                os.makedirs(full_temp_path)

            # Add keys from each of our credential buckets
            for bucket_name in credential_buckets:
                logger.debug("Adding keys from %s", bucket_name)
                DiscoRemoteExec.add_ssh_keys_in_bucket(tempdir, bucket_name)

        finally:
            # Remove the temporary directory
            shutil.rmtree(tempdir)

    # R0914 Allow more than 15 local variables so we can pass a lot of options to ssh
    # pylint: disable=R0914
    @staticmethod
    def remotecmd(address, remote_command, user, stdin=None,
                  nothrow=False, jump_address=None, log_on_error=None,
                  ssh_options=(), forward_agent=None):
        """
        Runs the passed in command on a remote host, via a jump host if a jump_address
        is provided and if the address is not reachable without a jump host.

        Returns a tuple containing the return code and the standard output from the command.
        """
        is_reachable = DiscoRemoteExec._is_reachable(address)

        if not is_reachable and not jump_address and not nothrow:
            raise CommandError('Unable to run command {0}. {1} host is not reachable'
                               .format(remote_command, address))

        use_jump_address = jump_address and not is_reachable
        command = DiscoRemoteExec._get_remote_exec_command(address, remote_command, user,
                                                           jump_address if use_jump_address else None,
                                                           ssh_options, forward_agent)
        logger.debug("command: %s", command)

        # output subprocess into a file to bypass pipe buffer size limitation,
        # which might cause subprocess hanging, see
        # https://thraxil.org/users/anders/posts/2008/03/13/Subprocess-Hanging-PIPE-is-your-enemy/
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command,
                                       stdin=subprocess.PIPE,
                                       stdout=output,
                                       stderr=subprocess.STDOUT)
            process.communicate(stdin)
            output.seek(0)
            stdout = output.read()
            logger.debug(stdout)
            if (not nothrow) and (process.returncode != 0):
                if log_on_error:
                    logger.error(stdout)
                raise CommandError("command: {0} returned {1}".format(
                    " ".join(command), process.returncode))
            return (process.returncode, stdout)

    @staticmethod
    def _get_remote_exec_command(address, remote_command, user, jump_address, ssh_options, forward_agent):
        """
        Get the SSH command to use to run a remote command
        Args:
            address (str): Address of the remote host
            remote_command (List[str]): The command and arguments to run
                                        or an empty list if commands will be taken from stdin
            user (str): The user to run command as
            jump_address (str): Address of the jump box to use if unable to connect directly to the host
            ssh_options (List[str]): List of extra ssh options
            forward_agent (bool): Enable forwarding agent

        Returns List[str]: The ssh command and its arguments in a list
        """
        common_flags = ["-oConnectTimeout=10"]
        common_flags.extend(SSH_DEFAULT_OPTIONS)
        if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
            common_flags.append("-v")

        # Build up command to get from tunnel to final dest
        final_hop = ["ssh"]
        final_hop.extend(common_flags)
        if forward_agent:
            final_hop.extend(["-A"])
        final_hop.append("-l{0}".format(user))
        final_hop.append(address)
        final_hop.extend(ssh_options)

        if jump_address:
            final_hop.append("'{0}'".format(" ".join(remote_command)))
        elif remote_command:
            final_hop.extend(remote_command)

        if jump_address:
            # Build up command to get to tunnel host
            proxy_hop = ["ssh", "-A", "-t", "{0}@{1}".format(user, jump_address)]
            proxy_hop.extend(common_flags)

            command = []
            command.extend(proxy_hop)
            command.append(" ".join(final_hop))
        else:
            command = final_hop

        return command

    @staticmethod
    def rsync(address, source, destination, user, nothrow=False):
        """Efficiently copies data from a source to a destination as a specific user"""
        local_command = ["rsync", "-a", "-v", "-z", "--delete"]

        # config ssh call
        ssh_options = "ssh {0} -oConnectTimeout=10".format(" ".join(SSH_DEFAULT_OPTIONS))
        local_command.append("-e")
        local_command.append(ssh_options)

        user_string = "{0}@".format(user) if user else ""
        full_destination = "{0}{1}:{2}".format(user_string, address, destination)

        local_command.append(source)
        local_command.append(full_destination)
        logger.debug("command: %s", local_command)

        # Try syncing with filter flag if we get syntax error assume its not
        # supported, then try without it.
        rsync_exit_code = DiscoRemoteExec._call_rsync(local_command + ["--filter=:e /.nosync"])
        if rsync_exit_code == 1:
            logger.debug("rsync failed due to 'Syntax or usage error.' Attempting without filter.")
            rsync_exit_code = DiscoRemoteExec._call_rsync(local_command)
        if (not nothrow) and (rsync_exit_code != 0):
            raise CommandError("command: rsync of {0} to {1} on {2} failed with exit code {3}".format(
                source, destination, address, rsync_exit_code))
        return rsync_exit_code

    @staticmethod
    def _call_rsync(command):
        if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
            return subprocess.call(command)
        else:
            with open("/dev/null", "w") as devnull:
                return subprocess.call(command, stdout=devnull, stderr=devnull)

    @staticmethod
    def _is_reachable(ip_address):
        """Returns True if we can connect to port 22 at the given ip address"""
        if not ip_address:
            return False
        logger.info("Probing %s", ip_address)
        try:
            sock = socket.create_connection((ip_address, 22), timeout=2)
            sock.close()
            return True
        except (socket.timeout, socket.error):
            return False

    if __name__ == "__main__":
        print("This is a library. Nothing to run.")
        import sys
        sys.exit(1)
