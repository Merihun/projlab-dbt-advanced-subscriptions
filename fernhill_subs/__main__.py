"""Command line for the practice data. See ``python -m fernhill_subs --help``."""

from __future__ import annotations

import argparse

from fernhill_subs import data
from fernhill_subs.load import load


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fernhill_subs", description="Fernhill Box practice data.")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="Write data/batch_1/, data/batch_2/ and the two seeds (always the same bytes).")
    gen.add_argument("--root", default=".", help="The project folder (default: here).")
    ld = sub.add_parser("load", help="Load one batch into the schema raw of a target's warehouse.")
    ld.add_argument("--batch", type=int, choices=data.BATCHES, required=True)
    ld.add_argument("--target", choices=("dev", "prod", "ci"), default="dev",
                    help="dev and prod: the lab's PostgreSQL; ci: the DuckDB file (default: dev).")  # fmt: skip
    ld.add_argument("--data", default="data", help="Where generate wrote the batches (default: data).")
    args = parser.parse_args(argv)
    if args.command == "generate":
        counts = data.generate(args.root)
        files = sum(1 for name in counts if name.startswith("batch_"))
        print(f"Wrote {files} files to data/batch_1/ and data/batch_2/, and seeds/currencies.csv and seeds/fx_rates.csv")
        return 0
    counts = load(args.batch, args.target, args.data)
    where = "DuckDB" if args.target == "ci" else "PostgreSQL"
    print(f"Loaded batch {args.batch} into raw ({where}, target {args.target}):")
    for table, rows in counts.items():
        print(f"  raw.{table:20} {rows:>6} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
