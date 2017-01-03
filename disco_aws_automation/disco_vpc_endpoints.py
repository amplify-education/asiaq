"""
Create / Destroy VPC endpoints
"""

import json

import boto3
from .resource_helper import throttled_call

S3_POLICY = {
    "Statement": [
        {
            "Action": "*",
            "Effect": "Allow",
            "Resource": "*",
            "Principal": "*"
        }
    ]
}


class DiscoVPCEndpoints(object):
    """
    Create / Destroy VPC endpoints
    """

    def __init__(self, vpc_id, boto3_ec2_client=None):
        self.vpc_id = vpc_id
        self._boto3_ec2 = boto3_ec2_client

    @property
    def boto3_ec2(self):
        """
        Lazily creates boto3 EC2 connection
        """
        if not self._boto3_ec2:
            self._boto3_ec2 = boto3.client('ec2')
        return self._boto3_ec2

    def service_name(self, service):
        """
        Find full, region specific service endpoint name from colloquial
        name (eg: s3 -> com.amazonaws.us-west-2.s3)
        """
        endpoint_responce = throttled_call(self.boto3_ec2.describe_vpc_endpoint_services)
        service_names = endpoint_responce["ServiceNames"]

        matching_service_names = [
            service_name
            for service_name in service_names
            if service_name.endswith(service)
        ]
        return matching_service_names[0]

    def filters(self, service):
        """
        Return all endpoints of a particular service in the vpc.
        """
        return [
            {"Name": "vpc-id", "Values": [self.vpc_id]},
            {"Name": "service-name", "Values": [self.service_name(service)]}
        ]

    def _all_vpc_route_tables_ids(self):
        route_table_response = throttled_call(self.boto3_ec2.describe_route_tables,
                                              Filters=[{"Name": "vpc-id", "Values": [self.vpc_id]}])
        return [
            rt["RouteTableId"]
            for rt in route_table_response["RouteTables"]
        ]

    def update_s3(self, route_table_ids=None, policy_dict=None, dry_run=False):
        """
        Creates/updates s3 endpoint and update corresponding route tables
        """
        route_table_ids = route_table_ids or self._all_vpc_route_tables_ids()
        policy_dict = policy_dict or S3_POLICY

        self.delete_s3(dry_run)

        if not dry_run:
            throttled_call(self.boto3_ec2.create_vpc_endpoint,
                           VpcId=self.vpc_id,
                           ServiceName=self.service_name("s3"),
                           PolicyDocument=json.dumps(policy_dict),
                           RouteTableIds=route_table_ids)

    def list_s3_ids(self):
        """
        Return all s3 endpoint ids in the vpc.
        """
        endpoint_results = throttled_call(self.boto3_ec2.describe_vpc_endpoints,
                                          Filters=self.filters('s3'))
        return [
            endpoint['VpcEndpointId']
            for endpoint in endpoint_results['VpcEndpoints']
        ]

    def delete_s3(self, dry_run=False):
        """
        Delete all s3 endpoints in vpc
        """
        s3_ids = self.list_s3_ids()
        if not s3_ids:
            return False

        if not dry_run:
            throttled_call(self.boto3_ec2.delete_vpc_endpoints,
                           VpcEndpointIds=s3_ids)
        return True

    def update(self, dry_run=False):
        """
        Create/Update all VPC endpoints
        """
        self.update_s3(dry_run=dry_run)

    def delete(self, dry_run=False):
        """
        Delete all VPC endpoints
        """
        self.delete_s3(dry_run)
