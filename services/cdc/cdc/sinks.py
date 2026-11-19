"""Where decoded transactions go. A sink's send() returns only once the event is safely stored,
because the reader confirms the LSN to Postgres right after it.
"""

import json
import os
import sys
from typing import Any, Protocol

from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic

TOPIC = "liverag.changes"
MAX_MESSAGE_BYTES = 16 * 1024 * 1024  # one message = one transaction; a re-seed is several MB


class Sink(Protocol):
    def send(self, tx: dict[str, Any]) -> None: ...
    def close(self) -> None: ...


class StdoutSink:
    """For debugging: one JSON line per transaction."""

    def send(self, tx: dict[str, Any]) -> None:
        print(json.dumps(tx, ensure_ascii=False), flush=True)

    def close(self) -> None:
        pass


class KafkaSink:
    """Publishes each transaction as ONE message to a single-partition topic.

    One partition keeps every transaction in commit order, and one message per transaction keeps
    it whole, so the indexer can apply it atomically. (Many partitions would scale further but
    would split transactions and lose the global order.)
    """

    def __init__(self, bootstrap: str, topic: str = TOPIC) -> None:
        self.topic = topic
        self._ensure_topic(bootstrap)
        self.producer = Producer({
            "bootstrap.servers": bootstrap,
            "enable.idempotence": True,  # broker drops duplicates caused by producer retries
            "acks": "all",               # wait until the broker has stored the message
            "compression.type": "zstd",
            "message.max.bytes": MAX_MESSAGE_BYTES,
        })
        self.error: Exception | None = None

    def _ensure_topic(self, bootstrap: str) -> None:
        admin = AdminClient({"bootstrap.servers": bootstrap})
        if self.topic in admin.list_topics(timeout=10).topics:
            return
        futures = admin.create_topics([NewTopic(
            self.topic, num_partitions=1, replication_factor=1,
            config={"max.message.bytes": str(MAX_MESSAGE_BYTES)},
        )])
        for future in futures.values():
            future.result()
        print(f"Created topic {self.topic}", file=sys.stderr, flush=True)

    def _delivered(self, err: Any, msg: Any) -> None:
        if err is not None:
            self.error = KafkaException(err)

    def send(self, tx: dict[str, Any]) -> None:
        self.producer.produce(
            self.topic,
            key=tx["commit_lsn"].encode(),
            value=json.dumps(tx, ensure_ascii=False).encode(),
            on_delivery=self._delivered,
        )
        # Block until the broker acknowledges. Only then may the reader confirm the LSN.
        if self.producer.flush(30) > 0:
            raise TimeoutError("Redpanda did not acknowledge the message within 30 s")
        if self.error:
            error, self.error = self.error, None
            raise error

    def close(self) -> None:
        self.producer.flush(30)


def make_sink(name: str) -> Sink:
    if name == "stdout":
        return StdoutSink()
    return KafkaSink(os.environ.get("KAFKA_BOOTSTRAP", "127.0.0.1:19092"))