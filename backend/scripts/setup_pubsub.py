"""Idempotently create the TokenLens Pub/Sub telemetry buffer.

Creates on tokenlens-504404:
  - main topic:        tokenlens-traces
  - dead-letter topic:  tokenlens-traces-dlq
  - pull subscription: tokenlens-traces-worker (on the main topic, with a
    dead-letter policy routing to the DLQ topic after 5 failed deliveries)

Also grants the project's Pub/Sub service agent the IAM roles it needs to
actually perform dead-lettering (publish to the DLQ topic, and subscribe on
the source subscription) — without this, messages that exceed the delivery
attempt limit are silently dropped instead of dead-lettered.

Run: uv run python scripts/setup_pubsub.py
"""

from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1
from google.iam.v1 import policy_pb2

from config import settings

PROJECT_ID = settings.gcp_project_id
PROJECT_NUMBER = "418874072229"
TOPIC_ID = settings.pubsub_topic_traces
DLQ_TOPIC_ID = settings.pubsub_dlq_topic
SUBSCRIPTION_ID = settings.pubsub_subscription_traces
MAX_DELIVERY_ATTEMPTS = 5

PUBSUB_SERVICE_AGENT = f"serviceAccount:service-{PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"


def _create_topic_if_missing(publisher: pubsub_v1.PublisherClient, topic_id: str) -> str:
    topic_path = publisher.topic_path(PROJECT_ID, topic_id)
    try:
        publisher.create_topic(request={"name": topic_path})
        print(f"Topic created: {topic_path}")
    except AlreadyExists:
        print(f"Topic already exists: {topic_path}")
    return topic_path


def _grant_publisher_role(publisher: pubsub_v1.PublisherClient, topic_path: str) -> None:
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    binding = next(
        (b for b in policy.bindings if b.role == "roles/pubsub.publisher"), None
    )
    if binding is None:
        binding = policy_pb2.Binding(role="roles/pubsub.publisher", members=[])
        policy.bindings.append(binding)
    if PUBSUB_SERVICE_AGENT not in binding.members:
        binding.members.append(PUBSUB_SERVICE_AGENT)
        publisher.set_iam_policy(
            request={"resource": topic_path, "policy": policy}
        )
        print(f"Granted roles/pubsub.publisher on {topic_path} to Pub/Sub service agent")
    else:
        print(f"Pub/Sub service agent already has roles/pubsub.publisher on {topic_path}")


def main() -> None:
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()

    topic_path = _create_topic_if_missing(publisher, TOPIC_ID)
    dlq_topic_path = _create_topic_if_missing(publisher, DLQ_TOPIC_ID)

    # The Pub/Sub service agent needs to publish to the DLQ topic to
    # dead-letter a message.
    _grant_publisher_role(publisher, dlq_topic_path)

    subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)
    dead_letter_policy = {
        "dead_letter_topic": dlq_topic_path,
        "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS,
    }
    try:
        subscriber.create_subscription(
            request={
                "name": subscription_path,
                "topic": topic_path,
                "dead_letter_policy": dead_letter_policy,
                "ack_deadline_seconds": 30,
            }
        )
        print(f"Subscription created: {subscription_path}")
    except AlreadyExists:
        print(f"Subscription already exists: {subscription_path}")

    # The Pub/Sub service agent also needs subscriber+publisher on the
    # subscription's *own* topic isn't required, but it does need
    # roles/pubsub.subscriber on the subscription itself to read messages
    # it's about to dead-letter.
    policy = subscriber.get_iam_policy(request={"resource": subscription_path})
    binding = next(
        (b for b in policy.bindings if b.role == "roles/pubsub.subscriber"), None
    )
    if binding is None:
        binding = policy_pb2.Binding(role="roles/pubsub.subscriber", members=[])
        policy.bindings.append(binding)
    if PUBSUB_SERVICE_AGENT not in binding.members:
        binding.members.append(PUBSUB_SERVICE_AGENT)
        subscriber.set_iam_policy(
            request={"resource": subscription_path, "policy": policy}
        )
        print(f"Granted roles/pubsub.subscriber on {subscription_path} to Pub/Sub service agent")
    else:
        print(f"Pub/Sub service agent already has roles/pubsub.subscriber on {subscription_path}")

if __name__ == "__main__":
    main()
