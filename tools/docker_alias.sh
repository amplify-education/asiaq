#!/usr/bin/env bash

docker run --rm -it \
    --volume "$HOME/.aws/credentials:/root/.aws/credentials:ro" \
    --volume "$(pwd):/project/asiaq_config" \
    --volume "/run/host-services/ssh-auth.sock:/run/host-services/ssh-auth.sock" \
    --env SSH_AUTH_SOCK="/run/host-services/ssh-auth.sock" \
    --env AWS_PROFILE="$AWS_PROFILE" \
    --env SPOTINST_TOKEN="$SPOTINST_TOKEN" \
    --env LOGNAME="$(whoami)" \
    --entrypoint "/usr/local/bin/$(basename $0)" \
    asiaq:latest \
    "$@"
