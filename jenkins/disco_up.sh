#!/bin/bash

function usage() {
    echo "Create environment and spinup the pipeline"
    echo "Usage: $0 environment_name environment_type [pipeline_name]"
    echo "If pipeline param is omited, then we assume its same as environment name."
    exit 1
}

if [[ "$#" -lt "2" ]] || [[ "$#" -gt "3" ]]; then
    usage
fi

source "$(dirname $0)/boto_init.sh" $1

DISCO_VPC="disco_vpc_ui.py"
DISCO_AWS="disco_aws.py"
DISCO_ELASTICACHE="disco_elasticache.py"
ENV_NAME=$1
ENV_TYPE=$2
PIPELINE=${3:-$ENV_NAME}

function error() {
    echo "Encountered an error"
    echo "Destroying vpc $ENV_NAME"
    $DISCO_VPC destroy --name $ENV_NAME
    exit 1
}

if [[ $($DISCO_VPC list | grep "[[:space:]]$ENV_NAME[[:space:]]") != "" ]]; then
    # eventually we may want to have this kill everything and recreate the vpc, or add VPC-update functionality
    echo "VPC $ENV_NAME already exists. Refusing to kill all hosts and recreate."
    exit 1
fi

trap error ERR
$DISCO_VPC create --type $ENV_TYPE --name $ENV_NAME
$DISCO_ELASTICACHE --env $ENV_NAME update
if [[ "${ENV_NAME,,}" != "ci" ]]; then
    $DISCO_AWS --env $ENV_NAME --debug spinup --pipeline ${ASIAQ_CONFIG:-.}/pipelines/${PIPELINE}.csv
fi
