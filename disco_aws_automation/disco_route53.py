"""
Some code to manage Route53.
Route53 manages our domains and DNS records.
"""
import logging

from boto.route53 import Route53Connection
from boto.route53.record import Record

from disco_aws_automation.resource_helper import throttled_call


class DiscoRoute53(object):
    """
    A simple class to manage Route53
    """

    def __init__(self):
        self.route53 = Route53Connection()

    def list_zones(self):
        """Returns a list of Hosted Zones in Route53"""
        return sorted(throttled_call(self.route53.get_zones), key=lambda zone: zone.name)

    def create_record(self, hosted_zone_name, record_name, record_type, value):
        """
        Create a DNS record. Update an existing record if one already exists.
        Args:
            hosted_zone_name (str): the domain of the Hosted Zone
            record_name (str): the name of the record
            record_type (str): the type of record (A, AAAA, CNAME, etc)
            value (str): a single value to insert into the record
        """
        zone = throttled_call(self.route53.get_zone, hosted_zone_name)

        record = Record(record_name, record_type, ttl=5)
        record.add_value(value)

        logging.info("Setting Record %s of type %s to %s", record_name, record_type, value)

        changes = throttled_call(self.route53.get_all_rrsets, zone.id)
        changes.add_change_record('UPSERT', record)

        throttled_call(changes.commit)

    def list_records(self, hosted_zone_name):
        """
        List all DNS records
        Args:
            hosted_zone_name (str): the domain of the Hosted Zone
        """
        zone = throttled_call(self.route53.get_zone, hosted_zone_name)
        return sorted(throttled_call(self.route53.get_all_rrsets, zone.id), key=lambda record: record.name)

    def delete_record(self, hosted_zone_name, record_name, record_type):
        """
        Delete a DNS record
        Args:
            hosted_zone_name (str): the domain of the Hosted Zone
            record_name (str): the name of the record
            record_type (str): the type of record (A, AAAA, CNAME, etc)
        """
        zone = throttled_call(self.route53.get_zone, hosted_zone_name)

        records = throttled_call(self.route53.get_all_rrsets, zone.id)
        # Needs a default None, to prevent StopIteration when the iterator is exhausted
        selected_record = next((record for record in records
                                if record.name == record_name and record.type == record_type), None)
        if not selected_record:
            logging.info("Record '%s' in '%s' hosted zone does not exist. Nothing to delete",
                         record_name, hosted_zone_name)
            return
        records.add_change_record('DELETE', selected_record)
        throttled_call(records.commit)

    def delete_records_by_value(self, record_type, value):
        """
        Delete records across all zones that contain the specified value
        Args:
            record_type (str): the type of record (A, AAAA, CNAME, etc)
            value: the value to search for
        """
        logging.info('Deleting %s records with value "%s"', record_type, value)
        for record in self.get_records_by_value(record_type, value):
            self.delete_record(record['zone_name'], record['record_name'], record_type)

    def get_records_by_value(self, record_type, value):
        """
        Get records across all zones that contain the specified value
        Args:
            record_type (str): the type of record (A, AAAA, CNAME, etc)
            value: the value to search for
        """
        return [{'zone_name': zone.name, 'record_name': record.name}
                for zone in self.list_zones()
                for record in self.list_records(zone.name)
                if record.type == record_type and value in record.resource_records]
