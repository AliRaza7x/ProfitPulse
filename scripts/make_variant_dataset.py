"""CLI for profitpulse.variant: write a copy of the dataset with an injected product-return anomaly.

Usage:  python scripts/make_variant_dataset.py --product P0932 --multiplier 3.5
Output: data/derived/profitpulse_variant_returns.csv  (the source CSV is untouched)
Run it where pandas is available (the tools container has it):
  docker compose run --rm tools python scripts/make_variant_dataset.py --product P0932
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from profitpulse.variant import inject_returns  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=ROOT / "data" / "source" / "profitpulse_synthetic_dataset.csv")
    ap.add_argument("--product", default="P0932", help="product to give an excess of returns")
    ap.add_argument("--multiplier", type=float, default=3.5, help="target return count as a multiple of the original")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "derived" / "profitpulse_variant_returns.csv")
    args = ap.parse_args()

    df = pd.read_csv(args.source)
    variant = inject_returns(df, args.product, args.multiplier)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    variant.to_csv(args.out, index=False)
    added = len(variant) - len(df)
    print(f"Wrote {args.out} ({len(variant):,} rows, {added} extra RETURN rows for {args.product}). Source file unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
