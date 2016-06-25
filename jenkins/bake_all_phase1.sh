#!/bin/bash

source "$(dirname $0)/bake_common.sh"  > /dev/null 2>&1

phase1_images=$(python -c 'from ConfigParser import ConfigParser; c = ConfigParser(); c.read(["disco_aws.ini"]); print " ".join([s for s in c.sections() if c.has_option(s, "phase") and c.get(s,"phase") == "1"])')
exit_status=0
RETRY_DELAY=30

function all_testable {
    local images=$1

    for image_name in $phase1_images; do
        ami=$(disco_deploy.py list --testable --hostclass "$image_name" | awk '{print $1}')
        if [[ "$ami" == "" ]]; then
            echo "$image_name is not testable" 1>&2
            echo "0"
            return
        fi
    done
    echo "1"
}

echo "Baking phase 1 AMIs"
bake_hostclasses $phase1_images

echo "Waiting for newly baked AMIs to become testable"
for I in 0 1 2 3 4 5 6 7 8 9 10 ; do
    if [[ "$(all_testable $phase1_images)" == "0" ]] ; then
        echo "Not all phase 1 images are testable, sleeping $RETRY_DELAY seconds"
        sleep $RETRY_DELAY
    else
        break
    fi
done

echo "Promoting phase 1 AMIs"
for image_name in $phase1_images; do
    ami=$(disco_deploy.py list --testable --hostclass "$image_name" | awk '{print $1}')
    if [[ "$ami" != "" ]]; then
        if ! disco_bake.py promote --ami "$ami" --stage tested; then
            echo "$image_name phase 1 promote failed"
            exit_status=1
        fi
    else
        echo "$image_name phase 1 bake failed"
        exit_status=1
    fi
done

exit $exit_status
