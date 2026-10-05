"""Replay the source CSV into Kafka as realistic business events.

Examples
    python -m profitpulse.kafka.producer --csv data/source/profitpulse_synthetic_dataset.csv            # batch replay
    python -m profitpulse.kafka.producer --csv ... --rate 50 --limit 5000                              # ~50 events/s
    python -m profitpulse.kafka.producer --csv ... --corrupt-rate 0.01 --seed 7                        # + injected faults

Events are published in timestamp order (the file itself is shuffled), routed by
event type and keyed by branch. The source file is never modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from confluent_kafka import KafkaException, Producer

from .. import contract
from ..db import tracked_run
from ..settings import Settings, get_settings
from .admin import ensure_topics
from .faults import FaultInjector

log = logging.getLogger("profitpulse.producer")


def load_events(csv_path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        events = [contract.csv_row_to_event(row) for row in csv.DictReader(fh)]
    # Replay in business-time order; event_id breaks ties deterministically.
    events.sort(key=lambda e: (str(e["event_timestamp"]), str(e["event_id"])))
    return events[:limit] if limit else events


class Publisher:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.producer = Producer({
            "bootstrap.servers": settings.kafka_bootstrap,
            "enable.idempotence": True,          # no duplicates from producer retries
            "acks": "all",
            "compression.type": "lz4",
            "linger.ms": 20,
            "batch.num.messages": 10000,
            "queue.buffering.max.messages": 200000,
            "client.id": "profitpulse-replay",
        })
        self.delivered: Counter[str] = Counter()
        self.errors: list[str] = []

    def _on_delivery(self, err, msg) -> None:
        if err is not None:
            self.errors.append(f"{msg.topic()}: {err}")
        else:
            self.delivered[msg.topic()] += 1

    def send(self, short_topic: str, key: bytes | None, value: bytes) -> None:
        topic = self.s.topic(short_topic)
        while True:
            try:
                self.producer.produce(topic, value=value, key=key, on_delivery=self._on_delivery)
                break
            except BufferError:                    # local queue full: let delivery callbacks drain it
                self.producer.poll(0.2)
        self.producer.poll(0)

    def flush(self) -> None:
        remaining = self.producer.flush(120)
        if remaining:
            raise RuntimeError(f"{remaining} messages still undelivered after flush timeout")


def publish(csv_path: Path, rate: float = 0.0, limit: int | None = None, corrupt_rate: float = 0.0,
            seed: int = 42, faults: list[str] | None = None, settings: Settings | None = None,
            faults_only: bool = False) -> dict[str, Any]:
    """Publish events; returns a summary suitable for reconciliation."""
    s = settings or get_settings()
    ensure_topics(s)
    events = load_events(csv_path, limit)
    injector = FaultInjector(corrupt_rate, seed, faults)
    pub = Publisher(s)

    started = time.monotonic()
    last_log = started
    sent_clean = sent_faulty = 0

    for n, event in enumerate(events):
        if rate > 0:                                 # simulate a live feed
            due = started + (n / rate)
            while (delay := due - time.monotonic()) > 0:
                pub.producer.poll(min(delay, 0.05))
        if not faults_only:                          # faults-only: bad data arriving on its own, later
            pub.send(contract.route(event["event_type"]), contract.message_key(event), contract.encode(event))
            sent_clean += 1

        injected = injector.maybe_fault(event)
        if injected:
            _, payload = injected
            if isinstance(payload, bytes):           # unparseable message: cannot be routed by type
                pub.send(contract.DEAD_LETTER, None, payload)
            else:
                pub.send(contract.route(payload.get("event_type")), contract.message_key(payload), contract.encode(payload))
            sent_faulty += 1

        if time.monotonic() - last_log > 5:
            log.info("published %d/%d events (%.0f/s)", n + 1, len(events), (n + 1) / (time.monotonic() - started))
            last_log = time.monotonic()

    pub.flush()
    elapsed = time.monotonic() - started
    if pub.errors:
        raise RuntimeError(f"{len(pub.errors)} delivery errors, first: {pub.errors[0]}")
    return {
        "source_events": len(events),
        "clean_messages": sent_clean,
        "fault_messages": sent_faulty,
        "total_messages": sent_clean + sent_faulty,
        "delivered_by_topic": dict(pub.delivered),
        "delivered_total": sum(pub.delivered.values()),
        "faults_injected": injector.injected,
        "expected_rejections_by_rule": injector.expected_rejections(),
        "elapsed_seconds": round(elapsed, 2),
        "events_per_second": round((sent_clean + sent_faulty) / elapsed, 1) if elapsed else None,
        "mode": f"rate={rate}/s" if rate > 0 else "batch",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", type=Path, default=None, help="source CSV (default: data/source/profitpulse_synthetic_dataset.csv)")
    ap.add_argument("--rate", type=float, default=0.0, help="events per second; 0 = batch replay as fast as possible")
    ap.add_argument("--limit", type=int, default=None, help="publish only the first N events (in time order)")
    ap.add_argument("--corrupt-rate", type=float, default=0.0,
                    help="probability of adding one extra faulty message per event (quarantine testing)")
    ap.add_argument("--faults-only", action="store_true",
                    help="publish only the injected faulty messages (needs --corrupt-rate); originals are not re-sent")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--faults", nargs="*", default=None, help="restrict injection to these fault names")
    ap.add_argument("--record-run", action="store_true", help="record the publish in ops.pipeline_runs")
    ap.add_argument("--summary-json", type=Path, default=None, help="write the summary to this file")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    csv_path = args.csv or (s.data_dir / "source" / "profitpulse_synthetic_dataset.csv")
    if not csv_path.exists():
        log.error("source CSV not found: %s", csv_path)
        return 2

    try:
        if args.record_run:
            with tracked_run("publish") as run:
                summary = publish(csv_path, args.rate, args.limit, args.corrupt_rate, args.seed, args.faults, s, args.faults_only)
                run.rows_in = summary["source_events"]
                run.rows_out = summary["delivered_total"]
                run.details = {k: summary[k] for k in ("faults_injected", "expected_rejections_by_rule",
                                                       "delivered_by_topic", "mode", "elapsed_seconds")}
        else:
            summary = publish(csv_path, args.rate, args.limit, args.corrupt_rate, args.seed, args.faults, s, args.faults_only)
    except KafkaException as exc:
        log.error("Kafka error: %s", exc)
        return 1

    print(json.dumps(summary, indent=2))
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
