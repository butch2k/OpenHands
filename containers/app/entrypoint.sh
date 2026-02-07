#!/bin/bash
set -eo pipefail

echo "Starting OpenHands..."
if [[ $NO_SETUP == "true" ]]; then
  echo "Skipping setup, running as $(whoami)"
  "$@"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "The OpenHands entrypoint.sh must run as root"
  exit 1
fi

if [ -z "$SANDBOX_USER_ID" ]; then
  echo "SANDBOX_USER_ID is not set"
  exit 1
fi

if [ -z "$WORKSPACE_MOUNT_PATH" ]; then
  # This is set to /opt/workspace in the Dockerfile. But if the user isn't mounting, we want to unset it so that OpenHands doesn't mount at all
  unset WORKSPACE_BASE
fi

if [[ "$INSTALL_THIRD_PARTY_RUNTIMES" == "true" ]]; then
  echo "Downloading and installing third_party_runtimes..."
  echo "Warning: Third-party runtimes are provided as-is, not actively supported and may be removed in future releases."

  if pip install 'openhands-ai[third_party_runtimes]' -qqq 2> >(tee /dev/stderr); then
    echo "third_party_runtimes installed successfully."
  else
    echo "Failed to install third_party_runtimes." >&2
    exit 1
  fi
fi

# ============================================================
# Privileged operations phase (running as root)
# All user creation, group management, and directory permission
# changes are performed here before dropping privileges.
# ============================================================

if [[ "$SANDBOX_USER_ID" -eq 0 ]]; then
  echo "Running OpenHands as root"
  export RUN_AS_OPENHANDS=false
  exec "$@"
fi

# --- Non-root sandbox user setup (privileged ops) ---

echo "Setting up enduser with id $SANDBOX_USER_ID"
if id "enduser" &>/dev/null; then
  echo "User enduser already exists. Skipping creation."
else
  if ! useradd -l -m -u "$SANDBOX_USER_ID" -s /bin/bash enduser; then
    echo "Failed to create user enduser with id $SANDBOX_USER_ID. Moving openhands user."
    incremented_id=$(("$SANDBOX_USER_ID" + 1))
    usermod -u "$incremented_id" openhands
    if ! useradd -l -m -u "$SANDBOX_USER_ID" -s /bin/bash enduser; then
      echo "Failed to create user enduser with id $SANDBOX_USER_ID for a second time. Exiting."
      exit 1
    fi
  fi
fi
usermod -aG openhands enduser

# SECURITY NOTE: The Docker socket (/var/run/docker.sock) is mounted into this
# container so that OpenHands can create and manage sandbox containers for code
# execution. Access to the Docker socket is equivalent to root access on the
# host, so it is critical that:
#   1. Only trusted users/processes have access to this container.
#   2. The socket is not exposed beyond this orchestrating container.
#   3. The enduser is added to the socket's group solely to manage sandboxes.
DOCKER_SOCKET_GID=$(stat -c '%g' /var/run/docker.sock)
echo "Docker socket group id: $DOCKER_SOCKET_GID"
if getent group "$DOCKER_SOCKET_GID"; then
  echo "Group with id $DOCKER_SOCKET_GID already exists"
else
  echo "Creating group with id $DOCKER_SOCKET_GID"
  groupadd -g "$DOCKER_SOCKET_GID" docker
fi

mkdir -p /home/enduser/.cache/huggingface/hub/
chown -R enduser:enduser /home/enduser/.cache

usermod -aG "$DOCKER_SOCKET_GID" enduser

# ============================================================
# Privilege drop phase
# All privileged operations are complete. Use gosu to exec as
# the unprivileged enduser, replacing the current root process.
# gosu is preferred over su because it uses exec and avoids
# leaving a root-owned parent process or TTY issues.
# ============================================================

echo "Dropping privileges to enduser"
exec gosu enduser "$@"
