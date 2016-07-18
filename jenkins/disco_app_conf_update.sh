#!/bin/bash -ex

RUN_DIR=$PWD

function print_help() {
    echo "usage: $0 <env>"
    echo
    echo "Updates environment specific application configuration"
    echo "Data is taken from jenkins/../app_conf/<env>/"
}

if [[ "$1" == "-h" || "$1" == "--help" || "$1" == "" ]] ; then
    print_help
    exit 1
fi

source /opt/wgen/bin/use_asiaq_from_rpm.sh

env="$1"
conf_dir="$RUN_DIR/app_conf/${env}"
confs=$(find -L $conf_dir -type f |sed 's/\.\///;')

for conf in $confs; do
    key=$(echo $conf | grep -o "app_conf.*$" | sed -e "s.app_conf/$env.app_conf.")
    echo "Updating $key in $env bucket"
    cat $conf | disco_creds.py --env $env set --key "$key"  --value -
done
