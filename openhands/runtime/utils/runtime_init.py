# IMPORTANT: LEGACY V0 CODE - Deprecated since version 1.0.0, scheduled for removal April 1, 2026
# This file is part of the legacy (V0) implementation of OpenHands and will be removed soon as we complete the migration to V1.
# OpenHands V1 uses the Software Agent SDK for the agentic core and runs a new application server. Please refer to:
#   - V1 agentic core (SDK): https://github.com/OpenHands/software-agent-sdk
#   - V1 application server (in this repo): openhands/app_server/
# Unless you are working on deprecation, please avoid extending this legacy file and consult the V1 codepaths above.
# Tag: Legacy-V0
import os
import subprocess
import sys

from openhands.core.logger import openhands_logger as logger


def init_user_and_working_directory(
    username: str, user_id: int, initial_cwd: str
) -> int | None:
    """Create working directory and user if not exists.
    It performs the following steps effectively:
    * Creates the Working Directory:
        - Uses mkdir -p to create the directory.
        - Sets ownership to username:group (respects SANDBOX_GROUP_ID if set).
        - Adjusts permissions to be readable and writable by group and others.
    * User Verification and Creation:
        - Checks if the user exists using id -u.
        - If the user exists with the correct UID, it skips creation.
        - If the UID differs, it logs a warning and return an updated user_id.
        - If the user doesn't exist, it proceeds to create the user.
    * Sudo Configuration:
        - Appends a restricted sudoers line to /etc/sudoers granting
            passwordless sudo for apt-get and chown only.
        - Adds the user to the sudo group with the useradd command, handling
            UID conflicts by incrementing the UID if necessary.

    Args:
        username (str): The username to create.
        user_id (int): The user ID to assign to the user.
        initial_cwd (str): The initial working directory to create.

    Returns:
        int | None: The user ID if it was updated, None otherwise.
    """
    # If running on Windows, just create the directory and return
    if sys.platform == 'win32':
        logger.debug('Running on Windows, skipping Unix-specific user setup')
        logger.debug(f'Client working directory: {initial_cwd}')

        # Create the working directory if it doesn't exist
        os.makedirs(initial_cwd, exist_ok=True)
        logger.debug(f'Created working directory: {initial_cwd}')

        return None

    # if username is CURRENT_USER, then we don't need to do anything
    # This is specific to the local runtime
    if username == os.getenv('USER') and username not in ['root', 'openhands']:
        return None

    # Skip root since it is already created
    existing_user_id = -1
    if username != 'root':
        # Check if the username already exists
        logger.debug(f'Attempting to create user `{username}` with UID {user_id}.')
        setup_user = True
        try:
            result = subprocess.run(
                ['id', '-u', username], check=True, capture_output=True
            )
            existing_user_id = int(result.stdout.decode().strip())

            # The user ID already exists, skip setup
            if existing_user_id == user_id:
                logger.debug(
                    f'User `{username}` already has the provided UID {user_id}. Skipping user setup.'
                )
            else:
                logger.warning(
                    f'User `{username}` already exists with UID {existing_user_id}. Skipping user setup.'
                )
            setup_user = False
        except subprocess.CalledProcessError as e:
            # Returncode 1 indicates, that the user does not exist yet
            if e.returncode == 1:
                logger.debug(
                    f'User `{username}` does not exist. Proceeding with user creation.'
                )
            else:
                logger.error(
                    f'Error checking user `{username}`, skipping setup:\n{e}\n'
                )
                raise

        if setup_user:
            # Add sudoer - restricted to apt-get and chown only for least-privilege
            sudoer_line = '%sudo ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/apt, /bin/chown, /usr/bin/chown\n'
            try:
                with open('/etc/sudoers', 'a') as f:
                    f.write(sudoer_line)
            except OSError as e:
                raise RuntimeError(f'Failed to add sudoer: {e}')
            logger.debug('Added restricted sudoer line successfully.')

            output = subprocess.run(
                [
                    'useradd', '-rm',
                    '-d', f'/home/{username}',
                    '-s', '/bin/bash',
                    '-g', 'root',
                    '-G', 'sudo',
                    '-u', str(user_id),
                    username,
                ],
                capture_output=True,
            )
            if output.returncode == 0:
                logger.debug(
                    f'Added user `{username}` successfully with UID {user_id}. Output: [{output.stdout.decode()}]'
                )
            else:
                raise RuntimeError(
                    f'Failed to create user `{username}` with UID {user_id}. Output: [{output.stderr.decode()}]'
                )

    # First create the working directory, independent of the user
    logger.debug(f'Client working directory: {initial_cwd}')
    old_umask = os.umask(0o002)
    try:
        os.makedirs(initial_cwd, exist_ok=True)
    finally:
        os.umask(old_umask)

    # Get group ID from environment variable, default to 'root' for backward compatibility
    group_id = os.getenv('SANDBOX_GROUP_ID', 'root')
    output = subprocess.run(
        ['chown', '-R', f'{username}:{group_id}', initial_cwd],
        capture_output=True,
    )
    out_str = output.stdout.decode()

    output = subprocess.run(
        ['chmod', 'g+rw', initial_cwd],
        capture_output=True,
    )
    out_str += output.stdout.decode()
    logger.debug(f'Created working directory. Output: [{out_str}]')

    return None if existing_user_id == -1 else existing_user_id
