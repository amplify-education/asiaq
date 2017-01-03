#!/bin/bash

source "$(dirname $0)/bake_common.sh"  > /dev/null 2>&1

phase1_images=$(python -c 'from ConfigParser import ConfigParser; c = ConfigParser(); c.read(["disco_aws.ini"]); print " ".join([s for s in c.sections() if c.has_option(s, "phase") and c.get(s,"phase") == "1"])')
RETRY_DELAY=30
BAKE_TO_STAGE=tested

echo "Baking phase 1 AMIs"
bake_hostclasses $phase1_images
