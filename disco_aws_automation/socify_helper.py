"""
This module has utility functions for working with the socify lambda
"""
import logging
from ConfigParser import NoOptionError

import requests
from .disco_config import read_config

logger = logging.getLogger(__name__)


class SocifyHelper(object):
    """Socify helper provides the function to invoke the Socify lambda functions"""
    SOC_EVENT_OK = 100
    SOC_EVENT_BAD_DATA = 200
    SOC_EVENT_ERROR = 300

    def __init__(self, ticket_id, dry_run, command, sub_command=None, config=None):
        self._ticket_id = ticket_id
        self.dry_run = dry_run
        self._command = command
        self._sub_command = sub_command

        if config:
            self._config = config
        else:
            self._config = read_config()
        self._socify_url = None

    def _build_event_url(self):
        """
        Build the socify event url using the socify configuration data provided in the disco_aws.ini file
        :return: The socify URL
        """
        if not self._socify_url:
            try:
                self._socify_url = self._config.get("socify", "socify_baseurl")
            except NoOptionError:
                logger.warning("The property socify_baseurl is not set in your disco_aws.ini file. The "
                               "deploy action won't be logged in your ticket. Please make sure to add the "
                               "definition for socify_baseurl in the [socify] section.")
                return
        return self._socify_url + "/event"

    def _build_event_json(self, status, **kwargs):
        """
        Build the event JSON for the Socify Event associated to the executed command
        :param status: The status of the executed command that we are going to log
        :param hostclass: The hostclass for which the command was executed
        :param message: An optional error message
        :return: The Event JSON for the associated Event
        """
        event_info = {'status': status}
        if self._sub_command:
            event_info['sub_cmd'] = self._sub_command

        event_info.update(kwargs)

        event_json = {"ticketId": self._ticket_id,
                      "cmd": self._command,
                      "data": event_info}

        return event_json

    def send_event(self, status, **kwargs):
        """
        helper function used to send a socify event
        :param status: The status of the executed command that we are going to log
        :param hostclass: The hostclass for which the command was executed
        :param message:An optional error message
        """
        if not self._ticket_id or self.dry_run:
            return

        url = self._build_event_url()
        if not url:
            # We were not able to build the event URL
            return

        data = self._build_event_json(status, **kwargs)
        try:
            headers = {'Content-Type': 'application/json'}
            response = requests.post(url=url, headers=headers, json=data)
            response.raise_for_status()
            status = response.status_code
            rsp_json = response.json()
            logger.info("received response status %s data: %s", status, rsp_json)
        except requests.HTTPError:
            rsp_json = response.json()
            logger.error("Socify event failed with the following error: %s", rsp_json)
        except Exception:
            logger.exception("Failed to send event to Socify")

        return