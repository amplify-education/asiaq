#!/bin/bash

# this script has to be crond safe. So we include all commands that we are using with absolute path
# the path should be updated if the linux distribution changes file locations.

# include configs for crond safe path and credentials

source "/opt/wgen/etc/smoketest_status.conf"

function get_hostclass() {
    cat "/opt/wgen/etc/hostclass"
}

function get_smoketests() {
    IFS=$'\n'
    cat /opt/wgen/etc/smoketest.conf
}

function verify_init_status() {
    IFS=$' '
    local service=$1
    local on_levels=("${@:2}")
    local check_status=$($CHKCONFIG --list "$service")
    if [[ "$check_status" = "" ]]
    then
        >&2 echo "service $service is not in chkconfig"
        return 1
    else
        for level in "${on_levels[@]}"
        do
            local count=$(echo $check_status|$GREP "$level:on"|$WC -l)
            if [ "$count" -eq "0" ]
            then
                >&2 echo "service $service is NOT enabled on startup"
                return 2
            fi
        done
        return 0
    fi
}


function is_service_running() {
    local service_name=$1
    if "$SERVICE_SYSTEMD" status "$service_name" &> /dev/null || "$SERVICE_SYSV" $service_name status &> /dev/null
    then
        return 0
    else
        >&2 echo "service $service_name is not running"
        return 1
    fi
}

# ntp specific func tests
function check_ntpstat() {
    local check_result=$($NTPSTAT|$WC -l)
    if [ "$check_result" -gt "0" ]
    then
        return 0
    else
        >&2 echo "ntpd is not working"
        return 1
    fi
}

# redis specific func test
function check_redis() {
    local password=$($GREP "^requirepass" /etc/redis-password.conf|$SED -e "s/requirepass//g" -e "s/\"//g")
    # checking the redis by insering a key that should not be used by any other code
    local key="disco.redis.smoketest.key"
    local value=$(date --rfc-3339=seconds)
    local key_ttl="300"
    $REDIS_CLI -a $password DEL $key &> /dev/null
    $REDIS_CLI -a $password SETEX $key $key_ttl "$value" &> /dev/null
    local check_result=$($REDIS_CLI -a $password GET $key)
    if [[ "$check_result" == "$value" ]]
    then
        return 0
    else
        >&2 echo "redis is not working"
        return 1
    fi
}

# zookeeper specific func test
function check_zookeeper() {
    local port=$($GREP clientPort /opt/wgen/zookeeper/conf/zoo.cfg|$SED -e "s/clientPort\=//g")
    local check_result=$(printf ruok|$NC localhost $port)
    if [[ "$check_result" = "imok" ]]
    then
        return 0
    else
        >&2 echo "zookeeper is not working"
        return 1
    fi
}

#check rsyslogd listening
function check_rsyslog_port(){
    local port=$($GREP "type=\"imtcp\"" /etc/rsyslog.conf |$SED -e 's/)//g' -e 's/input(type="imtcp" port="//g' -e 's/\"//')
    $NC -v -w 0 localhost $port &> /dev/null
    local check_status=$?
    if [ "$check_status" = "0" ]
    then
        return 0
    else
        >&2 echo "rsyslogd is not listening"
        return 1
    fi
}

#check admin proxy
function check_admin_proxy() {
    # get external ip
    local external_ip=$1
    # unset proxy
    unset http_proxy
    unset https_proxy
    unset HTTP_PROXY
    unset HTTPS_PROXY
    return $(get_https_status "$external_ip" 443)
}

#verify dnsmasq works via dig
function check_dnsmasq() {
    $DIG google.com @localhost &> /dev/null
    local check_status=$?
    if [ "$check_status" = "0" ];
    then
        return 0
    else
        >&2 echo "Dnsmasq is not working"
        return 1
    fi
}

#check rabbitmq status
function check_rabbitmq()
{
    $SUDO $RABBITMQCTL status &> /dev/null
    if [[ "$?" = "0" ]]
    then
        return 0
    else
        >&2 echo "Rabbitmq is not working"
        return 1
    fi
}

# get mongodb password
function get_mongo_pass()
{
    local mongo_user="${MONGO_USER:-disco}"
    if [ -e "$MONGO_PASS_FILE" ];
    then
        # awk in English, Find the line contains db.addUser("disco" from /opt/wgen/discodb/configure_mongo.js
        # then pick the 2nd column, remove all ",),; and use the last one for disco user password
        $AWK '/db.addUser\("'$mongo_user'",/ {pass = $2} END{gsub(/"|\)|;/, "", pass); print pass}' $MONGO_PASS_FILE
    else
        echo ""
    fi
}

# get mongodb password
function get_mongo3_pass()
{
    local mongo_user="${MONGO_USER:-disco}"
    if [ -e "$MONGO_PASS_FILE" ];
    then
        # awk in English, Find the line contains db.createUser("username" from config file
        # then pick the 2nd column, remove all ",),; and use the last one for disco user password
        # TODO: rewrite with some python script ot use service instead
        $AWK '/db.auth\("'$mongo_user'",/ {pass = $3} END{gsub(/"|\)|;/, "", pass); print pass}' $MONGO_PASS_FILE
    else
        echo ""
    fi
}

function check_mongod()
{
    local mongo_pass=$(get_mongo_pass)
    # If mongo password is not found because no password file.
    # And we are checking mongodb on the hostclass. Something is serious broken in mongodb hostclass.
    # We will trigger smoketest failure to force devs to fix mongo related hostclass.
    $MONGO_STAT --username $MONGO_USER --password $mongo_pass -h localhost -n 1 &> /dev/null
    if [ "$?" = "0" ]
    then
        return 0
    else
        >&2 echo "Mongodb is not working"
        return 1
    fi
}

function check_mongod_admin()
{
    local mongo_pass=$(get_mongo3_pass)
    # If mongo password is not found because no password file.
    # And we are checking mongodb on the hostclass. Something is serious broken in mongodb hostclass.
    # We will trigger smoketest failure to force devs to fix mongo related hostclass.
    $MONGO_STAT -u $MONGO_USER -p $mongo_pass -h localhost -n 1 --authenticationDatabase admin &> /dev/null
    if [ "$?" = "0" ]
    then
        return 0
    else
        >&2 echo "Mongodb is not working"
        return 1
    fi
}

function get_status() {

  local host=$1
  local port=$2
  local service_name=$3
  if [ -z "$4" ]
  then
        local status_page="liveops/status"
  else
        local status_page=$4
  fi
  local status_url="$host:$port/$service_name/$status_page"

  status=$($CURL -s -o /dev/null -w %{http_code} $status_url)
  if [[ $status != "200" ]] ; then
      >&2 echo "Failed to get 200 response from status end point"
      return 1
  else
      return 0
  fi
}

function get_https_status() {

  local host="$1"
  local port="$2"
  local service_name="$3"
  local status_url="https://$host:$port/$service_name/"
  local expected_status="$4"

  if [[ "$expected_status" = "" ]]
  then
    expected_status="200"
  fi

  status=$($CURL --insecure -s -o /dev/null -w %{http_code} $status_url)
  if [[ "$status" != "$expected_status" ]] ; then
      >&2 echo "Failed to get "$expected_status" response from status end point"
      return 1
  else
      return 0
  fi
}

function is_celery_running() {
    if [[ $($PS -ucelery | $WC -l) -le 1 ]] ; then
        >&2 echo "Celery is not running" 1>&2
        return 1
    else
        return 0
    fi
}

function check_celery_workers() {
    source /etc/sysconfig/celeryd
    local check_result=$($CELERYD_CHDIR/bin/celery inspect ping|$GREP $HOSTNAME)
    if [[ "$check_result" == "" ]]
    then
        >&2 echo "Celery workers is not running" 1>&2
        return 1
    else
        return 0
    fi
}

function check_root_disk_space() {
    local percent_used=$(df -k / | tail -1 | sed -r "s/.* ([0-9]+)%.*/\1/g")
    if [[ "$percent_used" -gt 98 ]]; then
        echo "Disk is full" >&2
        return 1
    else
        return 0
    fi
}
