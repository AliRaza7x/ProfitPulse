"""Spark job: Kafka -> raw zone.

Incremental batch ingestion with explicit offset tracking:

  1. read each partition's committed `next_offset` from ops.kafka_offsets
     (or its earliest offset the first time) and its current high watermark,
  2. read exactly that offset range with Spark's Kafka source,
  3. write it to lake/raw/events/ingest_batch=<id>/ as Parquet, untouched
     (value kept as the original string, plus topic/partition/offset/timestamp),
  4. verify rows written == messages expected, then commit offsets and the batch
     registry in ONE transaction.

If step 2-3 fail, offsets don't move and the next run re-reads the same range. A
batch directory that isn't registered in ops.ingest_batches is never read
downstream, so a crash between 3 and 4 cannot create duplicates.
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import functions as F

from ..db import fetch_all, tracked_run, transaction
from ..kafka.admin import topic_names, watermarks
from ..settings import get_settings
from .session import get_lake, get_spark

log = logging.getLogger("profitpulse.ingest")


def committed_offsets() -> dict[tuple[str, int], int]:
    rows = fetch_all("SELECT topic, partition_id, next_offset FROM ops.kafka_offsets")
    return {(r["topic"], r["partition_id"]): r["next_offset"] for r in rows}


def registered_batches() -> list[str]:
    return [r["batch_id"] for r in fetch_all("SELECT DISTINCT batch_id FROM ops.ingest_batches ORDER BY batch_id")]


def plan_ranges() -> list[dict]:
    """Offset range to ingest for EVERY partition (possibly empty).

    Spark's Kafka source needs start/end offsets for all partitions of all
    subscribed topics, including ones with nothing new, so empty ranges stay in.
    """
    committed = committed_offsets()
    ranges = []
    for w in watermarks():
        start = max(committed.get((w.topic, w.partition), w.low), w.low)   # retention may have moved `low`
        ranges.append({"topic": w.topic, "partition": w.partition, "from": start, "until": max(w.high, start)})
    return ranges


def _offsets_json(ranges: list[dict], key: str) -> str:
    out: dict[str, dict[str, int]] = {}
    for r in ranges:
        out.setdefault(r["topic"], {})[str(r["partition"])] = r[key]
    return json.dumps(out)


def drop_orphan_batches(lake_raw: Path) -> list[str]:
    """Remove raw batch directories that were never registered (interrupted runs)."""
    keep = set(registered_batches())
    removed = []
    if lake_raw.exists():
        for d in lake_raw.glob("ingest_batch=*"):
            if d.name.split("=", 1)[1] not in keep:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
    return removed


def run() -> dict:
    s = get_settings()
    lake = get_lake(s)
    with tracked_run("ingest") as run_handle:
        orphans = drop_orphan_batches(Path(lake.raw()))
        ranges = plan_ranges()
        expected = sum(r["until"] - r["from"] for r in ranges)
        if expected == 0:
            log.info("No new Kafka messages to ingest.")
            run_handle.rows_in = run_handle.rows_out = 0
            run_handle.details = {"batch_id": None, "note": "nothing new", "orphans_removed": orphans}
            return {"batch_id": None, "messages": 0}

        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        spark = get_spark("profitpulse-ingest", s)
        df = (spark.read.format("kafka")
              .option("kafka.bootstrap.servers", s.kafka_bootstrap)
              .option("subscribe", ",".join(topic_names(s)))
              .option("startingOffsets", _offsets_json(ranges, "from"))
              .option("endingOffsets", _offsets_json(ranges, "until"))
              .option("failOnDataLoss", "true")
              .load())
        raw = df.select(
            F.col("topic"),
            F.col("partition").alias("kafka_partition"),
            F.col("offset").alias("kafka_offset"),
            F.col("timestamp").cast("timestamp_ntz").alias("kafka_timestamp"),
            F.col("key").cast("string").alias("key"),
            F.col("value").cast("string").alias("value"),
            F.lit(batch_id).alias("ingest_batch"),
            F.current_timestamp().cast("timestamp_ntz").alias("ingested_at"),
        )
        target = lake.raw(batch_id)
        raw.write.mode("overwrite").parquet(target)

        landed = spark.read.parquet(target)
        written = landed.count()
        per_partition = {(r["topic"], r["kafka_partition"]): r["n"]
                         for r in landed.groupBy("topic", "kafka_partition").agg(F.count("*").alias("n")).collect()}
        if written != expected:
            shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(f"Ingest mismatch: expected {expected} messages, landed {written}. Offsets not advanced.")

        with transaction(s) as conn, conn.cursor() as cur:
            for r in ranges:
                n = r["until"] - r["from"]
                if per_partition.get((r["topic"], r["partition"]), 0) != n:
                    raise RuntimeError(f"Partition count mismatch for {r['topic']}[{r['partition']}]")
                if n > 0:                              # empty partitions only need their offset recorded
                    cur.execute(
                        "INSERT INTO ops.ingest_batches (batch_id, topic, partition_id, from_offset, until_offset, messages, run_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (batch_id, r["topic"], r["partition"], r["from"], r["until"], n, run_handle.run_id))
                cur.execute(
                    "INSERT INTO ops.kafka_offsets (topic, partition_id, next_offset) VALUES (%s,%s,%s) "
                    "ON CONFLICT (topic, partition_id) DO UPDATE SET next_offset = EXCLUDED.next_offset, updated_at = now()",
                    (r["topic"], r["partition"], r["until"]))
        run_handle.rows_in = expected
        run_handle.rows_out = written
        run_handle.details = {"batch_id": batch_id, "partitions": len(ranges), "orphans_removed": orphans,
                              "messages_by_topic": _by_topic(ranges)}
        log.info("Ingested %d messages into raw batch %s", written, batch_id)
        return {"batch_id": batch_id, "messages": written}


def _by_topic(ranges: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in ranges:
        out[r["topic"]] = out.get(r["topic"], 0) + (r["until"] - r["from"])
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    result = run()
    print(json.dumps(result))
    sys.exit(0)
