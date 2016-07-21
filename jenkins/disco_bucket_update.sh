#!/bin/bash -ex

# Note that this script must be run by the account's root, or someone with "s3:*" IAM permissions

REGION="$1"
ACCOUNT="$2"
DEV_CANONICAL_ID="$(grep -i dev_canonical_id disco_aws.ini | cut -d= -f 2)"
PROD_CANONICAL_ID="$(grep -i  prod_canonical_id disco_aws.ini | cut -d= -f 2)"
PROJECT="$(grep -i project disco_aws.ini | cut -d= -f 2)"
CANONICAL_ID="" # this will be set the first time we look at a bucket

function print_help() {
    echo "This will create or update the buckets that have lifecycles under iam/s3/ACCOUNT/"
    echo "  if a .iam file exists it will be applied as the bucket policy"
    echo "  if a .web file exists it will be applied as the website-configuration"
    echo "  if a .logging file exists it will be applied as the logging status"
    echo
    echo "  A bucket name containing REGION will have that string replaced with it's region"
    echo
    echo "  Default iam, web and logging configurations can be created using a default.X file"
    echo "  such a file will be used if a more specific file does not exist for a bucket."
    echo "  occurrences of $ACCOUNT, $REGION and $BUCKET_NAME in the default file will be"
    echo "  substituted before the file is used."
    echo
    echo "  The first parameter is the name of the region in AWS to create the buckets in [us-west-1,us-east-1,etc]"
    echo "  The second parameter is the account to create the buckets for [prod,dev,audit]"
}

if [[ "$1" == "-h" || "$1" == "--help" || "$2" == "" ]] ; then
    print_help
    exit 1
fi

if [[ "$(which jq)" == "" ]] ; then
    echo "Must install jq json parser to run this script"
    echo "See: http://linuxpedia.net/how-to-parse-json-string-via-command-line-on-linux/"
    echo "On Ubuntu: apt-get install jq"
    exit 1
fi

function interpret_file() {
    # Prints file after substituting $ACCOUNT, $REGION, $PROJECT, $CANONICAL_ID
    # $DEV_CANONICAL_ID, $PROD_CANNONCIAL_ID and $BUCKET_NAME
    local input_file="$1"
    local bucket_name="$2"
    cat $1 | sed -e "s/\$ACCOUNT/$ACCOUNT/g" \
        -e "s/\$REGION/$REGION/g" \
        -e "s/\$PROJECT/$PROJECT/g" \
        -e "s/\$DEV_CANONICAL_ID/$DEV_CANONICAL_ID/g" \
        -e "s/\$PROD_CANONICAL_ID/$PROD_CANONICAL_ID/g" \
        -e "s/\$CANONICAL_ID/$CANONICAL_ID/g" \
        -e "s/\$BUCKET_NAME/$bucket_name/g"
}

function get_bucket_region() {
    # prints a buckets region
    local region="$(aws s3api get-bucket-location --bucket "$1" | jq -r .LocationConstraint)"
    if [[ "$region" == "null" ]] ; then
        echo "us-east-1"
    else
        echo "$region"
    fi
}

function update_bucket() {
    local region=$1
    local project=$2
    local account=$3
    local existing_buckets=$4
    local lifecycle_file=$5

    local base_dir=$(dirname $lifecycle_file)
    local base_file=$(echo $lifecycle_file | sed -e 's/.lifecycle//')

    local bucket_name=$(echo $(basename $lifecycle_file .lifecycle) | sed -e "s/REGION/$region/" | sed -e "s/PROJECT/$project/")

    # Create the bucket if it doesn't exist
    if [[ ! $existing_buckets =~ $bucket_name ]] ; then
        local bucket_region=$region
        echo Creating: $bucket_name in $bucket_region
        aws s3 --region $region mb "s3://$bucket_name"
    else
        local bucket_region=$(get_bucket_region $bucket_name)
        echo Updating: $bucket_name in $bucket_region
    fi

    # Determine our Canonical ID
    if [[ "$CANONICAL_ID" == "" ]] ; then
        CANONICAL_ID="$(aws --region $bucket_region s3api get-bucket-acl --bucket $bucket_name | jq -r .Owner.ID)"
    fi

    if [ -s "$lifecycle_file" ] ; then
        # Apply the lifecycle policy
        aws s3api --region $bucket_region put-bucket-lifecycle --bucket $bucket_name \
            --lifecycle-configuration file://$lifecycle_file
    fi

    # Apply bucket policy, if one exists
    if [[ -e ${base_file}.iam ]] ; then
        local policy="$(interpret_file ${base_file}.iam $bucket_name)"
    elif [[ -e ${base_dir}/default.iam ]] ; then
        local policy="$(interpret_file ${base_dir}/default.iam $bucket_name)"
    fi
    if [[ "$policy" != "" ]] ; then
        aws s3api --region $bucket_region put-bucket-policy --bucket $bucket_name \
            --policy "$policy"
    fi

    # Apply bucket access control policy, if one exists
    if [[ -e ${base_file}.acp ]] ; then
        local acp="$(interpret_file ${base_file}.acp $bucket_name)"
    elif [[ -e ${base_dir}/default.acp ]] ; then
        local acp="$(interpret_file ${base_dir}/default.acp $bucket_name)"
    fi
    if [[ "$acp" != "" ]] ; then
        aws --region $bucket_region s3api put-bucket-acl --bucket $bucket_name \
            --access-control-policy "$acp"
    fi

    # Apply versioning configuration, if one exists
    if [[ -e ${base_file}.versioning ]] ; then
        local ver_config="$(interpret_file ${base_file}.versioning $bucket_name)"
    elif [[ -e ${base_dir}/default.versioning ]] ; then
        local ver_config="$(interpret_file ${base_dir}/default.versioning $bucket_name)"
    fi
    if [[ "$ver_config" != "" ]] ; then
        aws s3api --region $bucket_region put-bucket-versioning --bucket $bucket_name \
            --versioning-configuration "$ver_config"
    fi

    # Set the website configuration, if one exists
    if [[ -e ${base_file}.web ]] ; then
        local web_config="$(interpret_file ${base_file}.web $bucket_name)"
    elif [[ -e ${base_dir}/default.web ]] ; then
        local web_config="$(interpret_file ${base_dir}/default.web $bucket_name)"
    fi
    if [[ "$web_config" != "" ]] ; then
        aws s3api --region $bucket_region put-bucket-website --bucket $bucket_name \
            --website-configuration "$web_config"
    fi

    # Set the logging policy, if one exists
    if [[ -e ${base_file}.logging ]] ; then
        local logging_policy="$(interpret_file ${base_file}.logging $bucket_name)"
    elif [[ -e ${base_dir}/default.logging ]] ; then
        local logging_policy="$(interpret_file ${base_dir}/default.logging $bucket_name)"
    fi
    if [[ "$logging_policy" != "" ]] ; then
        local target_bucket=$(echo $logging_policy | jq -r .LoggingEnabled.TargetBucket)
        local target_region=$(get_bucket_region $target_bucket)
        if [[ "$target_region" == "$bucket_region" ]] ; then
            aws s3api --region $bucket_region put-bucket-logging --bucket $bucket_name \
                --bucket-logging-status "$logging_policy"
        else
            echo "Can't log from $bucket_name to $target_bucket, region mismatch"
        fi
    fi

# Commented out since we don't use this and it could be a security concern.
#    # Run custom setup script for bucket, if it exists
#    local shell_script=$(mktemp -t temp.sh.XXXXXX)
#    if [[ -e ${base_file}.sh ]] ; then
#        interpret_file ${base_file}.sh $bucket_name > $shell_script
#    elif [[ -e ${base_dir}/default.sh ]] ; then
#        interpret_file ${base_dir}/default.sh $bucket_name > $shell_script
#    fi
#    if [[ "$(cat $shell_script)" != "" ]] ; then
#        chmod a+x $shell_script
#        $shell_script
#    fi
#    rm $shell_script
}

source "$(dirname $0)/boto_init.sh"

# Try to install latest AWS CLI, but proceed with whatever we have if it fails
set +e
pip install awscli > /dev/null
set -e

BASE=$PWD

export LIFECYCLE_FILES=$(ls ${BASE}/iam/s3/$ACCOUNT/*.lifecycle)

export EXISTING_BUCKETS=$(aws s3 --region $REGION ls | grep -o "[a-zA-Z].*$")

echo Pre-existing buckets: $EXISTING_BUCKETS
echo


file_counts=$(ls ${BASE}/iam/s3/$ACCOUNT/*s3audit*.lifecycle|wc -l)
if [[ "$file_counts" -ne '1' ]]; then
    echo "There are more than 1 audit lifecycle file, something is wrong!"
    exit 1
fi

S3_LIFECYCLE_FILE=$(ls ${BASE}/iam/s3/$ACCOUNT/*s3audit*.lifecycle)
update_bucket $REGION $PROJECT $ACCOUNT "$EXISTING_BUCKETS" $S3_LIFECYCLE_FILE

aws s3api put-bucket-acl --region $REGION \
    --bucket $REGION.$PROJECT.s3audit.$ACCOUNT \
    --grant-write 'URI="http://acs.amazonaws.com/groups/s3/LogDelivery"' \
    --grant-read-acp 'URI="http://acs.amazonaws.com/groups/s3/LogDelivery"'

for LIFECYCLE_FILE in $LIFECYCLE_FILES ; do
    update_bucket $REGION $PROJECT $ACCOUNT "$EXISTING_BUCKETS" $LIFECYCLE_FILE
done

echo
echo Buckets after update: $(aws s3 --region $REGION ls | grep -o "[a-zA-Z].*$")
echo
echo Our Canonical ID is $CANONICAL_ID
