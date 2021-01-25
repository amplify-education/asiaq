#Sets up the virtual env, keys and config to make boto work on jenkins.

SELF_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

function create_boto_config() {
    local boto_tmp_dir="$1"
    export BOTO_CONFIG="$boto_tmp_dir/boto.cfg"

    # If AWS credentials environment variables are set, place them in boto config
    if [[ "$AWS_ACCESS_KEY_ID" != "" && "$AWS_SECRET_ACCESS_KEY" != "" ]] ; then
        # Tell boto where to find config, and create the config
        cat > $BOTO_CONFIG <<EOF
[Credentials]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
    fi

    cat "$SELF_DIR/base_boto.cfg" >> $BOTO_CONFIG
}

function create_boto3_config() {
    local boto_tmp_dir="$1"
    export AWS_CONFIG_FILE="$boto_tmp_dir/aws.config"
    cat "$SELF_DIR/base_aws.config" >> $AWS_CONFIG_FILE

    # If AWS credentials environment variables are set, place them in aws.credentials
    if [[ "$AWS_ACCESS_KEY_ID" != "" && "$AWS_SECRET_ACCESS_KEY" != "" ]] ; then
        # Tell boto3 where to find config, and create the config
        export AWS_SHARED_CREDENTIALS_FILE="$boto_tmp_dir/aws.credentials"
        cat > $AWS_SHARED_CREDENTIALS_FILE <<EOF
[default]
aws_access_key_id = $AWS_ACCESS_KEY_ID
aws_secret_access_key = $AWS_SECRET_ACCESS_KEY
EOF
    fi
}

function boto_init_activate {
    echo "Activating boto env"

    # Should be set by Jenkins
    export PYNEST=${PYNEST:-/tmp}

    # Start ssh-agent
    eval $(ssh-agent)

    # Set temp dir
    boto_tmp_dir=`mktemp -d $PWD/boto_env_XXX`

    # Set exit trap to cleanup on success or failure
    trap boto_init_deactivate EXIT

    create_boto_config "$boto_tmp_dir"

    create_boto3_config "$boto_tmp_dir"

    # virtual env setup
    export PATH=/opt/wgen-3p/python27/bin:$PATH
    virtualenv $boto_tmp_dir > /dev/null
    source $boto_tmp_dir/bin/activate > /dev/null
    pip install pip==20.3 > /dev/null # limit upgrade to version that supports python 2.7
    pip install --upgrade wheel > /dev/null # upgrades wheel so the install succeeds
    pip install ${SELF_DIR}/.. > /dev/null  # installs asiaq
}

function boto_init_deactivate {
    set +x  # with -x, this function prints one screen worth of paths being unset; not useful
    echo "Deactivating boto env"
    ssh-agent -k

    deactivate > /dev/null # deactivate virtual env
    rm -rf $boto_tmp_dir
}

# only activate virtualenv if we're not already in one
if [[ "$VIRTUAL_ENV" == "" ]]; then
    boto_init_activate
fi

#Make sure we don't leave this source file with error-exit code.
true
