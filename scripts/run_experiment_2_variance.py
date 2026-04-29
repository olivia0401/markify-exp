"""Experiment 2 — Variance subset: 10 scenarios × N rounds for CLT estimate.

Strategy: sample ONLY from scenarios whose first call already succeeded in
the main timing run (attempt_status == "ok" in exp2_full_results.csv). This
guarantees that what we measure is real single-call latency variance, not
HTTP 401 response variance — which is the bug an earlier version had.

Pre-requisite: run_experiment_2 must have completed first (we read its
output to pick the sample).
"""

import argparse
import json
import random
import sys
import time

import pandas as pd

from src.config import INPUT_XLSX, OUTPUTS_DIR
from src.markify_client import QuotaExhaustedError, search

OUT_CSV = OUTPUTS_DIR / "exp2_variance_results.csv"
FULL_RESULTS_CSV = OUTPUTS_DIR / "exp2_full_results.csv"


def safe_to_csv(df, path, *, mode, header, max_retries=6):
    """Retry on PermissionError (OneDrive sync locks)."""
    delay = 0.5
    for attempt in range(max_retries):
        try:
            df.to_csv(path, mode=mode, header=header, index=False)
            return
        except PermissionError:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def call_once(sc, round_idx):
    name = str(sc["Name"]).strip()
    classes = str(sc["Nice_Classes"]).strip()
    db_codes = str(sc["Database_Codes"]).strip()
    dbs = [d.strip() for d in db_codes.split(",")]

    r1 = search(mark=name, classes=classes, databases=db_codes,
                min_score=0.0, http_timeout=30)
    row = {
        "scenario_id": sc["Scenario_ID"],
        "name": name,
        "db_codes": db_codes,
        "round_idx": round_idx,
        "db_count": sc["Database_Count"],
        "class_count": sc["Class_Count"],
        "original_status": r1.status,
        "original_elapsed": round(r1.elapsed, 3),
        "was_split": False,
        "total_elapsed": round(r1.elapsed, 3),
        "sub_details_json": None,
    }

    if r1.status in ("timeout", "http_error") and len(dbs) > 1:
        subs, total = [], 0.0
        for db in dbs:
            r = search(mark=name, classes=classes, databases=db,
                       min_score=0.0, http_timeout=30)
            total += r.elapsed
            subs.append({"db": db, "status": r.status, "elapsed": round(r.elapsed, 3)})
        row.update(was_split=True, total_elapsed=round(total, 3),
                   sub_details_json=json.dumps(subs))

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--per-stratum", type=int, default=2)
    ap.add_argument(
        "--force", action="store_true",
        help="Overwrite existing exp2_variance_results.csv (otherwise abort).",
    )
    args = ap.parse_args()

    # Safety: refuse to append onto data from a previous (possibly bad) run.
    if OUT_CSV.exists() and not args.force:
        print(f"ERROR: {OUT_CSV.name} already exists.")
        print("       Rename or delete it first (e.g. mv to "
              "exp2_variance_results_old.csv), then re-run.")
        print("       Or pass --force to overwrite.")
        sys.exit(1)
    if OUT_CSV.exists() and args.force:
        OUT_CSV.unlink()
        print(f"--force: deleted existing {OUT_CSV.name}\n")

    # Pre-requisite: main timing run has produced exp2_full_results.csv,
    # from which we pick scenarios that succeeded as single calls.
    #
    # IMPORTANT: Scenario_ID is NOT unique in Sheet 2 — each appears 21 times
    # paired with different names. Within a single Scenario_ID, some (name, dbs)
    # combinations succeeded ("ok") and others failed (e.g. when db_codes='US').
    # We MUST filter on the (scenario_id, name) tuple, not just scenario_id,
    # to avoid picking a failing variant of an "ok" scenario_id.
    if not FULL_RESULTS_CSV.exists():
        print(f"ERROR: {FULL_RESULTS_CSV.name} not found.")
        print("       Run `python -m scripts.run_experiment_2` first so we "
              "know which scenarios produce real single-call timings.")
        sys.exit(1)
    full = pd.read_csv(FULL_RESULTS_CSV)
    ok_full = full[full["attempt_status"] == "ok"]
    ok_pairs = set(zip(
        ok_full["scenario_id"].astype(int),
        ok_full["name"].astype(str).str.strip(),
    ))
    if not ok_pairs:
        print("ERROR: no scenarios had attempt_status=='ok' in the main run.")
        print("       Variance can't be measured for single-call timing.")
        sys.exit(1)

    df = pd.read_excel(INPUT_XLSX, sheet_name="Speed_Tests")
    df["_pair"] = list(zip(
        df["Scenario_ID"].astype(int),
        df["Name"].astype(str).str.strip(),
    ))
    df = df[df["_pair"].isin(ok_pairs)].drop(columns="_pair").copy()
    print(f"Pool: {len(df)} (scenario_id, name) variants known to succeed "
          f"as single calls in main run")

    rng = random.Random(42)
    sample = []
    for db_count, group in df.groupby("Database_Count"):
        rows = group.to_dict("records")
        rng.shuffle(rows)
        picked = rows[: args.per_stratum]
        sample.extend(picked)
        print(f"  Database_Count={db_count}: pool={len(rows)}, picked={len(picked)}")
    print(f"\nSampled {len(sample)} scenarios × {args.rounds} rounds "
          f"(~{len(sample) * args.rounds * 2 / 60:.0f} min @ 2s rate limit)\n")

    write_header = not OUT_CSV.exists()
    start = time.monotonic()

    for round_idx in range(args.rounds):
        order = sample.copy()
        random.Random(42 + round_idx).shuffle(order)
        for sc in order:
            try:
                row = call_once(sc, round_idx)
            except QuotaExhaustedError as e:
                print(f"\n✗ Quota exhausted in round {round_idx+1}: {e}")
                print(f"  Variance CSV has whatever rows we collected before this.")
                return
            safe_to_csv(pd.DataFrame([row]), OUT_CSV,
                        mode="a", header=write_header)
            write_header = False
            print(f"r={round_idx+1:>2} sid={row['scenario_id']:>3} "
                  f"db={row['db_count']:>2} t={row['total_elapsed']:.2f}s")

    print(f"\nDone in {(time.monotonic()-start)/60:.1f} min")


if __name__ == "__main__":
    main()