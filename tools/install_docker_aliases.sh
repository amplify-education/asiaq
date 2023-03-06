#!/usr/bin/env bash

# Make sure we are in the asiaq directory
cd "$(dirname "$0")/../"
ASIAQ_DIR=$(pwd)

# Build the asiaq docker file with latest changes
if ! docker_output=$(docker build . -t asiaq:latest 2>&1) ; then
    echo "Error building docker image!"
    echo "$docker_output"
    exit 1
fi

# Create a bin directory for the user if it doesn't exist
mkdir -p "$HOME/bin"

# Warn if bin directory not on path
if [[ ":$PATH:" != *":$HOME/bin:"* ]]; then
  echo "Looks like $HOME/bin is not on your PATH. You probably want to add it with the below snippet:"
  echo $'echo \'export PATH="$PATH:$HOME/bin"\' >> ~/.bashrc'
fi

# Create aliases for asiaq commands in bin directory
find bin -perm +111 -type f -execdir ln -s -f "$ASIAQ_DIR/tools/docker_alias.sh" "$HOME/bin/$(basename {})" ';'
