"""
This module has utility functions for working with networks
"""
import random

from math import ceil, log
from netaddr import IPNetwork, IPSet


def calc_subnet_offset(num_subnets):
    """
    Calculate the size difference between a network and its subnets if there are a certain number of
    equally sized subnets


    Returns (int): The difference in cidr bits of a network and the subnets.
                   For example breaking a /20 network into 4 subnets will create /22 subnets returning 2.

    """
    return int(ceil(log(num_subnets, 2)))


def get_random_free_subnet(network_cidr, network_size, occupied_network_cidrs):
    """
    Pick a random available subnet from a bigger network
    Args:
        network_cidr (str): CIDR string describing a network
        network_size (int): The number of bits for the CIDR of the subnet
        occupied_network_cidrs (List[str]): List of CIDR strings describing existing networks
                                            to avoid overlapping with

    Returns str: The CIDR of a randomly chosen subnet that doesn't intersect with
                 the ip ranges of any of the given other networks
    """
    possible_subnets = IPNetwork(network_cidr).subnet(int(network_size))
    occupied_networks = [IPSet(IPNetwork(cidr)) for cidr in occupied_network_cidrs]

    # find the subnets that don't overlap with any other networks
    available_subnets = [subnet for subnet in possible_subnets
                         if all([IPSet(subnet).isdisjoint(occupied_network)
                                 for occupied_network in occupied_networks])]

    return random.choice(available_subnets) if available_subnets else None
