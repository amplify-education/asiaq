#!/bin/bash

echo "db_identifier: $DB_IDENTIFIER"

db_subnetgroup_name=$(aws rds describe-db-instances --db-instance-identifier $DB_IDENTIFIER \
--query 'DBInstances[*].DBSubnetGroup.[DBSubnetGroupName]' --output text)

vpc_security_group_ids=$(aws rds describe-db-instances --db-instance-identifier $DB_IDENTIFIER \
 --query 'DBInstances[*].VpcSecurityGroups[*].VpcSecurityGroupId' --output text)

aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier $DB_IDENTIFIER \
--target-db-instance-identifier $DB_IDENTIFIER-restored --use-latest-restorable-time \
--db-subnet-group-name $db_subnetgroup_name --vpc-security-group-ids $vpc_security_group_ids