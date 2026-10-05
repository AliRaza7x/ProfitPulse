"""Command-line entry point:  python -m profitpulse <command>

Pipeline stages are also importable and are what Airflow tasks call.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def _cmd_migrate(_: argparse.Namespace) -> int:
    from .db import run_migrations
    applied = run_migrations()
    print(json.dumps({"applied": applied}))
    return 0


def _cmd_topics(args: argparse.Namespace) -> int:
    from .kafka import admin
    if args.action == "ensure":
        print(json.dumps(admin.ensure_topics()))
    else:
        for w in admin.watermarks():
            print(f"{w.topic}[{w.partition}] low={w.low} high={w.high} messages={w.high - w.low}")
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    from .kafka import admin
    from .settings import get_settings
    for msg in admin.tail(get_settings().topic(args.topic), args.count):
        print(json.dumps(msg))
    return 0


def _cmd_publish(args: argparse.Namespace) -> int:
    from .kafka import producer
    return producer.main(args.rest)


def _cmd_validation_report(args: argparse.Namespace) -> int:
    from . import reporting
    print(json.dumps(reporting.run(write_docs=args.docs)))
    return 0


def _stage(module: str):
    def _run(_: argparse.Namespace) -> int:
        import importlib
        mod = importlib.import_module(f"profitpulse.{module}")
        print(json.dumps(mod.run(), default=str))
        return 0
    return _run


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "publish":            # the producer owns its own flags (see producer --help)
        return _cmd_publish(argparse.Namespace(rest=argv[1:]))
    p = argparse.ArgumentParser(prog="profitpulse", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply SQL migrations").set_defaults(fn=_cmd_migrate)

    t = sub.add_parser("topics", help="ensure Kafka topics or show offsets")
    t.add_argument("action", choices=["ensure", "stats"])
    t.set_defaults(fn=_cmd_topics)

    tl = sub.add_parser("tail", help="show recent messages of a topic (short name, e.g. sales)")
    tl.add_argument("topic")
    tl.add_argument("-n", "--count", type=int, default=3)
    tl.set_defaults(fn=_cmd_tail)

    pub = sub.add_parser("publish", help="replay the CSV into Kafka (see producer --help)")
    pub.add_argument("rest", nargs=argparse.REMAINDER)
    pub.set_defaults(fn=_cmd_publish)

    for name, module, helptext in [
        ("ingest", "spark.ingest", "Kafka -> raw zone"),
        ("clean", "spark.clean", "raw -> cleaned zone, quarantine rejects"),
        ("transform", "spark.transform", "cleaned -> dimensions and facts"),
        ("load", "spark.load_core", "transformed -> PostgreSQL core"),
        ("features", "spark.features", "core -> analytical feature tables (Spark)"),
        ("analytics", "analytics.run", "feature tables -> anomalies, scorecards, leakage, KPIs"),
        ("publish-analytics", "spark.publish", "analytics outputs -> PostgreSQL analytics schema"),
        ("dq", "quality.checks", "post-load data-quality gates (warehouse + analytics)"),
    ]:
        sub.add_parser(name, help=helptext).set_defaults(fn=_stage(module))

    vr = sub.add_parser("validation-report", help="end-to-end validation report (counts, financials, anomalies, timings)")
    vr.add_argument("--docs", action="store_true", help="also write docs/VALIDATION_REPORT.md")
    vr.set_defaults(fn=_cmd_validation_report)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
