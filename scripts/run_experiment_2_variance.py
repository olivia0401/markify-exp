import argparse
import json
import random
import sys
import time

import pandas as pd

from src.config import INPUT_XLSX, OUTPUTS_DIR
from src.markify_client import (
    CallCapReachedError,
    QuotaExhaustedError,
    get_call_count,
    search,
    set_call_cap,
)

OUT_CSV = OUTPUTS_DIR / "exp2_variance_results.csv"
FULL_RESULTS_CSV = OUTPUTS_DIR / "exp2_full_results.csv"


def safe_to_csv(df, path, *, mode, header, max_retries=6):
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

    r1 = search(mark=name, classes=classes, databases=db_codes, min_score=0.0)
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
            r = search(mark=name, classes=classes, databases=db, min_score=0.0)
            total += r.elapsed
            subs.append({"db": db, "status": r.status, "elapsed": round(r.elapsed, 3)})
        row.update(
            was_split=True,
            total_elapsed=round(total, 3),
            sub_details_json=json.dumps(subs),
        )

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--per-stratum", type=int, default=2)
    ap.add_argument("--max-calls", type=int, default=1900,
                    help="Hard cap on API calls in this run (default 1900).")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing exp2_variance_results.csv.")
    args = ap.parse_args()

    set_call_cap(args.max_calls)

    if OUT_CSV.exists() and not args.force:
        print(f"ERROR: {OUT_CSV.name} already exists. Use --force to overwrite.")
        sys.exit(1)
    if OUT_CSV.exists() and args.force:
        OUT_CSV.unlink()
        print(f"--force: deleted existing {OUT_CSV.name}\n")

    if not FULL_RESULTS_CSV.exists():
        print(f"ERROR: {FULL_RESULTS_CSV.name} not found.")
        print("Run scripts.run_experiment_2 first to know which scenarios succeed.")
        sys.exit(1)
    full = pd.read_csv(FULL_RESULTS_CSV)
    ok_full = full[full["attempt_status"] == "ok"]
    ok_pairs = set(zip(
        ok_full["scenario_id"].astype(int),
        ok_full["name"].astype(str).str.strip(),
    ))
    if not ok_pairs:
        print("ERROR: no 'ok' scenarios in main run. Cannot measure variance.")
        sys.exit(1)

    df = pd.read_excel(INPUT_XLSX, sheet_name="Speed_Tests")
    df["_pair"] = list(zip(
        df["Scenario_ID"].astype(int),
        df["Name"].astype(str).str.strip(),
    ))
    df = df[df["_pair"].isin(ok_pairs)].drop(columns="_pair").copy()
    print(f"Pool: {len(df)} known-good (scenario_id, name) pairs.")

    rng = random.Random(42)
    sample = []
    for db_count, group in df.groupby("Database_Count"):
        rows = group.to_dict("records")
        rng.shuffle(rows)
        picked = rows[: args.per_stratum]
        sample.extend(picked)
        print(f"  Database_Count={db_count}: pool={len(rows)}, picked={len(picked)}")
    print(f"\nSampled {len(sample)} scenarios x {args.rounds} rounds. "
          f"Cap: {args.max_calls} calls.\n")

    write_header = not OUT_CSV.exists()
    start = time.monotonic()

    for round_idx in range(args.rounds):
        order = sample.copy()
        random.Random(42 + round_idx).shuffle(order)
        for sc in order:
            try:
                row = call_once(sc, round_idx)
            except (QuotaExhaustedError, CallCapReachedError) as e:
                print(f"\nStopped in round {round_idx+1}: {e}")
                print(f"Calls used: {get_call_count()}.")
                return
            safe_to_csv(pd.DataFrame([row]), OUT_CSV, mode="a", header=write_header)
            write_header = False
            print(f"r={round_idx+1:>2} sid={row['scenario_id']:>3} "
                  f"db={row['db_count']:>2} t={row['total_elapsed']:.2f}s "
                  f"[calls: {get_call_count()}/{args.max_calls}]")

    total = time.monotonic() - start
    print(f"\nDone in {total/60:.1f} min. Calls used: {get_call_count()}.")


if __name__ == "__main__":
    main()
