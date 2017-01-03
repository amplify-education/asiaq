"""
This module contains logic that processes a VPC's Internet, VPN, and NAT gateways and the routes to them
"""

import logging
import time

from boto.exception import EC2ResponseError

from .resource_helper import (
    wait_for_state_boto3, find_or_create, create_filters, throttled_call)
from .disco_eip import DiscoEIP
from .disco_subnet import DYNO_NAT_TAG_KEY
from .exceptions import (TimeoutError, EIPConfigError)

logger = logging.getLogger(__name__)


VGW_STATE_POLL_INTERVAL = 2  # seconds
VGW_ATTACH_TIME = 600  # seconds. From observation, it takes about 300s to attach vgw


class DiscoVPCGateways(object):
    """
    This class takes care of processing of a VPC's Internet, VPN, and NAT gateways and the routes to them
    """
    def __init__(self, vpc, boto3_ec2):
        self.disco_vpc = vpc
        self.boto3_ec2 = boto3_ec2
        self.eip = DiscoEIP()

    def update_gateways_and_routes(self, dry_run=False):
        """ Find or create Internet and VPN gateways and update the routes to them """
        internet_gateway = find_or_create(self._find_internet_gw, self._create_internet_gw)
        vpn_gateway = self._find_and_attach_vpn_gw()

        for network in self.disco_vpc.networks.values():
            logger.info("Updating gateway routes for meta network: %s", network.name)
            route_tuples = self._get_gateway_route_tuples(network.name, internet_gateway, vpn_gateway)
            network.update_gateways_and_routes(route_tuples, dry_run)

    def destroy_igw_and_detach_vgws(self):
        """ Destroy Internet gateways and detach VPN gateways in a VPC """
        self._destroy_igws()
        self._detach_vgws()

    def _get_gateway_route_tuples(self, network_name, internet_gateway, vpn_gateway):
        route_tuples = []

        if internet_gateway:
            igw_routes = self.disco_vpc.get_config("{0}_igw_routes".format(network_name))
            if igw_routes:
                igw_routes = igw_routes.split(" ")
                for igw_route in igw_routes:
                    route_tuples.append((igw_route, internet_gateway['InternetGatewayId']))

        if vpn_gateway:
            vgw_routes = self.disco_vpc.get_config("{0}_vgw_routes".format(network_name))
            if vgw_routes:
                vgw_routes = vgw_routes.split(" ")
                for vgw_route in vgw_routes:
                    route_tuples.append((vgw_route, vpn_gateway['VpnGatewayId']))

        return route_tuples

    def _create_internet_gw(self):
        internet_gateway = throttled_call(self.boto3_ec2.create_internet_gateway)['InternetGateway']
        throttled_call(self.boto3_ec2.attach_internet_gateway,
                       InternetGatewayId=internet_gateway['InternetGatewayId'],
                       VpcId=self.disco_vpc.get_vpc_id())
        logger.debug("internet_gateway: %s", internet_gateway)

        return internet_gateway

    def _find_and_attach_vpn_gw(self):
        """If configured, attach VPN Gateway and create corresponding routes"""
        vgw = self._find_vgw()
        if vgw:
            logger.debug("Attaching VGW: %s.", vgw)
            if vgw.get('VpcAttachments') and vgw['VpcAttachments'][0]['State'] != 'detached' and \
                    vgw['VpcAttachments'][0]['VpcId'] != self.disco_vpc.get_vpc_id():

                logger.info("VGW %s already attached to %s. Will detach and reattach to %s.",
                            vgw['VpnGatewayId'], vgw['VpcAttachments'][0]['VpcId'],
                            self.disco_vpc.get_vpc_id())
                self._detach_vgws()
                logger.debug("Waiting 30s to avoid VGW 'non-existance' conditon post detach.")
                time.sleep(30)

            throttled_call(self.boto3_ec2.attach_vpn_gateway,
                           VpnGatewayId=vgw['VpnGatewayId'], VpcId=self.disco_vpc.get_vpc_id())
            logger.debug("Waiting for VGW to become attached.")

            self._wait_for_vgw_states(u'attached')
            logger.debug("VGW have been attached.")
        else:
            logger.info("No VGW to attach.")

        return vgw

    def _find_internet_gw(self):
        """Locate Internet Gateway that corresponds to this VPN"""
        igw_filter = create_filters({'attachment.vpc-id': [self.disco_vpc.vpc['VpcId']]})
        igws = throttled_call(self.boto3_ec2.describe_internet_gateways,
                              Filters=igw_filter)['InternetGateways']
        if len(igws) == 0:
            logger.debug("Cannot find the required Internet Gateway named for VPC %s.",
                         self.disco_vpc.vpc['VpcId'])
            return None

        return igws[0]

    def _find_vgw(self):
        """Locate VPN Gateway that corresponds to this VPN"""
        vgw_filter = create_filters({'tag:Name': [self.disco_vpc.environment_name]})
        vgws = throttled_call(self.boto3_ec2.describe_vpn_gateways, Filters=vgw_filter)
        if not len(vgws['VpnGateways']):
            logger.debug("Cannot find the required VPN Gateway named %s.", self.disco_vpc.environment_name)
            return None
        return vgws['VpnGateways'][0]

    def _check_vgw_states(self, state):
        """Checks if all VPN Gateways are in the desired state"""
        filters = create_filters({'tag:Name': [self.disco_vpc.environment_name]})
        states = []
        vgws = throttled_call(self.boto3_ec2.describe_vpn_gateways, Filters=filters)
        for vgw in vgws['VpnGateways']:
            if vgw.get('VpcAttachments'):
                for attachment in vgw['VpcAttachments']:
                    if state == u'detached':
                        states.append(attachment['State'] == state)
                    elif attachment['VpcId'] == self.disco_vpc.get_vpc_id():
                        states.append(attachment['State'] == state)
        logger.debug("%s of %s VGW attachments are now in state '%s'",
                     states.count(True), len(states), state)
        return states and all(states)

    def _wait_for_vgw_states(self, state, timeout=VGW_ATTACH_TIME):
        """Wait for all VPN Gateways to reach a specified state"""
        time_passed = 0
        while True:
            try:
                if self._check_vgw_states(state):
                    return True
            except EC2ResponseError:
                pass  # These are most likely transient, we will timeout if they are not

            if time_passed >= timeout:
                raise TimeoutError(
                    "Timed out waiting for VPN Gateways to change state to {0} after {1}s."
                    .format(state, time_passed))

            time.sleep(VGW_STATE_POLL_INTERVAL)
            time_passed += VGW_STATE_POLL_INTERVAL

    def _detach_vgws(self):
        """Detach VPN Gateways, but don't delete them so they can be re-used"""
        vgw_filter = create_filters({'attachment.state': ['attached'],
                                     'tag:Name': [self.disco_vpc.environment_name]})
        detached = False
        vpn_gateways = throttled_call(self.boto3_ec2.describe_vpn_gateways, Filters=vgw_filter)['VpnGateways']
        for vgw in vpn_gateways:
            logger.debug("Detaching VGW: %s.", vgw)
            if not throttled_call(self.boto3_ec2.detach_vpn_gateway,
                                  VpnGatewayId=vgw['VpnGatewayId'],
                                  VpcId=vgw['VpcAttachments'][0]['VpcId']):
                logger.error("Failed to detach %s from %s", vgw['VpnGatewayId'],
                             vgw['VpcAttachments'][0]['VpcId'])
            else:
                detached = True

        if not detached:
            return

        try:
            logger.debug("Waiting for VGWs to become detached.")
            self._wait_for_vgw_states(u'detached')
        except TimeoutError:
            logger.exception("Failed to detach VPN Gateways (Timeout).")

    def _destroy_igws(self):
        """ Find all gateways belonging to vpc and destroy them"""
        vpc_attachment_filters = create_filters(
            {'attachment.vpc-id': [self.disco_vpc.get_vpc_id()]})

        # delete gateways
        for igw in throttled_call(self.boto3_ec2.describe_internet_gateways,
                                  Filters=vpc_attachment_filters)['InternetGateways']:
            throttled_call(self.boto3_ec2.detach_internet_gateway,
                           InternetGatewayId=igw['InternetGatewayId'],
                           VpcId=self.disco_vpc.get_vpc_id())
            throttled_call(self.boto3_ec2.delete_internet_gateway, InternetGatewayId=igw['InternetGatewayId'])

    def update_nat_gateways_and_routes(self, dry_run=False):
        """ Update the NAT gateways and the routes to them for the VPC based on
        the config file """

        desired_nat_routes = set(self._parse_nat_gateway_routes_config())

        current_nat_routes = set()
        for network in self.disco_vpc.networks.values():
            nat_gateway_metanetwork = network.get_nat_gateway_metanetwork()
            if nat_gateway_metanetwork:
                current_nat_routes.add((network.name, nat_gateway_metanetwork))

        # Updating NAT gateways has to be done AFTER current NAT routes are calculated
        # because we don't want to delete existing NAT gateways before that.
        for network in self.disco_vpc.networks.values():
            self._update_nat_gateways(network, dry_run)

        routes_to_delete = current_nat_routes - desired_nat_routes
        logger.info("NAT gateway routes to delete (source, dest): %s", routes_to_delete)

        routes_to_add = desired_nat_routes - current_nat_routes
        logger.info("NAT gateway routes to add (source, dest): %s", routes_to_add)

        routes_check_for_update = desired_nat_routes & current_nat_routes
        logger.info("NAT gateway routes to check for update (source, dest): %s",
                    routes_check_for_update)

        if not dry_run:
            self._delete_nat_gateway_routes([route[0] for route in routes_to_delete])
            self._upsert_nat_gateway_routes(routes_to_add | routes_check_for_update)

    def _update_nat_gateways(self, network, dry_run=False):
        eips = self.disco_vpc.get_config("{0}_nat_gateways".format(network.name))
        if not eips:
            # No NAT config, delete the gateways if any
            logger.info("No NAT gateways defined for meta network %s. Deleting them if there's any.",
                        network.name)
            if not dry_run:
                network.delete_nat_gateways()
        else:
            if eips.lower() == "auto":
                logger.info("Setting up NAT gateways in meta network %s using dyno NATs.",
                            network.name)
                if not dry_run:
                    network.add_nat_gateways()

            else:
                eips = [eip.strip() for eip in eips.split(",")]

                allocation_ids = []
                for eip in eips:
                    address = self.eip.find_eip_address(eip)
                    if not address:
                        raise EIPConfigError("Couldn't find Elastic IP: {0}".format(eip))

                    allocation_ids.append(address.allocation_id)

                if allocation_ids:
                    logger.info("Setting up NAT gateways in meta network %s using these allocation IDs: %s",
                                network.name, allocation_ids)
                    if not dry_run:
                        network.add_nat_gateways(allocation_ids=allocation_ids)

    def _upsert_nat_gateway_routes(self, nat_gateway_routes):
        for route in nat_gateway_routes:
            self.disco_vpc.networks[route[0]].upsert_nat_gateway_route(self.disco_vpc.networks[route[1]])

    def _delete_nat_gateway_routes(self, meta_networks):
        for route in meta_networks:
            self.disco_vpc.networks[route].delete_nat_gateway_route()

    def _parse_nat_gateway_routes_config(self):
        """ Returns a list of tuples whose first value is the source meta network and
        whose second value is the destination meta network """
        result = []
        nat_gateway_routes = self.disco_vpc.get_config("nat_gateway_routes")
        if nat_gateway_routes:
            nat_gateway_routes = nat_gateway_routes.split(" ")
            for nat_gateway_route in nat_gateway_routes:
                network_pair = nat_gateway_route.split("/")
                result.append((network_pair[0].strip(), network_pair[1].strip()))

        return result

    def destroy_nat_gateways(self):
        """ Find all NAT gateways belonging to a vpc and destroy them"""
        nat_filter = {'Filters': create_filters({'vpc-id': [self.disco_vpc.vpc['VpcId']]})}
        nat_gateways = throttled_call(self.boto3_ec2.describe_nat_gateways, **nat_filter)['NatGateways']

        for nat_gateway in nat_gateways:
            throttled_call(self.boto3_ec2.delete_nat_gateway, NatGatewayId=nat_gateway['NatGatewayId'])

        # Need to wait for all the NAT gateways to be deleted
        wait_for_state_boto3(self.boto3_ec2.describe_nat_gateways, nat_filter,
                             'NatGateways', 'deleted', 'State')

        # Release EIPs of dynamically configured subnets
        subnet_filter = {'Filters': create_filters(
            {
                'vpc-id': [self.disco_vpc.vpc['VpcId']],
                'tag-key': [DYNO_NAT_TAG_KEY]
            }
        )}
        subnet_ids = [
            sn['SubnetId']
            for sn in throttled_call(self.boto3_ec2.describe_subnets, **subnet_filter)['Subnets']
        ]
        eips = [
            gw['NatGatewayAddresses'][0]['PublicIp']
            for gw in nat_gateways
            if gw['SubnetId'] in subnet_ids
        ]
        if eips:
            logger.info("Releasing temporary NAT EIPs: %s.", " ".join(eips))
        for eip in eips:
            self.eip.release(eip)
