#!/bin/bash
#
# This script performs a complete build of the egg,
# creating a virtualenv, installing dependencies,
# linting and running tests, then publishing to the Pynest
# and (optionally) creating an RPM.
#
# It is intended to be run on the Jenkins server,
# but can be run locally as well.
#
# On the Jenkins server, the environment variables below
# will have been set. In a local build, they are set
# to reasonable values.
#
# The only env variable you must set yourself for a
# local build is WG_RPMBUILD, which should point
# to the location of wg_rpmbuild.py, from rpmtools
#
export PYNEST=${PYNEST:-/tmp}
export PATH=/opt/wgen-3p/python27/bin:$PATH
export PATH=/opt/wgen-3p/ruby-1.9/bin:$PATH
export WORKSPACE=${WORKSPACE:-$PWD}
export PIP_LOG=${PIP_LOG:-$WORKSPACE/pip.log}
export BUILD_NUMBER=${BUILD_NUMBER:-1}
export CI_REPO=${CI_REPO:-/tmp}
# Deprecation warning added March 2014
if [[ "$DOCUMENTATION_PACKAGE_BASE" == "" && "$DOCUMENTATION_ROOT" != "" ]] ; then
    export DOCUMENTATION_PACKAGE_BASE="$DOCUMENTATION_ROOT"
    echo "WARNING: DOCUMENTATION_ROOT has been replaced with DOCUMENTATION_PACKAGE_BASE"
    echo "NOTE: You may want to use DOCUMENTATION_BASE instead."
fi
# Set DOCUMENTATION_BASE globally and use DOCUMENTATION_PACKAGE_BASE
# when you want to to specify the exact location of the documentation and
# not just place it in DOCUMENTATION_BASE/PACKAGE_NAME
if [[ "$DOCUMENTATION_PACKAGE_BASE" == "" && "$DOCUMENTATION_BASE" != "" ]] ; then
    export PACKAGE=$(ruby -r "$PWD/tasks/project.rb" -e "puts Project::PACKAGE")
    export DOCUMENTATION_PACKAGE_BASE="$DOCUMENTATION_BASE/$PACKAGE"
fi
export DOCUMENTATION_PACKAGE_BASE=${DOCUMENTATION_PACKAGE_BASE:-/tmp/docs}
# WG_RPMBUILD we don't have a default value for

# disco_remote_exec.py needs a running ssh-agent, so start one
eval $(ssh-agent)
trap "ssh-agent -k" EXIT

rake virtualenv:create
source venv/bin/activate
pip install --upgrade setuptools
pip install --upgrade pip
rake jenkins:build_no_docs
