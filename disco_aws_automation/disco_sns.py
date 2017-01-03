'''Contains DiscoSNS class for manipulating SNS topics'''
import logging
import itertools

import boto
from boto.exception import BotoServerError

logger = logging.getLogger(__name__)


class DiscoSNS(object):
    """
    Class for manipulating SNS topics
    """

    def __init__(self, connection=None, account_id=None):
        self.sns = connection or boto.connect_sns()

        # account_id is useful for constructing topic ARNs
        # we get the account_id per https://groups.google.com/forum/#!topic/boto-users/QhASXlNBm40
        if not account_id:
            try:
                # Attempt to look up ARN from user information.
                arn = boto.connect_iam().get_user().arn
            except BotoServerError:
                # Instance Roles have no user ID, so we fetch the instance profile arn
                logger.debug(
                    "Failed to lookup ARN from user ID. "
                    "Attempting to lookup ARN from InstanceProfile instead."
                )
                arn = boto.utils.get_instance_metadata(data='meta-data/iam/')['info']['InstanceProfileArn']
            account_id = arn.split(':')[4]
        self.account_id = account_id

    def topic_arn_from_name(self, name):
        """Returns something like: arn:aws:sns:us-east-1:162873917757:my_topic_name"""
        return ":".join(["arn", "aws", "sns", self.sns.region.name, self.account_id, name])

    def create_topic(self, name):
        """Creates a topic with the given name, if one doesn't exist already."""
        self.sns.create_topic(name)

    def delete_topic(self, name):
        """Deletes a topic with the given name."""
        self.sns.delete_topic(self.topic_arn_from_name(name))

    def subscribe_email(self, topic_name, email):
        """
        Subscribes an email address to a topic.
        Note that this will trigger a confirmation email from AWS to the target email, inviting the
        recipient to join the SNS topic. No topic notifications will be sent until recipient joins.
        """
        self.sns.subscribe(self.topic_arn_from_name(topic_name), "email", email)

    def subscribe_http(self, topic_name, url):
        """Subscribes an HTTP(s) callback to a topic."""
        protocol = url.split(":")[0]
        self.sns.subscribe(self.topic_arn_from_name(topic_name), protocol, url)

    def subscribe(self, topic_name, endpoints):
        """
        Subscribes a topic to a list of endpoints.
        An endpoint can be one of: an HTTP(s) URL or a list of comma separated email addresses
        """
        for endpoint in endpoints:
            if "@" in endpoint:
                self.subscribe_email(topic_name, endpoint)
            elif endpoint.startswith("http"):
                self.subscribe_http(topic_name, endpoint)
            elif endpoint.strip() == "":
                continue  # do nothing on empty subscriptions
            else:
                raise ValueError("Failed to determine endpoint type: %s" % endpoint)

    @staticmethod
    def get_topics_to_delete(existing_topics, desired_topics, env):
        """Returns list of topics that needs to be deleted"""
        return [topic
                for topic in set(existing_topics) - set(desired_topics)
                if topic.find("_") != -1 and topic.split("_")[-2] == env]

    @staticmethod
    def get_subscriptions_to_delete(existing_subscriptions_by_topic, desired_subscriptions_by_topic, env):
        """Returns lists of subscriptions that needs to be deleted"""
        return [subscription_arn
                for topic in existing_subscriptions_by_topic.keys()
                if topic.find("_") != -1 and topic.rsplit("_")[-2] == env
                for subscription_arn, subscription_endpoint in
                existing_subscriptions_by_topic[topic].iteritems()
                if subscription_endpoint not in desired_subscriptions_by_topic.get(topic, [])]

    # >15 local variables is actually a good thing in the context of immutability
    # pylint: disable=R0914
    def update_sns_with_notifications(self, notifications, env, delete=False, dry_run=False):
        """
        Updates SNS topics and subscriptions to match the ones given.
        If `delete` is True then it also deletes existing topics and subscriptions that were
        not included in `notifications`
        """
        desired_topics = [notification.name for notification in notifications]
        desired_subscriptions_by_topic = {
            notification.name: notification.endpoints
            for notification in notifications}

        existing_topic_arns = [
            topic["TopicArn"]
            for topic in self.sns.get_all_topics()["ListTopicsResponse"]["ListTopicsResult"]["Topics"]]

        existing_topics = [arn.split(":")[-1] for arn in existing_topic_arns]

        existing_subscriptions = [
            subscription
            for subscription in self.sns.get_all_subscriptions()[
                "ListSubscriptionsResponse"]["ListSubscriptionsResult"]["Subscriptions"]
            if subscription["SubscriptionArn"] != "PendingConfirmation"]  # pending is managed by aws
        existing_subscriptions_by_topic = {
            topic_arn.split(":")[-1]: {
                subscription["SubscriptionArn"]: subscription["Endpoint"]
                for subscription in group}
            for topic_arn, group in itertools.groupby(
                existing_subscriptions, lambda subscription: subscription["TopicArn"])}

        topics_to_delete = DiscoSNS.get_topics_to_delete(existing_topics, desired_topics, env)

        topics_to_delete_arn = [self.topic_arn_from_name(topic) for topic in topics_to_delete]

        subscriptions_to_delete = DiscoSNS.get_subscriptions_to_delete(existing_subscriptions_by_topic,
                                                                       desired_subscriptions_by_topic,
                                                                       env)

        if topics_to_delete_arn:
            logger.warning("Found %s extraneous topics: %s", len(topics_to_delete_arn), topics_to_delete_arn)
        if subscriptions_to_delete:
            logger.warning("Found %s extraneous subscriptions: %s",
                           len(subscriptions_to_delete), subscriptions_to_delete)
        if desired_topics:
            logger.info("The following topics and their subscriptions will be updated: %s", desired_topics)
        if delete:
            logger.info("The following topics will be deleted: %s", topics_to_delete_arn)
            logger.info("The following subscriptions will be deleted: %s", subscriptions_to_delete)

        if not dry_run:
            for notification in notifications:
                self.create_topic(notification.name)
                self.subscribe(notification.name, notification.endpoints)
            if delete:
                for topic in topics_to_delete_arn:
                    self.sns.delete_topic(topic)
                for subscription in subscriptions_to_delete:
                    self.sns.unsubscribe(subscription)
