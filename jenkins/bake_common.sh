#!/bin/false

JENKINS_URL=https://localhost/jenkins/
STATUS_JOB_NAME=bake-hostclass-STATUS

# Create log output directory
if [[ "$LOG_DIR" == "" ]] ; then
    LOG_DIR="bake_logs.${BUILD_NUMBER}"
fi
mkdir -p $LOG_DIR

if [[ "$MAX_ATTEMPTS" == "" ]] ; then
    MAX_ATTEMPTS=2
fi

if [[ "$TOKEN" == "" ]] ; then
    echo "This job requires the token of the $STATUS_JOB_NAME job."
    echo "Please set the TOKEN environment variable."
fi

source "$(dirname $0)/boto_init.sh" 2>&1 > /dev/null

function bake {
    local hostclass=$1
    local attempt=0
    local exit_code=1
    if [ "${BAKE_TO_STAGE}" != "" ]; then
        stage_arg="--stage=$BAKE_TO_STAGE"
    fi

    while [ "$exit_code" != "0" -a "$attempt" -lt "$MAX_ATTEMPTS" ]; do
        local log_file="$LOG_DIR/$hostclass.$attempt.log"
        echo "Baking $hostclass -- log at $log_file"
        disco_bake.py --debug bake --hostclass $hostclass --use-local-ip $stage_arg &> $log_file
        exit_code="$?"
        attempt=$((attempt+1))
    done

    if [[ "$exit_code" == "0" ]]; then
        curl -ksS -X POST $JENKINS_URL/job/$STATUS_JOB_NAME/build --data token=$TOKEN \
            --data-urlencode json="{\"parameter\": [{\"name\":\"HOSTCLASS\", \"value\":\"$hostclass\"}, {\"name\":\"SUCCEEDED\", \"value\":\"true\"}]}"
    else
        echo "Bake attempt $attempt for $hostclass failed, excerpt from $log_file:"
        tail -n 250 $log_file | sed "s/^/$hostclass $attempt: /"
        curl -ksS -X POST $JENKINS_URL/job/$STATUS_JOB_NAME/build --data token=$TOKEN \
            --data-urlencode json="{\"parameter\": [{\"name\":\"HOSTCLASS\", \"value\":\"$hostclass\"}, {\"name\":\"SUCCEEDED\", \"value\":\"false\"}]}"
    fi
}

function bake_hostclasses {
    local hostclasses=$*

    for hostclass in $hostclasses ; do
        bake $hostclass &
    done
    wait
}
