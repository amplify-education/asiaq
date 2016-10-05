Asiaq
=====

Asiaq is a collection of tools useful for running production-grade
infrastructure in AWS.

Table of Contents
-----------------

  * [History](#history)
  * [Overview](#overview)
  * [Setting up AWS accounts](#setting-up-aws-accounts)
  * [Installation](#installation)
  * [Baking Host Images](#baking-host-images)
  * [Environments](#environments)
  * [Provisioning a pipeline](#provisioning-a-pipeline)
  * [Autoscaling](#autoscaling)
  * [Defining a new hostclass](#defining-a-new-hostclass)
  * [Image management](#image-management)
  * [Logging](#logging)
  * [Network Configuration](#network-configuration)
  * [EBS Snapshots](#ebs-snapshots)
  * [Identity and Access Management](#identity-and-access-management)
  * [Monitoring and alerting](#monitoring-and-alerting)
  * [Working with DynamoDB](#working-with-dynamodb)
  * [Elastic Load Balancing](#elastic-load-balancing)
  * [Route53](#route53)
  * [Chaos](#chaos)
  * [ElastiCache](#elasticache)
  * [Elasticsearch](#elasticsearch)
  * [RDS](#rds)
  * [SSM](#ssm)
  * [Testing a hostclass](#testing-hostclasses)


History
-------

Why Asiaq?
In Inuit mythology, [Asiaq](https://en.wikipedia.org/wiki/Asiaq)
is a weather goddess. If she had a collection of python tools to tame
the clouds, this would have been it!

Why Disco?
This repository evolved from an engineering team called Disco (short for
*discover*). As such, you will find plenty of references to "disco" throughout
the code and documentation. You will probably get the right picture
if you mentally translate "disco" to "asiaq" whenever you encounter it. :)


Overview
--------

Don't Panic. Starting up infrastructure in AWS is easy. Start off by
[installing the Asiaq tools](#installation). If you are
starting in a brand new environment a few extra steps will be required
to build or promote the machines images before they can be started up to
create your cloud infrastructure.

The process of creating machine images is called baking. When baking a
pie, it is often required to pre-bake the crust before adding in the
filling. Similarly with these tools a machine image needs to be baked
two times before it can be put to use.

During the [first baking](#baking-a-base-image) we convert a vanilla
third party AMI into generic, fully updated (e.g., yum update), machine image.
This image can then be converted into a host-specific image by a
[second baking](#baking-hostclass-images). You might be wondering how
does Asiaq know [what my host ought to look like](#adding-a-new-hostclass)?
There is no magic, there is a configuration process for that.

Now that we have images ready to use we need to [create
environment(s)](#creating-environments) so we have a place to start them
in. Creating environment sets up all required networking and gateways
(as per disco_aws.ini configuration).

Finally. All that's left is to start up all the images. You could do
this by drafting out the start sequence on a piece of paper and
provisioning each instance as you check it off the list. But why would
you? A much better method is to write a small config and [spin up the
whole pipeline](#provisioning-a-pipeline) with one command.

Oh, and at some point you probably want to [stop your
pipeline](#tearing-down-a-pipeline). Hopefully sometime before you
notice a huge bill from Amazon.

Setting up AWS accounts
-----------------------

If you are a developer joining an existing team using this tool you do
not need to set up any AWS accounts and you can skip this section for
now.

Asiaq is designed to be used with three AWS accounts: production, audit and
development.

Once set up, the audit account is administered by another team (likely a
Core Ops team). The CloudTrail logs for the production and development
accounts are forwarded to that account and are read-only from the
production and development accounts respectively.

The development account houses the build and ci VPCs as well as all
sandbox VPCs.

The production account houses the deploy and prod VPCs.

### Setting up an audit account

Create a ~/.aws/config using base_boto.cfg as your starting point and add
the [audit] section to your credentials file and the [profile audit] section
to your config file (see Working with multiple accounts below if this is
unfamiliar).

1.  Set these as your default credentials with the following command

<!-- -->

    export AWS_PROFILE=audit

Ensure IAM roles are correct

    disco_iam.py update --environment audit

Create buckets with proper permissions

1.  Edit disco_aws.ini ensuring dev_canonical_id and
    prod_canonical_id initialized with the AWS Canonical account IDs
    of the development and production accounts.
2.  Create the audit account buckets

<!-- -->

Make sure you set the project variable in disco_aws.ini with a unique
project name. This will be used to create credentials buckets and those S3
buckets need to be globally unique across all AWS accounts.

    ./jenkins/disco_bucket_update.sh us-west-2 audit

### Setting up a development account

NOTE: Before setting up a development account, make sure you have setup
the audit account first.

The commands below are intended to be run from the a config directory
containing your various configs (iam,app_conf,app_auth), and it is
expected that the ASIAQ environment variable is set to the path to
asiaq (which contains the jenkins directory).


If you haven't already, create a ~/.aws/config using base_boto.cfg as
your starting point. Then add your development credentials to the [default]
section of ~/.aws/credentials. Finally, make sure you are using the
default profile by running this command:

    unset AWS_PROFILE

Ensure EC2 limits are sufficient

Limits are per-account. This means that if you have multiple VPCs in the
same account, you have to ensure enough capacity for the union of resource
requirements across all VPCs.

For example, if 'X' resources are needed for a deploy environment (1 VPC),
and 'Y' for a dev environment, then you'd likely need to:

1. Submit instance increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
2. Submit autoscaling group increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
3. Submit launch configuration increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
4. Submit elastic ip increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard

Ensure disco_aws.ini and disco_vpc.ini has been updated with proper
Elastic IPs for instances that needs Elastic IPs.

Also make sure you set the project variable in disco_aws.ini with a unique
project name. This will be used to create credentials buckets and those S3
buckets need to be globally unique across all AWS accounts.

Ensure CloudTrail is enabled for the each VPC under the account.

    $ASIAQ/jenkins/disco_cloudtrail_init.sh us-west-2 dev

Ensure IAM roles are correct

    disco_iam.py update --environment dev

Ensure needed buckets exist

    $ASIAQ/jenkins/disco_bucket_update.sh us-west-2 dev

Ensure build and ci VPCs get created

    disco_vpc_ui.py create --type build --name build
    disco_vpc_ui.py create --type openvpn --name ci

Ensure that Application Configuration is up to date for each VPC

1.  Edit and commit the configuration in
    disco_aws_automation/app_conf to match your VPN configuration and
    then apply the configuration

<!-- -->

    $ASIAQ/jenkins/disco_app_conf_update.sh build
    $ASIAQ/jenkins/disco_app_conf_update.sh ci

Ensure that Application Authorization tokens exist

    $ASIAQ/jenkins/disco_app_auth_update.sh us-west-2 ci .

Ensure the Build SSH/SSL keys exist (read the output and manually
set any necessary keys)

    $ASIAQ/jenkins/disco_keys_rotate.sh us-west-2 build

Ensure the ci SSH/SSL keys exist (read the output and manually
set any necessary keys)

    $ASIAQ/jenkins/disco_keys_rotate.sh us-west-2 ci

Ensure that your new development environment publish AMIs to the production account

    Add the account id to disco_aws.ini in the [bake] section under
    trusted_account_ids

Bake a phase 1 AMI

   disco_bake.py bake

Follow the link in the error message, then hit continue, select Manual Launch, click the Accept Terms button, wait 30 seconds and then re-run the bake command.

Apin up the build environment

    disco_aws.py --env build spinup --pipeline pipelines/build.csv

### Setting up a production account

NOTE: Before setting up a production account, make sure you have setup
the audit account first.

If you haven't already, create a ~/.aws/config using base_boto.cfg as
as your starting point. Then add a [prod] section to your ~/.aws/credentials
file with your credentials for that account. Add the matching the
[profile prod] section to your ~/.aws/config file (see Working with
multiple accounts below if this is unfamiliar). Finally, set these as
your credentials for the commands that follow in this section by setting
the AWS_PROFILE environment variable:

<!-- -->

    export AWS_PROFILE=prod

Ensure EC2 limits are sufficient

Limits are per-account. This means that if you have multiple VPCs in the
same account, you have to ensure enough capacity for the union of resource
requirements across all VPCs.

For example, if 'X' resources are needed for a deploy environment (1 VPC),
and 'Y' for a prod environment, then you'd likely need to:

1. Submit instance increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
2. Submit autoscaling group increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
3. Submit launch configuration increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard
4. Submit elastic ip increase limit to (X + Y \* number of VPCs) in us-west-2 region from EC2 dashboard

Ensure that your new production environment can see the Asiaq AMIs

1.  Add the account id to disco_aws.ini in the [bake] section under
    prod_account_ids
2.  Rebuild all hostclasses from the development environment’s jenkins

Ensure disco_aws.ini and disco_vpc.ini has been updated with proper
Elastic IPs for instances that needs Elastic IPs.

Also make sure you set the project variable in disco_aws.ini with a unique
project name. This will be used to create credentials buckets and those S3
buckets need to be globally unique across all AWS accounts.

Ensure CloudTrail is enabled for the each VPC under the account.

    $ASIAQ/jenkins/disco_cloudtrail_init.sh us-west-2 prod

Ensure IAM roles are correct

    disco_iam.py update --environment prod

Ensure needed buckets exist

    $ASIAQ/jenkins/disco_bucket_update.sh us-west-2 prod

Ensure the staging, production and deploy VPCs get created

    disco_vpc_ui.py create --type deploy --name deploy
    disco_vpc_ui.py create --type staging --name staging
    disco_vpc_ui.py create --type production --name production

Ensure that Application Configuration is up to date for each VPC

1.  Edit and commit the configuration in
    disco_aws_automation/app_conf to match your VPN configuration and
    then apply the configuration

<!-- -->

    $ASIAQ/jenkins/disco_app_conf_update.sh deploy
    $ASIAQ/jenkins/disco_app_conf_update.sh staging
    $ASIAQ/jenkins/disco_app_conf_update.sh production

Ensure that Application Authorization tokens exist

    $ASIAQ/jenkins/disco_app_auth_update.sh us-west-2 staging .
    $ASIAQ/jenkins/disco_app_auth_update.sh us-west-2 production .

Ensure the Deployment SSH/SSL keys exist (read the output and manually
set any necessary keys)

    $ASIAQ/jenkins/disco_keys_rotate.sh us-west-2 deploy

Ensure the Production SSH/SSL keys exist (read the output and manually
set any necessary keys)

    $ASIAQ/jenkins/disco_keys_rotate.sh us-west-2 staging
    $ASIAQ/jenkins/disco_keys_rotate.sh us-west-2 production

Start up deployenator (a jenkins host for deployment automation) for each production VPC

\#. For the moment, all production environments share the same produciton account.
\#. Every production VPC is managed by its own deployenator.

Installation
------------

Get the latest copy of Asiaq tools:

    git clone <repo_url>
    cd asiaq

Using virtualenvwrapper, create an environment to run python in:

    mkproject asiaq

Then install Ruby (for example, using rbenv).

And then install rake:

    gem install rake

Finally, install the egg in your virtualenv:

    rake setup:develop

Asiaq uses both boto 2 and boto 3 to communicate with the AWS APIs. Because we
now use both, you should no longer be using a .boto with your credentials in them.
This is because boto2 can find credentials specified in \~/.aws/credentials where
boto3, but boto3 will not see credentials specified in your .boto file.

Instead create an \~/.aws directory and add a credentials and config file to it.

    mkdir -p ~/.aws
    cp jenkins/base_boto.cfg ~/.aws/config
    ln -s ~/.aws/config ~/.boto
    touch ~/.aws/credentials

The credentials file should contain default credentials for your main development
account and profiles for any additional accounts you have credentials for.

    [default]
    aws_access_key_id = CafeFedF1d01DeadBeaf
    aws_secret_access_key = My/AWS/Secret/Is/Awesome/How/Is/Yours/00

    [litco-prod]
    aws_access_key_id = CafeFedF1d01DeadBeaf
    aws_secret_access_key = My/AWS/Secret/Is/Awesome/How/Is/Yours/00

Replace the aws_access_key_id and aws_secret_access_key with your
own. If you don't know your credentials, you can create new ones in the
AWS console:

1.  Log-in to the AWS Console as yourself (*not* as the root account).
2.  Go here <https://console.aws.amazon.com/iam/home>?\#users
3.  Right click on your user name and select "Manage Access Keys"
4.  Then click on the "Create Access Key" button.
5.  Then click on the "Download Credentials" button
6.  Copy the access key and secret access keys from the CSV file into
    your boto file.

If there was an old access key you had forgotten:

1.  Left click on your user name and select "Manage Access Keys" (again)
2.  Delete the old Access Key (use the date created to disambiguate)

Now you will be able to run the disco_aws.py and friends.

### Working with multiple accounts

You will need to create a [profile_name] section in your ~/.aws/credentials
file and you will need to add a [profile profile_name] section for each
profile in the ~/.aws/config with the same contents as the [default] section.

i.e. for a profile called [litco-prod] in your credentials file you will need
this section in your ~/.aws/config:

    [profile litco-prod]
    region = us-west-2

And this section in your ~/.aws/credentials

    [litco-prod]
    aws_access_key_id = CafeFedF1d01DeadBeaf
    aws_secret_access_key = My/AWS/Secret/Is/Awesome/How/Is/Yours/00

Then when wanting to run commands against the non-default account, use the
AWS_PROFILE environment variable.

For example:

    AWS_PROFILE=litco-prod disco_aws.py listhosts

### Jenkins

The best way to have Jenkins use Asiaq is to spin up jenkins in AWS and assign
proper AWS instance role that can interact with AWS. Please see
disco_aws.ini mhcdiscojenkins section for 'instance_profile_name'
attribute.

### Adding Operator AWS Account

As a new member you need someone to initialize your AWS account.

1. Create new user config by copying an existing one from
   'iam/group_membership/dev' to a new file following the same naming
   pattern as the original.
2. Run `disco_iam.py update --environment dev` to create apply the
   configuration to AWS.
3. Log in to AWS console and navigate over to [IAM Users](https://console.aws.amazon.com/iam/home#users)
   page select newly created user and create a new password with the
   `Manage Password` action.
4. Share the new password with the user. And point them to the
   [installation](#installation) and
   [Adding Operator Unix Account](#adding operator unix account) section.

### Adding Operator Unix Account

To be able to log into a provisioned machine you first need to add your
user credentials to an S3 bucket with the help of `disco_accounts.py`

To add a new operator account:

    disco_account.py adduser --name foo

This will ask you to type a new password, it will then pre-generate a
user configuration, and open it with your favorite `$EDITOR`. At this
point you can make any alterations to the account, including pasting in
you ssh key into the `ssh_key_0` (without which you will not be able to
ssh to this account). Multiple ssh_keys are supported by adding more
`ssh_key_X` fields where X is a number. When satisfied with changes save
file and close editor, configuration will now be uploaded to S3.

Should any subsequent alterations be needed to account, just run:

    disco_account.py edituser --name foo

To disable a user account, open the config with edit and change `active`
field value to no.

To change your password, you need to paste in crypt(3) compatible hash,
which you can conveniently generate with
`disco_account.py hashpassword`.

NOTE: `disco_account.py` only updates configuration in s3, these changes
do not take immediate effect on any running instances. The instance
needs to be rebooted or the `disco-add-operators` service is restarted.

### SSH agent

When working with instances inside an environment you're especially
forced to use [sshaagent](https://en.wikipedia.org/wiki/Ssh-agent). This
is because all ssh connections must go through a [jump
host](https://en.wikipedia.org/wiki/Jump_host) and password based
authentication is disabled. Obviously you don't want to store a private
key on [jump host](https://en.wikipedia.org/wiki/Jump_host) not only
because its ephemeral (in most cases anyway) but also because its
insecure.

After SSH agent is running you can daisy chain ssh connections by either
connecting to the jump host and then to target machine. Or using this
command:

    ssh -At root@$jump_host ssh root@$destination

In either case be sure to pass in -A when connecting to the jump host.

Alternatively, you can use the `disco_ssh.sh` script to avoid having to
know the jump host and destination host IPs:

    disco_ssh.sh $env_name $destination_instance_id

This script assumes you have the agent running, but will take care of
the rest of the configuration for you.

If you prefer to use the agent manually (or if the `disco_ssh.sh` script
doesn't work for you and for whatever reason you can't fix it), then
read the following section on configuring the agent.

#### Configuring SSH agent

The most simplistic (but not convenient) method of using ssh_agent:

1.  Start ssh-agent: 'eval $(ssh-agent)'

There is one rather annoying limitation when starting ssh-agent using
this method. The agent is only active for current shell session, so
sshing to VPC'd hosts will not be possible from any other terminal
windows/tabs.

The convenient method of using ssh_agent is by making use of
keychain(not to be confused with OSX keychain). After installing
keychain the setup requires addition of the following to your
\~/.bashrc:

    /usr/bin/keychain -Q -q --nogui ~/.ssh/id_rsa
    source ~/.keychain/$HOSTNAME-sh > /dev/null

With this configuration, when a shell is first started keychain will
prompt to unlock keys. After this any subsequent sessions will re-use
the unlocked key from the first session.

Baking Host Images
------------------

Before any baking can take place some infrastructure needs to be in
place. Most likely it is already running, but if you are baking at odd
times (late at night (maybe you should be sleeping instead?)) or
re-building pipeline from scratch, then it needs to be started up.

At a minimum the build VPC needs to be up. To confirm whether this is
indeed the case:

    disco_vpc_ui.py list | grep build

With just an empty VPC, only base images and a few hostclasses (templates
for groups of identical hosts) can be built, the ones that don't require
any of our custom RPMs or eggs. List of these special hostclasses can be
found under no_repo_hostclasses option of disco_aws.ini. If you are
planning on baking these no additional infrastructure needs to be up.

For all other hostclasses the rpm server needs to be up, it lives on
mhcdiscojenkins. Confirm that it's up:

    disco_aws.py --env build listhosts --hostclass mhcdiscojenkins

If it's not, start up the build pipeline:

    disco_aws.py --env build spinup --pipeline pipelines/build/build.csv

### Baking a base image

To prepare the base image we run this:

    disco_bake.py bake

This takes a CentOS provided image and runs the init/phase1.sh script to
perform a yum update and to install some packages we will need on all
hosts, such as NTP and gmond. Because of the yum update this step takes
almost 10 minutes.

### Baking hostclass images

To create an image with hostclass specific configuration / software:

    disco_bake.py bake --hostclass mhcNameOfHostclass

This will take the latest base image and install hostclass specific
software and configuration making it ready for use via provisioning
process.

### Baking within aws

Much of baking involves issuing remote commands to a temporarily
provisioned instance. These commands are performed over ssh, and there
are two different ways we can connect to the instance over ssh. Using
either its private or, its public address:

-   Private address can only be used when the bake is invoked from the
    same metanetwork as where the instance being baked.
-   Public address can be used whenever the default baking security
    group allows communication from the source address to the public on
    port 22.

Baking defaults into the mode where public address is used for ssh. But
when baking from within same metanetwork as the default bake security
group `--use-local-ip` flag should be supplied to switch to private
address mode:

    disco_bake.py bake --use-local-ip

Most generally, use `--use-local-ip` when baking from
jenkins-in-the-cloud and don't use it when baking from your desktop.

### Credentials injection

Some information is too sensitive to be stored in git (e.g., passwords,
SSL certificates, LADP password). We keep such "credentials" in S3
buckets whose permissions are more restrictive than git's.

To know what credentials need to be injected. Run the
disco_keys_rotate.sh script from console or run
disco-aws-key-rotate-prod. See the output for a list of keys that needs
to be uploaded by disco_operator.

Here is how to use credentials injection.

Store the sensitive information in S3:

    disco_creds.py set --key app_auth/my_cool_new_service/username --value "secret password"

Create the file you want to inject the information into:

    vim discoroot/opt/wgen/etc/my_cool_new_service/config.ini

Include the S3 substitution string in the file:

    # inside discoroot/opt/wgen/etc/my_cool_new_service/config.ini
    user = username
    password = {s3cred://app_auth/my_cool_new_service/username}

Note that the S3 substitution string will be baked into the AMI, and
replaced by the credentials stored in S3 at provision time. This allows
the same AMI to be deployed to multiple environments using different
credentials. The bucket from which credentials are fetched will be
chosen based on the environment in which the host is provisioned.

Environments
------------

Environments created by Asiaq are separated out by VPCs.
Each environment resides in its own VPC and has its own metanetworks,
gateways, instances, and so on. All environment management is done with
the disco_vpc_ui.py tool.

### Listing Active Environments

List all the running VPC style environments:

    disco_vpc_ui.py list

### Creating Environment(s)

Creating an environment (a VPC) requires two pieces of information. The name of
the environment, and the environment type.

The environment names must be unique, check that the selected name will
not collide with any currently running environment by [listing active
environments](#listing-active-environments).

An environment type defines network infrastructure including:

-   what IPs that are used
-   Whether it is unique
-   Network topology
-   EIP allocations

These are defined in disco_vpc.ini. Open this file to find one that
fits your purpose or define a new one.

Creating environment:

    disco_vpc_ui.py create --type sandbox --name test

### Updating an Active Environment

Updating an active environment involves making changes to the corresponding environment type
in the `disco_vpc.ini` file, and running the `disco_vpc_ui.py` tool. The `disco_vpc_ui.py`
tool currently supports updating the following resources of an active environment:

-   Security Group Rules
-   NAT gateways creation and deletion (note: update to NAT gateway EIPs is not supported yet)  
-   Routes to Internet, VPN, and NAT gateways
-   VPC Connection Peering and route creations (note: update and deletion are not supported yet)
-   Alarm notifications

Updating an environment:

    disco_vpc_ui.py update --name test

The `update` command also supports dry-run mode, in which resulting updates are printed out in
the console but not actually made to the environment. The dry-run mode is activated using the
`--dry-run` flag:

    disco_vpc_ui.py update --name test --dry-run

### Destroying an Active Environment

Find the vpc id string by [listing active
environments](#listing-active-environments).

Destroy the environment:

    disco_vpc_ui.py destroy --vpc-id vpc-dc84d8b7

### Working with other environments

By default disco_aws.py selects the environment defined in
disco_aws.ini. The --env parameter allows you to work in an environment
other than the default one without having to alter the config.

If you will be doing a lot of work in a particular environment you can
define the DEFAULT_ENV environment variable and disco_aws.py will
default to working in that environment.

Provisioning a pipeline
-----------------------

Once the images are baked you can spin up the pipeline using this
command:

    disco_aws.py spinup --pipeline \
       pipelines/dev/disco_profiling_pipeline.csv

The format of the CSV file is pretty simple. Here is a short sample:

    sequence,hostclass,min_size,desired_size,max_size,instance_type,extra_disk,iops,smoke_test,ami,deployable,integration_test
    1,mhcdiscologger,,1,,m3.large,200,,no,ami-12345678,no,
    1,mhcdiscoes,,2,,m3.large,200,,no,no,
    2,mhcdiscotaskstatus,,1,,m3.large,,,no,yes,disco_profiling_task_status_service
    2,mhcdiscoinferenceworer,1,1@45 19 * * *:3@33 19 * * *,,5,m3.large,,,no,yes,disco_inference_workflow

Field descriptions:

1.  Instance boot sequence number. The smaller the number the earlier
    the machines are started, relative to others. This field need not to
    be unique, in the example above the first three instance are started
    in parallel.
2.  Hostclass name of instance, this determines what AMI to use as well
    as applies host class specific configurations from disco_aws.ini
    (such as LB settings).
3.  Minimum Number of instances to use
4.  Desired Number instances to use.
5.  Maximum Number of instances to use
6.  [Instance
    type](http://aws.amazon.com/ec2/instance-types/#selecting-instance-types)
7.  [1-1000] Gigabytes of extra EBS storage to attach to the instances.
    This will need to be mounted to be of use.
8.  [Provisioned
    IOPS](http://aws.amazon.com/about-aws/whats-new/2012/07/31/announcing-provisioned-iops-for-amazon-ebs/)
    for EBS volume
9.  If yes ensure instance passes smoke test before continuing on
    starting next sequence. By default smoke test just tries to ssh to
    the instance and only passes after ssh connection has bee
    established and authenticated.
10. AMI specific AMI to use instead of latest tested AMI for hostclass
11. deployable If true we can replace an instance with a newer one
12. integration_test Name of the integration test to run to verify
    instances are in a good state

The desired_size can be either an integer or a colon (:) separated list
of integers with cron formatted times at which to apply each size. Using
the at symbol (@) to separate the desired size and the cron
specification. For example, `"1@30 10 * * *:5@45 1 * * *"` says to scale
to one host at 10:30 AM UTC and scale to 5 hosts at 1:45 AM UTC.

But you can also provision machines one at a time using the provision
command, for example:

    disco_aws.py provision --hostclass mhcntp --min-size 1

There are a number of optional parameters as well, here is a selection:

    --no-smoke-test This will skip the smoke test of newly provisioned hostclass.
    --no-destroy    This will forgo automatic destruction of a host that failed to provision
    --ami ami-XXXX  This will use a specific AMI rather than the latest for the hostclass
    --extra-space   This will resize the root partition on boot with the specified number of extra gigabytes of disk
    --extra-disk    This will attach an extra EBS volume with the specified number of gigabytes

Note: "Extra space" will automatically be added to the root partition,
but this slows down provisioning. An "extra disk" has to be formatted
and mounted before use and that is a much faster process. For these
reasons extra disk should generally be preferred. However if you need to
add storage capacity immediately to a host not already using "extra
disk" then "extra space" allows that without any config or code changes,
while adding "extra disk" support to an image requires that you figure
out which paths need to be mounted, create a start-up script to prepare
the volume, make sure it is run at the right time, and then bake and
test the new image.

### Tearing down a pipeline

Similar to spin up a running pipeline can be torn down:

    disco_aws.py spindown --pipeline \
       pipelines/dev/disco_profiling_pipeline.csv

If you are planning to also discard the environment you can skip this
step and proceed to Destroying an Active Environment_.

Autoscaling
-----------

When disco_aws.py is used to provision a hostclass it creates an
autoscaling group. You don't generally need to deal with these directly,
but if you would like to see the state of autoscaling groups you can use
disco_autoscale.py.

You can list autoscaling configurations:

   disco_autoscale.py listconfigs

You can list autoscaling groups:

   disco_autoscale.py listgroups

And you can list autoscaling policies:

   disco_autoscale.py listpolicies

There are also commands to delete each of these. Generally you can
simply use disco_aws.py and disco_vpc.py and they will handle the
details for you.

Defining a new hostclass
------------------------

A *hostclass* is our functional unit for deployment. Multiple machines
may be instantiated from a single hostclass, but every machine in the
hostclass has the same specific function, configuration and set of
packages installed.

When defining a new hostclass use the create command to start:

    disco_bake.py create --hostclass mhcbanana

Generally you will install some software in the init/mhcbanana.sh
script:

    yum install -y httpd
    chkconfig httpd on

As you define the hostclass you will want to test your changes by
baking:

    disco_bake.py --debug bake --hostclass mhcbanana --no-destroy

If the bake succeeds this will print out the AMI id of the newly baked
hostclass which you can provision normally. If it fails the --debug
option ensures that everything is logged and the --no-destroy option
ensures that you can ssh into the machine to investigate the problem.

You can find the IP of the machine by listing it:

    disco_aws.py --env build listhosts --most | grep mhcbanana

Then you can log in as root:

    ssh root@IP_OF_MACHINE

In addition to running commands on bake via the init/HOSTCLASS.sh script
you can also add files and set their permissions via the acfg mechanism.

For a file or directory to appear on the new machine simply add it under
discoroot and it will be copied over to the machine with root:root
ownership. If you need special ownership or permissions add this to
acfg.metadata in the form:

    path/to/file 0755 user group

acfg is run before the host specific install script, so any users that
might normally be created by package install scripts may need to be
created in init/phase1.sh instead; the base image will also need to be
regenerated after changes to phase1.sh

By default any file in discoroot will be installed on every Asiaq
machine. If you would like to limit a configuration file to a specific
machine append the tilde \~ and the name of the hostclass to the file.
Here is an example with two different database hosts:

    discoroot/etc/mongodb.conf~mhcobservationstore
    discoroot/etc/mongodb.conf~mhcprofilestore

We do not run any post boot configuration such as Chef, Puppet or
Ansible scripts. This means machines need to seek passwords from S3 and
otherwise do any post-provision configuration via init.d scripts. You
may want to look at other similar host's init.d scripts for
self-discovery templates when adding a new hostclass.

Finally if this new hostclass should become part of the standard Asiaq
pipeline add it to the appropriate pipeline definition CSV file. See
Provisioning a pipeline_ for more details.

#### Configuration

##### Enhanced Networking
By default, Asiaq does not set the enhanced networking attribute on AMIs that it builds. If you install enhanced networking compatible drivers (or your phase 1 AMI comes with them) and want to ensure that your hostclass is started with enhanced networking, you must configure this behavior in ```disco_aws.ini```. Below is an example of this:

```ini
[mhcfoo]
enhanced_networking=true
```

Image Management
----------------

### Promoting images

Images can go through various stages and vpc in the development
environment can be configured to run only images in a particular stage
unless the AMI is specified on provision.

The number and names of the stages are specified by the ami_stages
variable in the disco_aws.ini's bake section. By default the stages are
untested and tested.

Once an AMI reaches the last stage it will be shared with the accounts
specified in the prod_account_ids variable in the same section of
disco_aws.ini if it was baked by the prod_baker (as defined in the
[bake] section of disco_aws.ini). This prevents developer created
instances from accidentally being promoted to production use. We
currently set this to jenkins since the jenkins user on our Jenkins
machine builds all our production AMIs.

To test, promote and deploy (if deployable) the latest instance of a
hostclass, run this command:

    disco_deploy.py test --pipeline pipelines/ci/disco_profiling_pipeline.csv --hostclass HOSTCLASSNAME

If you leave out the --hostclass parameter this command will pick a
hostclass at random for testing.

To explicitly promote an AMI without testing to the tested stage you can
run this command:

    disco_bake.py promote --ami ami-XXXXXXXX --stage tested

### Cleanup and delete

Although Amazon charges only a nominal fee of a cent per month per
image, keeping many old images can lead to confusion.

An AMI can be deleted by simply running:

    disco_bake.py deleteami --ami ami-XXXXXXXX

Deleting many AMIs one by one can be cumbersome. A special command
exists in disco_aws called cleanupamis. It can be used to delete AMIs
by age or by keep count (per hostclass). To delete all but last three
tested images of each image type run:

    disco_bake.py cleanupamis --stage tested --keep 3 --age 0

To delete all untested AMIs older than one month:

    disco_bake.py cleanupamis --stage untested --keep 0 --age 31

The default age is 14 days and the default keep count is 3 and 14. This
will delete the images older than 14 days, but keep the last three even
if they are older than 14 days:

    disco_bake.py cleanupamis --stage untested
    disco_bake.py cleanupamis --stage tested

This is equivalent to running:

    disco_bake.py cleanupamis --stage untested --keep 3 --age 14
    disco_bake.py cleanupamis --stage tested --keep 3 --age 14

You can also restrict the cleanup to a particular hostclass using
the --hostclass parameter. You can get the list of amis that cleanupamis
would delete without actually deleting them using the --dryrun
parameter.

Logging
-------

Logging within Asiaq is all done over syslog. All hosts come
pre-configured with a local relaying rsyslog. To make use of it, send
syslog to `UDP 514`, `TCP 514`, or `/dev/log`.

All logs should have a
[syslogtag](http://tools.ietf.org/html/rfc3164#section-4.1.3)
corresponding to the application name. If its a custom Asiaq service
then syslogtag should be of the following format
`disco.$app_name.$log_type` ($log_type examples: app, errors, audit,
stat). This tag is used to organize log files on log aggregator as well
as makes it easy to search for logs through kibana. For services which
log to syslog with
[syslogtag](http://tools.ietf.org/html/rfc3164#section-4.1.3) but don't
follow the `disco.$app_name.$log_type` convention additional rule should
be added to `discoroot/etc/rsyslog.conf~mhcdiscologger`. Specifying
which file the logs should be written to. For example:

    # Handle mongo logs
    :syslogtag, startswith, "mongod" /opt/wgen/log/mongo.log
    & ~ # discard logs matching prev rule after processing

All application should write logs over syslog instead of a local file.

Network Configuration
---------------------

Options which affect network are split into disco_aws.ini and
disco_vpc.ini. Former is used for all configuration that is hostclass
specific while disco_vpc.ini is used for environment wide
configuration.

### Environment Network options

#### Metanetworks

An Asiaq environment network is composed of 4 metanetworks: intranet,
tunnel, dmz, and maintenance. Each has its own Security Group (SG),
Route Table, and one subnet per availability zone. This dictates the
granularity of network configuration. That is, all instances in the same
metanetwork will have the same SG Rules and routes. Even if those
instances are of different hostclasses.

These metanetworks have their own specific purposes:

-   **intranet:** All internal Asiaq services live here; web services,
    worker, rabbits & etc.
-   **tunnel:** Used by instances that need to have full direct internet
    access, in any production-like environments only internet filtering
    proxies should be here. Such as the s3 http proxy (mhcs3proxy).
-   **dmz:** In this metanetwork we terminate customers traffic and
    route it back to the services which live in the intranet. Generally
    there are only 2 types of hosts in this metanetworks. VPN terminator
    and a load balancer. This metanetwork should only have customer
    specific routes out to the internet.
-   **maintenance:** Serves as an entry point, a vestibule if you will,
    for all administration tasks. No hosts which are necessary for the
    continuous running of the production pipeline should be here. In
    sandbox environments this metanetwork usually routes one IP back to
    the internet this way all the ssh https packets get back to the
    developer. In production no outbound routes exist in this
    metanetwork.

Currently these 4 metanetworks are hardcoded. Adding new ones requires
changes to disco_vpc.py. However many tunables for these metanetworks
are exposed through disco_vpc.ini.

What metanetwork an instance gets placed into is controlled by the
hostclass specific `meta_network` option in disco_aws.ini:

    [mhcmyhostclass]
    meta_network=dmz

Different environments can share a large set of configuration options.
To reduce duplication of configuration, all environments have their
configuration split into two sections.

1.  environment type section, which comprises all the base options which
    are shared for a set of environments
2.  specific environment instance section, which only applies to only
    one environment.

These different ini-config section types are differentiated by the
`envtype:` and `env:` prefixes.

With this configuration you can, for example, define a set of security
group rules in the `envtype` section which are shared for production and
staging but vary IP ranges that are used by each by defining VPC
IP-range in the `env` section.

Both sections have the same options and any option defined in `env`
takes precedence over same option defined in `envtype`.

#### VPC and Subnet IP ranges

The IP range for the full VPC is specified with `vpc_cidr` option using
the standard CIDR format:

    vpc_cidr=10.0.0.0/20

In the same way as the VPC address range, the individual metanetwork
ranges can also be defined with `intranet_cidr`, `tunnel_cidr`,
`dmz_cidr`, and so on. There are some important facts to consider:

1.  The metanetwork cidr must be a subset of the VPC range and not
    overlap with other metanetworks
2.  The metanetworks will be automatically divided into multiple
    subnets, one per availability zone. Largely this is transparent to
    the user. With one exception: IP availability. CIDR range can only
    be split into 2\^n fragments. And so, when disco_vpc has to divide
    unevenly it will discard possible IP space.

    For example if there are 9 Availability zones and /24 CIDR. We need
    to over-divide the range into 16 ( Ceil(Log(9,2)) ) ranges but only
    use the first 9. So we lose almost 1/2 the original IP space.
    Usually the problem is not as severe as there are only 2-4
    Availability zones per region.

##### Dynamic IP ranges

The IP range for a VPC can also be dynamically alocated with the `ip_space` and `vpc_cidr_size` options. For example:

    [envtype:sandbox]
    ip_space=10.0.0.0/16
    vpc_cidr_size=20
    
A random IP range of size `vpc_cidr_size` inside of `ip_space` will be allocated for the VPC.
The IP range chosen will not overlap with any existing VPCs or, if its not possible, an error will be thrown.

In the same way, the IP range of the metaworks can be dynamically allocated
by specifying `auto` for the metanetwork cidr options. For example:

    [envtype:sandbox]
    intranet_cidr=auto
    tunnel_cidr=auto
    maintenance_cidr=auto
    dmz_cidr=10.0.1.0/24

The IP range of the VPC will be automatically divided to allocate the metanetworks. 
The `auto` option can be used together with statically defined IP ranges for different metanetworks. 

#### Security Group (firewall) Settings

Each metanetwork have many Security Group rules associated with it. Each
rule is a space separated triplet consisting of:

1.  Protocol type (TCP/UDP/ICMP...)
2.  Traffic source. This can be either a CIDR, name of metanetwork, or
    all (for all metanetworks)
3.  Ports. Space separated list of ports to which traffic will be
    allowed. The ports can also be specified in a range with `:` as a
    separator (Eg. 0:1024). IP protocols which do not have a notion of
    ports (Ie. ICMP), specify -1.

Each metanetwork have multiple rules associated with it, each one
separated from the other with a `,`.

Examples:

Allow port 0-1024, 443 and 8080 to intranet metanetwork from dmz and
ICMP traffic from all of 10.0.0.0/8:

    intranet_sg_rules=tcp dmz 0:1024 443 8080, icmp 10.0.0.0/8 -1

Just like with metanetwork IP range notation, each metanetwork has its
own `sg_rule` option.

Opening ports for customers using `{$my_metanetwork}_sg_rules` is somewhat
cumbersome. As customers can only talk to DMZ metanetwork, ports need to be
openeded on both intranet and DMZ. A shortcut is provided to make this easier:

    customer_ports=443,80
    customer_cidr=0.0.0.0/0

This will open port 443 and 80 to DMZ from internet, and also open the same
ports from DMZ to intranet. This way DMZ load balancer can both serve customer
and talk to services.

#### Internet Gateway Routing

What traffic can be routed out of the metanetwork to the Internet
gateway (IGW) can be specified `{$my_metanetwork}_igw_routes` option.
Any production like environments should use this option in Tunnel and
DMZ metanetworks. On Tunnel metanetwork we route all non-internal
traffic to internet:

    tunnel_igw_routes=0.0.0.0/0

And for DMZ route the VPN traffic back to customer:

    dmz_igw_routes=1.2.3.4/32

Multiple IGW routes can also be specified. So for example if the
customer has two VPNs:

    dmz_igw_routes=1.2.3.4/32 1.2.3.5/32

#### NAT

NAT is used to allow instances to talk to internet without having public IP's.
The NAT must be placed into metanetwork with IGW so it can connect to internet
itself. Then routes can be added to other metanetworks to allow them to communicate
to internet through NAT. AWS requires assignment of EIPs to a NAT, asiaq supports
two modes of EIP assignment: Long-lived static EIPs which are reserved using
disco_eip.py, or temporary EIPs can be used which get released with deletion of the
VPC. Static ips are preferred if you want to whitelist your outgoing IPs in other
systems.

To set up dynamic outbound IPs and allow all subnets to make use of NAT configure
network as follows:

    tunnel_igw_routes=0.0.0.0/0
    tunnel_nat_gateways=auto
    nat_gateway_routes=intranet/tunnel dmz/tunnel maintenance/tunnel

To set up static outbound IPs you must be sure to specify as many IPs as there are
Availability Zones (at time of writing there are three). Configuration below only
allows one metanetwork (intranet) to make use of the NAT:

    tunnel_igw_routes=0.0.0.0/0
    tunnel_nat_gateways=52.0.0.1 52.0.0.2 52.0.0.3
    nat_gateway_routes=intranet/tunnel


#### DHCP Settings

AWS allows to pass in some custom values to be subsequently handed out
to instances on boot with DHCP. We expose a smaller subset of these:

-   `internal_dns` Primary DNS server
-   `external_dns` Secondary DNS server
-   `domain_name` The network domain
-   `ntp_server` Address of NTP server.

We use AWS resolver by specifying AmazonProvidedDNS for internal_dns
and external_dns, alternatively the APIPA address 169.254.169.253 can
be used.

##### NTP server by offset.

NTP server can also be [assigned by metanetwork offset](#assigning-private-ips):

    ntp_server_metanetwork=tunnel
    ntp_server_offset=+5

For this to work the ntp_sever option must not be specified, as it takes
presidence.

#### DirectConnect / VPN Gateway

The important bits of DirectConnect / VPN Gateway are configured by
3rd parties. You file a request with your ISP or NOC, which
establishes a connection between your data center and AWS on your
behalf. From this point all we need to do is confirm the connection.
There might be some other things that happen behind the scenes, but
the outcome of this is a Virtual Gateway. This virtual gateway must
be tagged with Name tag, with a value of the environment it belongs
to. Unlike most other of our AWS, resources, this VGW is persistent.
It is not deleted when the VPC is deleted but is merely detached, this
way it can be re-attached when VPC is re-created without having to
involve a 3rd party.

To establish a Direct Connection / VPN Gateway to a VPC, it needs to be
attached and route table entry must be created. This can be done by
enabling route propigation or for finer control, manually specified as
options in `disco_vpc.ini`. The format is similar to Internet Gateway
routing.

What traffic can be routed out of the metanetwork to the Virtual
Private Gateway (VGW) can be specified `{$my_metanetwork}_vgw_routes`
option.
On Tunnel metanetwork we can route all 10.123.0.0/16 traffic to the
VGW:

    tunnel_vgw_routes=10.123.0.0/16

Multiple VGW routes can also be specified. So for example if the
customer has two networks to route via IGW:

    dmz_vgw_routes=10.123.0.0/16 1.124.0.0/16

#### VPC Peering

Peering can be configured between VPCs to enable communication between them. 
The syntax for the peering config is `vpc_name[:vpc_type]/metanetwork vpc_name[:vpc_type]/metanetwork`.
`vpc_name` can either be the name of an existing VPC or `*` to match any VPC of a certain type. 

For example:

    [peerings]
    connection_1=example-vpc:sandbox/tunnel ci/intranet
    connection_2=*:sandbox/tunnel ci/intranet
    
The keys for the peering config don't matter except that they must be unique and start with `connection_`
    
The `connection_1` configuration will create a peering connection from the `example-vpc` to a vpc named `ci`. 
Security groups will be updated to allow traffic from `example-vpc`'s tunnel metanetwork to `ci`'s intranet metanetwork.
    
The `connection_2` configuration will create peering connections to any VPC of type `sandbox` to `ci`

### Instance Network Options

#### Instance IP Addresses

We make use of several types of addresses for our instances: \* Public
IP (dynamically assigned) \* Elastic IP (EIP) \* Dynamic / Allocated
Private IP

##### Managing EIPs

Elastic IPs need to be pre-allocated before they can be used in any
configuration file.

Allocating EIP:

    disco_eip.py allocate

After an EIP is no longer needed it can be released. Before releasing
EIP one should grep through all of Asiaq (at minimum
the .ini) files to ensure the EIP is not being relied upon:

    disco_eip.py release --eip 54.186.17.80

Listing all EIPs:

    disco_eip.py list

##### Assigning Public IPs

To assign an EIP to a instance, add an eip option with the value of the
desired EIP address. The option is specified for the instance hostclass
in disco_aws.ini, as a result only the first started instance of the
hostclass will get the EIP. Consequently, its often desirable to also
specify public_ip=True so that any subsequent instances get a generic
public IP when EIP is already assigned. EIPs can be assigned differently
depending on environment, simply use the @ option convention. For
example:

    [myhostclass]
    ...
    eip=5.5.5.5
    eip@staging=1.3.3.7
    eip@production=31.3.3.7
    public_ip=True
    ...

with this configuration staging and production instances of this
hostclass will be assigned EIPs 1.3.3.7 and 31.3.3.7 respectively. The
first instance of any different environment will get EIP 5.5.5.5 and the
rest will get a random public IP.

##### Assigning Private IPs

For the most part private IPs are assigned dynamically by DHCP: a
arbitrary available IP in the metanetwork is allocated and assigned to
the instance. This is the default behavior for private IPs and used in
the vast majority of cases. For certain instances, such as DNS servers,
static private IPs are required. To assign such an IP, the ip_address
hostclass option can be used. Some care needs to be taken when choosing
a private IP address; AWS reserves the first 5 IP addresses of the VPC
range for its own use, the address must be within the IP range of the
instance subnet, and the address must not be re-used. Also, due to the
nature the way amazon maps subnets to specific availability zones any
instance with a static IP gets locked into one arbitrary availability
zone.

There are two ways to assign static private IP. By metanetwork (subnet)
offset and absolute IP address. The latter is straight forward:

    [myhostclass]
    ...
    ip_address=10.0.0.5
    ...

But setting absolute IPs can very quickly become bothersome. If you have
5 different environments with different ip spaces you'd have to explicitly
set eip for each environment using the `@` notation (same as in EIP example
above). So instead you can use the metanetwork offset, that is, specify
that a host should take the 5th ip from the beginning of the metanetwork range:

    [myhostclass]
    ...
    ip_address=+5
    ...

With this configuration host will be assigned private ip 10.0.0.5 in a
10.0.0.0/16 network and 192.168.0.5 in a 192.168.0.0/16.

WARNING! Setting a private IP on an instance will lock it a single
Availability Zone. This is a limitation of AWS' subnets, they cannot
span multiple Availability Zones.

EBS Snapshots
-------------

In AWS you almost always want to offload state to an AWS managed service
there are times when you will want to store state on an EBS volume that
is preserved across reboots. This can be done with EBS snapshots. Asiaq
will automatically link the latest snapshot tagged with a hostclass name
to the autoscaling group for that hostclass upon autoscaling group
creation (i.e. on provision or spinup).

If you wish to update an existing autoscaling group with the latest snapshot
you can run this command:

    disco_snapshot.py --env build update --hostclass mhcverystateful

There are also commands to list, cleanup, delete and capture snapshots
which work how you would expect them to work.

There is also a create command that allows you to create the initial
EBS volume snapshot for a hostclass. This initial volume will not be
formatted.


Identity and Access Management
------------------------------

We make use of IAM for access control to AWS resources quite
extensively. Despite this we treat the IAM configuration in AWS as
ephemeral, it gets periodically reloaded from configuration stored in
this repository. There are few reasons for this:

1.  Configuration can be easily copied between multiple accounts (such
    as audit, production, development).
2.  We do not accidentally leave temporary IAM changes behind when
    testing IAM policies and the like.

The IAM configuration is made up of several directories:

-   **federation**: Contains single file `AssumeRolePolicyDocument.iam`
    which specifies the policy to be used for federation. AWS
    documentation is rather scant on what the purpose of
    `AssumeRolePolicyDocument` / `trust relationship` is, but it
    certainly needs it. Its unlikely this will need modification.
-   **group_membership**: Contains set of directories, one per
    environment. Each of the environment directory has a file per used
    (with `.grp` extension) which species which groups the IAM user is
    part of (one group per line).

    Group membership is also use to create IAM users in the account. You
    might notice that there is no facility to specify keys or passwords,
    these must be created independently and will persist in IAM.
-   **instance_policies**: Specifies Instance Roles based on the
    containing IAM policy `.iam` file. The mapping between instance role
    and hostclass is defined in disco_aws.ini with the
    `instance_profile_name` parameter.
-   **user_policies**: Specifies User Roles (Federated users) and
    Groups based (IAM Users) on policy `.iam` files.
-   **s3**: Specifies policies for buckets.

There are several policy types: - **.iam**: These are the IAM policies
themselves. They say what can be done to what resources from which
locations. - **.tr** These determine the trust relationship between
accounts. - **.acp**: These are S3 Access Control Lists. When IAM won't
do what you need. - **.lifecycle** These determine when S3 buckets are
sent to Glacier or expired. - **.logging** These determine whether and
where S3 access logs are sent. - **.versioning** These determine whether
versioning is enabled for an S3 bucket.

The syntax of the IAM policies is described in the [AWS IAM Policy
Reference](http://docs.aws.amazon.com/IAM/latest/UserGuide/policy-reference.html).

In the disco_aws.ini file there is an [iam] section. This contains up
to six variables: - **saml_provider_name** Name of SAML Identity
Assertion Provider (for SSO) - **saml_provider_url** URL of SAML
Identity Assertion Provider (for SSO) - **role_prefix** This plus "_"
is prepended to user groups and roles. - **policy_blacklist** This
prevents a subset of the defined groups and roles from being created.
This is used with ["@prod](mailto:"@prod)" to keep developer account
policies from leaking into the production account. -
**prune_empty_groups** When set to True any groups to which no user
belongs are pruned. - **naked_roles** This specifies roles to which the
role_prefix is not applied. This is used when policies need to have a
specific name for cross account access. For example: SecurityMonkey.

### Commands for managing Access Control

To sync IAM configuration from the git repository to AWS:

    disco_iam.py update --environment foo

This will sync the following:

-   Federated User Roles & Policies
-   Group (AKA Unfederated User) Roles & Policies
-   Group membership
-   Instance Roles & Policies
-   SAML providers (based on disco_aws.ini's saml_provider_name and
    saml_provider_url parameters)
-   Users (only the existence there of, no API keys / password are
    synced)

Synchronization deletes unmatched Groups / Roles / Policies /
SAML-providers and overwrites with configuration from IAM directory. For
IAM users, only existence is synchronized. They will be deleted /
created as appropriate but will not be overwritten, this ensure keys and
passwords persist.

You can list the IAM users and see the groups each is in:

    disco_iam.py listusers

You can list the groups:

    disco_iam.py listgroups

You can list the currently in force policies for a group:

    disco_iam.py listgrouppolicies --group_name disco_dev

### Commands for rotating API keys

You can list keys (you can have zero, one or two API keys):

    disco_iam.py listkeys --user_name disco_deployenator

You can create a new key (this is the only time the secret key is
printed):

    disco_iam.py createkey --user_name disco_deployenator

You can deactivate the old key:

    disco_iam.py deactivatekey --user_name disco_deployenator --access_key_id AKIAJPXB3X3RVOOUSBAQ

If you have another key in your ~/.aws/credentials, you can reactivate the old key in
case of an issue:

    disco_iam.py activatekey --user_name disco_deployenator --access_key_id AKIAJPXB3X3RVOOUSBAQ

And, you can remove the old key:

    disco_iam.py removekey --user_name disco_deployenator --access_key_id AKIAJPXB3X3RVOOUSBAQ

The work flow for rotating the Jenkins AWS API key is as follows:

1.  List the existing keys. There should be only one key there and it
    should be the same as the key in your \~/.aws/credentials file.
2.  Create a new key. Note the output, this is the only time the secret
    key is available.
3.  Rename the AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY
    environment variables and create new variables with those names and
    the new key id and secret key.
4.  Deactivate the old key.
5.  Run a Jenkins job requiring AWS API access, assuming it works delete
    the old environment variables.
6.  Remove the old key.

The work-flow for rotating your own API key is as follows:

1.  List the existing keys. There should be only one key there and it
    should be the same as the key in your \~/.aws/credentials file.
2.  Create a new key. Note the output, this is the only time the secret
    key is available.
3.  Place the output of of createkey in your \~/.aws/credentials file and comment
    out the old key there.
4.  Remove the old key (using your new key in the \~/.aws/credentials file).

### Commands for updating app auth

You can update the environment application authentication password by
environment:

    disco_app_auth.py --env prod update

Or by bucket name:

    disco_app_auth.py --bucket <bucket_name> update

Monitoring and alerting
-----------------------

We use Cloudwatch Metrics and Alarms for monitoring and alerting. The
workflow is:

-   metrics and alarms are manually configured in disco_alarms.ini
-   on VPC creation, SNS topics are created and subscribed to first
    responders
-   on hostclass provisioning, Cloudwatch metrics and alarms are created,
    and configured to push to appropriate SNS topic
-   on hostclass termination, Cloudwatch metrics and alarms are deleted,
    but SNS topics are left intact
-   on VPC destruction, SNS topics are still left intact to avoid a
    manual confirmation step associated with re-subscribing first
    responders

### Configuration

The config file `disco_alarms.py` contains one `[notifications]` section
describing the SNS topics, and multiple `[<metric_name>]` sections
describing the metrics and corresponding alarms to be created, either
globally or at a hostclass level.

#### Notifications

Notification entries in `disco_alarms.py` are of the form
`<env>_<severity>=<subscribers>`, where:

-   `env` is an environment name (i.e., a VPC name)
-   `severity` is one of: `info`, `critical`
-   `subscribers` is either a list of comma-separated email addresses,
    or a callback url (useful for PagerDuty)

A new SNS topic will be created for every line in the Notifications
section, and all subscribers will be subscribed to this topic.

Note that every new email subscription will automatically trigger an
email from AWS to the subscriber, asking for confirmation that the
subscription is indeed desired. Notifications will not be sent out to
this email address until the subscriber has confirmed.

#### Metrics and alarms

Alarms and metric fall into two categories, custom and automatic.

##### Alarm Config for Automatic Metrics

The metrics for the latter are automatically created with auto scaling
group. To set up alarms on these metrics. A corresponding alarm section
needs to be added to `disco_alarms.py`:

    [AWS/EC2.CPUUtilization]
    custom_metric=False
    period=300
    duration=5
    threshold_max=99
    threshold_min=0
    statistic=Average

This will automatically create the alarm for all hostclasses that are
spun up. By appending the section with hostclass name (eg:
`[AWS/EC2.CPUUtilization.mhcfoo]`) the alarm will be only applied to the
one specific hostclass. This can also be used to 'override' any of the
options on specific hostclass.

Section name breaks down into 3-4 period separated fields:

1.  Team
2.  Namespace
3.  MetricName
4.  Hostclass (Optional)

NOTE: For the `AWS/ES` namespace, the hostclass field is required and refers to the internal name of the Elasticsearch domain. IE: `logs`.

Options:

-   `custom_metric` specifies whether its a custom or automatic
    (autoscale) metric
-   `period` Interval in seconds between checks of metric
-   `duration` Number of time check needs to be in critical state before
    alarm is triggered
-   `threshold_max` Optional maximum threshold for alarm
-   `threshold_min` Optional minimum threshold for alarm
-   `statistic` SampleCount | Average | Sum | Minimum | Maximum
-   `log_pattern_metric` Optional, specifies whether the metric comes from the CloudWatch Logs agent

For more information on the options refer to [CloudWatch' PutMetricAlarm
API](CloudWatch'%20PutMetricAlarm%20API)

##### Supported AWS Namespaces

Currently, `disco_alarms.py` supports the following namespaces:

-   AWS/EC2
-   AWS/ELB
-   AWS/RDS
-   AWS/ES

##### Alarm Config for Custom Metrics

To be able to create alarms on custom metrics, the metrics must have
dimension with a single field `env_hostclass` assigned with the
environment and hostclass concatenated through a underscore (eg:
`ci_mhcfoo`). There are examples on how to set up alarms in
`disco_aws_automation/disco_metrics.py`.

With the exception of the custom_metic option, which needs to be set to
True, Custom Alarm configuration is identical to that of Automatic
Metrics.

### Commands for updating the configuration

If you make any changes to SNS topic subscriptions you may want to apply
them without re-creating the VPC. This will do it:

    TK

If you make any changes to metrics and alarms and want to apply them
without cycling any hostclasses:

    TK

### Log Metrics

CloudWatch Logs can create metrics from log files. For example, sending an alert when there are exceptions in log files.
It can be controlled with `disco_log_metrics.py` and is configured with `disco_log_metrics.ini`

##### Configuration for log metrics

    [mhcdummy.ErrorCount]
    log_file=/var/log/httpd/error_log
    filter_pattern=error
    metric_value=1

Section name breaks down into 2 period separated fields:

1.  Hostclass
2.  MetricName

Options:

-   `log_file` The log file to read
-   `filter_pattern` The pattern to look for [Pattern Syntax](http://docs.aws.amazon.com/AmazonCloudWatch/latest/DeveloperGuide/FilterAndPatternSyntax.html)
-   `metric_value` A value to extract from the pattern or a static number to increment the metric by

And here's a more complicated example, extracting a metric value from log lines:

    [mhcdummy.StatusCode]
    log_file=/var/log/httpd/access_log
    filter_pattern=[ip, id, user, timestamp, request, status_code, ...]
    metric_value=$status_code

##### Enabling the CloudWatch Logs forwarding agent

The agent runs on your instance and forwards logs it's configured to monitor. Installing and enabling the agent
are common steps that you may wish to have many hostclasses perform, so it's a good idea to put those steps
in a common initialization script, such as `init/common.sh`, which is then sourced by your hostclass init script
at bake time.

The agent configuration file lives at `/etc/awslogs.conf`. For the file format, refer to the [Amazon docs](http://docs.aws.amazon.com/AmazonCloudWatch/latest/DeveloperGuide/AgentReference.html).
A sample agent configuration file for Apache could look like this:

    [general]
    state_file = /var/awslogs/state/agent-state

    [/var/log/httpd/access_log]
    datetime_format = %Y-%m-%d %H:%M:%S
    file = /var/log/httpd/access_log
    buffer_duration = 5000
    log_stream_name = {hostname}
    initial_position = start_of_file
    log_group_name = {env}/{hostclass}/var/log/httpd/access_log

    [/var/log/httpd/error_log]
    datetime_format = %Y-%m-%d %H:%M:%S
    file = /var/log/httpd/error_log
    buffer_duration = 5000
    log_stream_name = {hostname}
    initial_position = start_of_file
    log_group_name = {env}/{hostclass}/var/log/httpd/error_log

### Commands for updating metric filters
List all log metrics for a hostclass.

    disco_log_metrics.py [--debug] [--env ENV] list-metrics --hostclass HOSTCLASS

List all log groups for a hostclass.

    disco_log_metrics.py [--debug] [--env ENV] list-groups --hostclass HOSTCLASS

Update log metrics for a hostclass from the config file.

    disco_log_metrics.py [--debug] [--env ENV] update --hostclass HOSTCLASS

Delete all log metrics for a hostclass.

    disco_log_metrics.py [--debug] [--env ENV] delete --hostclass HOSTCLASS

Working with DynamoDB
------------------------------

Managing DynamoDB tables is done using the `disco_dynamodb.py` script.
The currently supported commands are: list, describe, create, update,
and delete.

Because DynamoDB tables are not physically tied to any one particular VPC,
their names have to be unique across all the environments we have. As such,
`disco_dynamodb.py` transforms the input table name to the actual one
it uses by postfixing it with an "_" followed by the environment name. For
example, `Notices` would become `Notices_ci` for the ci environment and
`Notices_staging` for the staging environment.

### Commands for managing DynamoDB

To list all the DynamoDB tables:

    disco_dynamodb.py list

To create a DynamoDB table:

    disco_dynamodb.py create --config notices.json

where notices.json is a JSON file containing the definition of the DynamoDB table. For
a list of properties that need and can be included in the table definition, please go
[here](http://boto3.readthedocs.org/en/latest/reference/services/dynamodb.html#DynamoDB.Client.create_table).

After issuing a create command, the describe command can be used to check on the status:

    disco_dynamodb.py describe --table Notices

To update a DynamoDB table:

    disco_dynamodb.py update --config notices.json

To specify the environment against which the command is run, use the `--env` option:
    disco_dynamodb.py create --config notices.json --env integrationtest

where notices.json is a JSON file containing the new definition of the DynamoDB table. For
a list of properties that need/can be included in the table definition, please go
[here](http://boto3.readthedocs.org/en/latest/reference/services/dynamodb.html#DynamoDB.Client.update_table).

To delete a DynamoDB table:

    disco_dynamodb.py delete --table Notices

Elastic Load Balancing
-----------------------

Elastic Load Balancers(ELB) add load balancers to AutoScaling groups.
This is useful for creating a single service endpoint for a collection of instances.
ELBs are automatically given a Route53 domain name based on the hostclass and environment name of the ELB

### Configuration
The following configuration is available in `disco_aws.ini` to configure an ELB for a hostclass

    [mhcbanana]
    domain_name=aws.wgen.net
    elb=True
    elb_health_check_url=/banana/liveops/heartbeat/status
    elb_sticky_app_cookie=banana_session
    elb_idle_timeout=300
    elb_connection_draining=300

Options:

-   `domain_name` Top level domain name to use as a suffix for all ELB domain names
-   `elb` Create an ELB for this hostclass
-   `elb_meta_network` [Optional] Meta network to run ELB in, defaults to same meta network as instances
-   `elb_health_check_url` [Default /] The heartbeat end-point to test instance health
-   `elb_instance_port` [Default=80] The port number that your services are running on
-   `elb_instance_protocol` [Default inferred from port] HTTP | HTTPS | SSL | TCP
-   `elb_port` [Default=80] Comma separated list of port numbers to expose in the ELB.
-   `elb_protocol` [Default inferred from port] Comma separated list of protocols to expose from ELB. The protocols should be in the same order as the ELB ports. HTTP | HTTPS | SSL | TCP
-   `elb_public` [Default no] yes | no Should the ELB have a publicly routable IP
-   `elb_sticky_app_cookie` [Optional] Enable sticky sessions by setting the session cookie of your application
-   `elb_idle_timeout` [Default=300] Timeout before ELB kills idle connections
-   `elb_connection_draining` [Default=300] The timeout, in seconds, for requests to unhealthy or de-registering instances to complete before instance is removed from ELB
-   `elb_cross_zone_load_balancing` [Default=True] If true, the ELB will evenly distribute load across instances regardless of their AZs. If false, the ELB will evenly distribute load across AZs regardless of number of instances in each AZ.

### Commands for managing ELB
List all ELBs in the environment

    disco_elb.py --env ci list

Create a new ELB for a hostclass. Update the ELB if it already exists.

    disco_elb.py --env ci create --hostclass <hostclass>

Delete a ELB. No error is thrown if ELB does not exist.

    disco_elb.py --env ci delete --hostclass <hostclass>

### TLS Certificates

To terminate HTTPS an ELB needs a valid TLS certificate. You can use either the AWS Certificate
Manager service or IAM server certificates. The ELB code will look for a cert in ACS with a
DomainName of HOSTCLASS-ENV.DOMAINNAME. If it fails to find the cert in ACS to will look for
an IAM Server Certificate with a ServerCertificateName of the same name.

Example commands for a self-signed cert in IAM (for testing)

    openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout privateKey.key -out certificate.crt
    openssl rsa -in privateKey.key -check
    openssl x509 -in certificate.crt -text -noout
    openssl rsa -in privateKey.key -text > private.pem
    openssl x509 -inform PEM -in certificate.crt > public.pem
    aws iam upload-server-certificate --server-certificate-name mhcadminproxy-ci.example.com \
                                      --certificate-body file://`pwd`/public.pem \
                                      --private-key file://`pwd`/private.pem

Example commands for an ACS cert

    aws --region us-east-1 acm request-certificate \
        --domain-name astro-jenkins.aws.wgen.net \
        --domain-validation-options DomainName=mhcfoo-ci.dev.example.com,ValidationDomain=example.com \
        --idempotency-token abcd1234

    # An email will be sent to admin@example.com, click on the link in the e-mail.
    aws --region us-east-1 acm list-certificates

Note: As of 2016-02-12 ACS is only supported in the us-east-1 region and so can only be used with ELBs
in that region.

Route53
-----------------------

Route53 is used for managing our DNS domains and records.
It is automatically used by Asiaq to manage DNS for services
and can also be controlled with `disco_route53.py`

### Hosted Zones
AWS Hosted Zones basically map to a DNS domain name and contain a list of DNS records.
There are public and private Hosted Zones. Private Hosted Zones only work inside VPCs.
For most of our needs we need to be able to use the domain names from outside a VPC so have been using Public Hosted Zones.

It is possible to create a Hosted Zone as a subdomain for a domain that is outside of Route53 (e.g wgen.net)
In this case amazon gives us a set of DNS records we need to insert into our DNS when creating the Hosted Zone.
This means that creating a Hosted Zone for a domain outside of Route53 needs to be a manual process.
For the exact steps see [CreatingNewSubdomain](http://docs.aws.amazon.com/Route53/latest/DeveloperGuide/CreatingNewSubdomain.html)
We have aws.wgen.net in this way.


### Commands for managing Route53
Retrieve the list of Route53 Hosted Zones. These are our top level domain names

    disco_route53.py list-zones

Retrieve the list of Route53 DNS records for all Hosted Zones. Of interest here are
the CNAME records that we use to give DNS names to the Elastic Load Balancers

    disco_route53.py list-records [--zone <zone-name>]

Create a new DNS record with a single value. Only non-alias records are supported.

    disco_route53.py create-record <zone-name> <record-name> <type> <value>

Delete a DNS record

    disco_route53.py delete-record <zone-name> <record-name> <type>

Chaos
-----

Machines do die and network partitions do happen. The more machines you run the more
likely you are to experience this on any particular day. disco_chaos.py allows you to
greatly increase the odds that identify recovery problems during working hours.

Modelled on the Netflix Chaos Monkey, the Asiaq chaos utility randomly kills instances for
you. There is only one command and it intended to be run regularly and then followed by a run
of integration tests so any problems can be identified.

For example this would terminate 0.5% of all machines in production, but maintain at least 33%
of capacity for any particular hostclass.

    disco_chaos.py --env production --level 0.5 --retainage 33.3

There is also a --dry-run option that will simply print out the instances it would have
terminated if you hadn't used that parameter.

This Retainage number is rounded up. So if there is only one instance of a hostclass and
the value is anything greater than 0 then at least one instance will be preserved.

The level is similarly rounded up to 1 instance. So if you specify a level of 1% and there
are only 50 machines available for termination, one instance will be terminated.

You can opt-out of chaos. This might be done for some hostclass which you don't need to be
resilient because you never intend to run it in production. In this case specify you don't
wanty chaos in your disco_aws.ini, for example:

    [mhccapiloadtester]
    chaos=no

On the nature of Chaos. There are many ways this could be implemented, but currently we
construct a list of machines eligle for termination and then select a set percentage of
those machines and terminate them without any bias. A bias might make for better tests.
For example, if we kill 66% of all machines of one type that will test our ability to
cope with self-DDOS than killing 33% of one type and 33% of the machine that calls it.
Another example is losing all the machines in a single AZ, which can happen due to network
partition, natural disaster, fire, etc. We welcome improvements.

ElastiCache
-----
ElastiCache provides cache servers as a service. Using the ElastiCache service means
we don't have to create hostclasses in EC2. Spinning up a new cache cluster involves
editing the `disco_elasticache.ini` config file and running `disco_elasticache.py`

Currently only Redis is supported in Asiaq.

### Configuration
The following configuration is available in `disco_elasticache.ini` to configure an ElastiCache cluster.

    [ci:dummy-redis]
    instance_type=cache.m1.small
    engine_version=2.8.23
    port=6379
    parameter_group=default.redis2.8
    num_nodes=2
    maintenance_window=sat:5:00-sat:06:00

Options:

-   `instance_type` The instance types to use. Must use instance types that start with "cache."
-   `engine_version` The version of Redis
-   `port` Port that Redis should be available on
-   `parameter_group` The set of Redis parameters to use
-   `num_nodes` Number of nodes in cache cluster
-   `maintenance_window` specifies the weekly time range (of atleast 1 hour) in UTC during which maintenance on the cache cluster is performed. Default maintenance window is from sat:1:00-sat:2:00 EST or sat 05:00-06:00 UTC.

ElastiCache also depends on some configuration from `disco_aws.ini`

    [disco_aws]
    default_domain_name=aws.wgen.net
    default_meta_network=intranet
    default_product_line=example_team

### Commands for managing ElastiCache
List cache clusters from ElastiCache

    disco_elasticache.py [--env ENV] list

Create/update the clusters in a environment from the `disco_elasticache.ini` config

    disco_elasticache.py [--env ENV] update [--cluster CLUSTER]

Delete a cache cluster

    disco_elasticache.py [--env ENV] delete --cluster CLUSTER

Elasticsearch
-------------

### Introduction
Elasticsearch is an AWS service that is capable of indexing and analyzing large amounts of data. A typical use would be analyzing and visualizing logs from instances.

Elasticsearch domains can be created using `disco_elasticsearch.py`. Each environment (VPC) can have multiple Elasticsearch domains. Currently, the domain is managed independent of VPC creation/deletion. An Elasticsearch domain has a service endpoint where we can ship logs to, and interact with it via API calls.

Our Elasticsearch Domain Name format is:

`es-<elasticsearch_name>-<environment_name>`

Example:

`es-logger-foo`

Elasticsearch Endpoint format is:

`search-<elasticsearch_domain_name>-<cluster_id>.<region>.es.amazonaws.com`

Example:

`search-es-logger-foo-nkcqfivhtjxy7ssl4vrr3s5cq4.us-west-2.es.amazonaws.com`

After the Elasticsearch domain has been created, a CNAME record in Route 53 for the endpoint is also added.
Route 53 CNAME format:

`<elasticsearch_domain_name>.<domain_name>`

NOTE: `domain_name` refers to the `default_domain_name` configured in `disco_aws.ini`

Example:

`es-logger-foo.aws.example.com`

NOTE: Elasticsearch endpoints use an SSL certificate issued to Amazon.com for `*.us-west-2.es.amazonaws.com`. Therefore, we cannot use our CNAME as an endpoint in rsyslog configuration because the SSL certificate is invalid for our Route 53 entry.

### Configuration

ElasticSearch configuration is read from `disco_elasticsearch.ini`.

Here is an explanation of the various options.
```ini
# elasticsearch settings (sample config)
[ENVIRONMENT_NAME:ELASTICSEARCH_NAME]
instance_type=                  # Instances ending in .elasticsearch (required) (string)
instance_count=                 # Total instances number (required) (int)
dedicated_master=               # Dedicate cluster master (boolean)
dedicated_master_type=          # Instances ending in .elasticsearch (string)
dedicated_master_count=         # Number of master instances (3 recommended for Prod) (int)
zone_awareness=                 # Use multi-AZ (if enabled min 2 nodes required) (boolean)
ebs_enabled=                    # Enable EBS-base storage (boolean)
volume_type=                    # (standard | gp2 | io1)
volume_size=                    # Min: 10(G)
iops=                           # only for io1 volume type - Min:1000, Max:4000 (int)
snapshot_start_hour=            # Hour at which to take an automated snapshot Ex: '5' for 5am UTC (int)
allowed_source_ips=             # A space separated list of IPs that allowed to interact with the ElasticSearch domain. (string)
archive_threshold=              # A fraction number representing the percentage of used space in the cluster that the archival's grooming operation should allow. (float)
archive_max_shards=             # The maximum number of shards allowed by the grooming operation. (int)
archive_role=                   # Name of the assumed role used by the archival process (string)
archive_index_prefix_pattern=   # Regex pattern used to match with the Elasticsearch indices that are included in the archival process. This pattern only tries to match the naming part of the indices before the date string. (string)
archive_repository=             # Name of the repository used to store the index snapshots (string)
version=                        # Version of Elasticsearch. See http://docs.aws.amazon.com/elasticsearch-service/latest/developerguide/es-configuration-api.html
```

Additionally, access to the Elasticsearch endpoint is restricted based on IP address via Access Policy. Instances in a VPC need to ship logs to Elasticsearch via a proxy server. This proxy server's IP is read from `disco_aws.ini`. The important options are `proxy_hostclass` in the `disco_aws` section as well as the `eip` in the hostclass section referenced from the `proxy_hostclass` option.

### Kibana
Amazon Elasticsearch provides a default installation of Kibana with every Amazon Elasticsearch domain. The Kibana interface is accessed via URL with the following format:

`https://<elasticsearch_endpoint>/_plugin/kibana/`

Example:

`https://search-es-logger-foo-nkcqfivhtjxy7ssl4vrr3s5cq4.us-west-2.es.amazonaws.com_plugin/kibana/`

The CNAME provided by Route 53 can also be used:

`https://es-logger-foo.aws.example.com/_plugin/kibana/`

NOTE: When using CNAME, certificate will show as invalid because it was issued for *.us-west-2.es.amazonaws.com.

RDS
-------------------

RDS provides databases as a service. Creating a new database instances with Asiaq involves
editing the `disco_rds.ini` config file and running `disco_rds.py`. Route53 CNAMEs are automatically created for each RDS instance


### Configuration
The following configuration is available for RDS. A section is needed for each RDS instance in every VPC. The section names are formatted as `[VPC Name]-[Database Name]`

```ini
    [ci-foodb]
    allocated_storage=100
    db_instance_class=db.m4.2xlarge
    engine=oracle-se2
    engine_version=12.1.0.2.v2
    iops=1000
    master_username=masteruser
    port=1521
    storage_encrypted=True
    multi_az=True
    backup_retention_period=1
    preferred_backup_window=05:04-05:34
    preferred_maintenance_window=sat:06:00-sat:06:30
```

Options:

-   `allocated_storage` Database size in GB
-   `db_instance_class` The instance type to use for database instances
-   `engine` The type of database such as Oracle or Postgres
-   `engine_version` The database version to use
-   `iops` Provisioned IOPS
-   `master_username` Master username for connecting to the database
-   `port` [Default 5432 for Postgres, 1521 for Oracle]
-   `storage_encrypted` [Default True]
-   `multi_az` [Default True]
-   `backup_retention_period` How many days should daily backups be retained? Setting to 0 disables automatic backups. [Default 1, Range >=0, <=35]
-   `preferred_backup_window` A window for daily backups. Format is hh24:mi-hh24:mi, timezone is UTC, window must be at least 30 minutes long, and the selected window must not conflict with the maintenance window.
-   `preferred_maintenance_window` A window for weekly maintenance. Format is ddd:hh24:mi-ddd:hh24:mi, timezone is UTC, window must be at least 30 minutes long, and the selected window must not conflict with the backup window.

Default backup and maintenance windows are taken from the time wedge given in the [AWS docs](http://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.BackingUpAndRestoringAmazonRDSInstances.html)

### Commands for managing RDS
List RDS instances for an environment. Optionally display database URL.

    disco_rds.py list [--env ENV] [--url]

Create/update the RDS clusters in a environment from the `disco_rds.ini` config. Updates all RDS clusters in an environment by default or optionally specify a cluster to update.

    disco_rds.py update [--env ENV] [--cluster CLUSTER]

Delete a RDS cluster.

    disco_rds.py delete [--env ENV] [--cluster CLUSTER] [--skip-final-snapshot]

Delete old database snapshots.

    disco_rds.py cleanup_snapshots [--env ENV] [--age DAYS]

Clone a database from a different environment into the current environment. The new database will copy all configuration options from the source database and use the most recent database snapshot from source database.

    disco_rds.py clone [--env ENV] --source-db SOURCE_DB --source-env SOURCE_ENV

SSM
-------------------

Asiaq supports the use of *Amazon EC2 Run Command* and *SSM Config* to achieve remote execution of a set of pre-defined commands on any EC2 instance, which would not have been possible if SSH access to the instance is not allowed. While *Amazon EC2 Run Command* specifies how the commands are executed, *SSM Config* allows for defining the commands that can be executed. Each command definition is individually configured in an SSM document. For more info on SSM, please visit the [AWS documentation](http://docs.aws.amazon.com/ssm/latest/APIReference/Welcome.html).

### Commands and Configuration

The Asiaq command for provisioning SSM documents is `disco_ssm.py`. It operates based on the configuration files defined in the `ssm/documents` directory.

To get a list of the existing SSM documents currently defined in AWS:

    disco_ssm.py list-documents

To view the content of an existing document:

    disco_ssm.py get-documents --name DOCUMENT_NAME

To create a new document, first create a `DOCUMENT_NAME.ssm` file with the document content in `ssm/documents`. Then run the following `update-documents` command. Note that the final document name in AWS would be the same as the `DOCUMENT_NAME` you specify in the file name.

    disco_ssm.py update-documents

To modify or delete a document, first update the content of the file or remove the file from `ssm/documents`, respectively. Then run the same `update-documents` command as creating a new document.

By default, the `update-documents` command waits for all the operations to finish before existing. This behavior can be changed by using the `--no-wait` flag.

    disco_ssm.py update-documents --no-wait

The `update-documents` command also accepts the `--dry-run` flag, which causes the command display the changes that would have been applied to AWS if the flag was not specified.

    disco_ssm.py update-documents --dry-run

### Execution

The mechanism for executing SSM commands is `disco_aws.py exec-ssm`. See the `--help` of that subcommand for usage instructions.

Some important notes about executing SSM documents:

* You cannot pick the user that is used for executing the SSM document. All SSM documents are executed as the root user.
* SSM documents execute in parallel across all targets instances, not serially.
* SSM documents have a default timeout of one minute. If this timeout is not otherwise overwritten, the output will still be returned but the command itself will be marked as a failure.

#### Configuration

Executing SSM documents can be somewhat customized through modifications to `disco_aws.ini`. You can set the `default_ssm_s3_bucket` option under the `disco_aws` section in the `disco_aws.ini`. The value of this option should correspond to an S3 bucket where the output of SSM documents should be uploaded for longer term storage. If this option is not set, then the output of SSM documents will be limited to 2500 characters.

Testing Hostclasses
-------------------

Asiaq supports two kinds of tests for a hostclass out of the box. There are *smoke tests* and *integration tests*. As a rule of thumb, smoke tests test if the hostclass is working internally. Integration tests test if the hostclass is able to interact with external services correctly. An example of a smoke test might be making sure that the apache service started correctly. An example of an integration test might be making sure that the hostclass can communicate with an external database.

### Integration Tests

Asiaq only runs integration tests of a hostclass when that hostclass is being tested or updated, typically through the use of ```disco_deploy.py test``` or ```disco_deploy.py update```. In general, integration tests take the form of executing a script on a designated hostclass and either pass or fail depending on the exit code returned by the script. The test script is also passed an argument, typically used to denote what exact test should be run.


#### Configuration

Configuration of integration tests is spread across two places, ```disco_aws.ini``` and the pipeline file.

##### disco_aws.ini

There are a few configuration options for integration tests in ```disco_aws.ini```.

```ini
test_hostclass=mhcfootest
test_user=integration_tester
test_command=/opt/asiaq/bin/run_tests.sh
deployment_strategy=classic # One of [classic, blue_green]
```

* test_hostclass
  * The hostclass to execute ```test_command``` on. Typically a hostclass dedicated to testing one or more other hostclasses. For example, mhcfootest would probably be a hostclass dedicated to testing the mhcfoo hostclass.
* test_user
  * The user to execute ```test_command``` as.
* test_command
  * The command to execute the tests on ```test_hostclass``` as the ```test_user```. Typically a shell script with some logic for handling the test argument that is passed to it. The exit code of this command determines whether or not the integration tests were successful.
* deployment_strategy
  * The deployment strategy to use when deploying a new AMI. Either ```classic``` or ```blue_green```. The default is currently ```classic```.

These options can be specified in two places in ```disco_aws.ini```, in the ```[test]``` section or in a given hostclass' section. Below is an example of specifying defaults in the ```[test]``` section and overriding them for the mhcfoo hostclass.

```ini
[test]
test_hostclass=mhcgenerictester
test_user=asiaq_tester
test_command=/opt/asiaq/bin/run_tests.sh

[mhcbar]
...

[mhcfoo]
test_hostclass=mhcfootests
deployment_strategy=blue_green
```

In this example, we set defaults in the ```[test]``` section for all hostclasses. Then ```[mhcfoo]``` overrides those defaults to specify a different test_hostclass and deployment_strategy for itself.


##### Pipeline

Integration tests are also configured in the pipeline file. As mentioned above, the ```test_command``` is passed an argument when run. This argument is defined in the pipeline file, under the ```integration_test``` entry in the pipeline. If the entry is empty, then no integration test will be run during ```disco_deploy.py test``` or ```disco_deploy.py update```. Instead, those commands will simply wait for the hostclass to pass its smoke tests and pass if those pass.

Here's an example of a hostclass with integration tests and one without integration tests in the pipeline, using some of the example hostclasses from above:

```csv
sequence,hostclass,min_size,desired_size,max_size,instance_type,extra_disk,iops,smoke_test,integration_test,deployable
1,mhcgenerictest,1,1,1,c4.large,,,no,,yes
1,mhcfootest,1,1,1,c4.large,,,no,,yes
1,mhcbar,2,2,2,m4.large,,,no,mhcbar_integration,yes
1,mhcfoo,2,2,2,m4.large,,,no,mhcfoo_integration,yes
1,mhcnointegrationtests,2,2,2,m4.large,,,no,,yes
```

In the above pipeline, mhcbar will be integration tested by passing ```mhcbar_integration``` as the argument to the ```test_command``` on the generic ```test_hostclass``` defined in our example ```disco_aws.ini``` above. In contrast, mhcfoo will be integration tested by passing ```mhcfoo_integration``` as the argument to the ```test_command``` on it's specially defined ```test_hostclass```. And because mhcnointegrationtests left the ```integration_test``` column empty, no integration tests will be run for it.

#### Deployment Strategies

##### Classic

Classic deployment strategy is used by default. Instances with a new AMI are spun up in the existing ASG and automatically added to the existing nerve and ELB groups. There are a number of problems with this strategy, so it is slated for deprecation and removal soon. Let us speak no more of it.

##### Blue/Green

Blue/Green deployment strategy is currentlt opt-in only.

The process is as follows:

* A new ASG is created with an 'is_testing' tag and attached to a dedicated 'testing' ELB.
* After waiting for smoke tests, integration tests are run against the isolated 'testing' ASG.
  * ASG is isolated because it is attached to a separate ELB, a separate Nerve group, and its services should respect the 'is_testing' tag.
* After integration tests pass, the instances in the new ASG are taken out of testing mode by executing `sudo /etc/asiaq/bin/testing_mode.sh off` as the `test_user`.
  * Exiting testing mode should restore the nerve configs to their proper groups as well as grabbing ENIs/EIPs/etc.
* After exiting test mode, the new ASG is attached to the normal ELB and the deploy process pauses until the ELB registers and marks the new instances as healthy.
* After the new instances are marked as Healthy by the ELB, the old ASG is destroyed.

If at any point there is a problem, the new ASG and testing ELB will be destroyed. Service to the original ASG and ELB should not be interrupted.
