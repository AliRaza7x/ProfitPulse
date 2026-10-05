"""Kafka administration and inspection utilities (topics, offsets, tailing)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Iterator

from confluent_kafka import Consumer, KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from ..contract import TOPICS
from ..settings import Settings, get_settings


def admin_client(settings: Settings | None = None) -> AdminClient:
    s = settings or get_settings()
    return AdminClient({"bootstrap.servers": s.kafka_bootstrap})


def wait_for_broker(settings: Settings | None = None, timeout_s: float = 60.0) -> None:
    """Block until the broker answers metadata requests."""
    admin = admin_client(settings)
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            admin.list_topics(timeout=5)
            return
        except KafkaException as exc:  # broker not up yet
            last = exc
            time.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {(settings or get_settings()).kafka_bootstrap}: {last}")


def ensure_topics(settings: Settings | None = None) -> dict[str, str]:
    """Create any missing ProfitPulse topics. Returns {topic: 'created' | 'exists'}."""
    s = settings or get_settings()
    wait_for_broker(s)
    admin = admin_client(s)
    existing = set(admin.list_topics(timeout=10).topics)
    wanted = {s.topic(t.short_name): t for t in TOPICS}
    to_create = [NewTopic(name, num_partitions=spec.partitions, replication_factor=1)
                 for name, spec in wanted.items() if name not in existing]
    result = {name: "exists" for name in wanted if name in existing}
    if to_create:                                   # create_topics rejects an empty list
        for name, future in admin.create_topics(to_create).items():
            future.result()
            result[name] = "created"
    return result


def topic_names(settings: Settings | None = None) -> list[str]:
    s = settings or get_settings()
    return [s.topic(t.short_name) for t in TOPICS]


@dataclass(frozen=True)
class PartitionOffsets:
    topic: str
    partition: int
    low: int
    high: int


def watermarks(settings: Settings | None = None, topics: list[str] | None = None) -> list[PartitionOffsets]:
    """Earliest and latest offset of every partition of the given (default: all ProfitPulse) topics."""
    s = settings or get_settings()
    topics = topics or topic_names(s)
    consumer = Consumer({"bootstrap.servers": s.kafka_bootstrap, "group.id": "profitpulse-watermarks",
                         "enable.auto.commit": False})
    try:
        meta = consumer.list_topics(timeout=10)
        out: list[PartitionOffsets] = []
        for topic in topics:
            if topic not in meta.topics:
                continue
            for pid in sorted(meta.topics[topic].partitions):
                low, high = consumer.get_watermark_offsets(TopicPartition(topic, pid), timeout=10)
                out.append(PartitionOffsets(topic, pid, low, high))
        return out
    finally:
        consumer.close()


def tail(topic: str, count: int = 5, settings: Settings | None = None) -> Iterator[dict]:
    """Yield the last `count` messages of each partition of a topic (for debugging)."""
    s = settings or get_settings()
    consumer = Consumer({"bootstrap.servers": s.kafka_bootstrap, "group.id": "profitpulse-tail",
                         "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    try:
        meta = consumer.list_topics(topic, timeout=10)
        for pid in sorted(meta.topics[topic].partitions):
            low, high = consumer.get_watermark_offsets(TopicPartition(topic, pid), timeout=10)
            start = max(low, high - count)
            if start >= high:
                continue
            consumer.assign([TopicPartition(topic, pid, start)])
            for _ in range(high - start):
                msg = consumer.poll(5.0)
                if msg is None or msg.error():
                    break
                yield {"partition": msg.partition(), "offset": msg.offset(),
                       "key": msg.key().decode() if msg.key() else None,
                       "value": json.loads(msg.value()) if msg.value() else None}
    finally:
        consumer.close()
