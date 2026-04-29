"""Experiment 2 — Full timing run across all 756 scenarios.

Goal: Measure latency per scenario to extrapolate batch runtimes
      for 500 / 1k / 5k / 10k / 20k names in production.

Strategy: Try the original config (30s timeout); if it fails AND
          db_count > 1, split per-DB and sum sub-elapsed.
          CSV keeps every Sheet 2 metadata column for factor analysis.

Resumable: skips (scenario_id, name) pairs already in CSV.
Shuffled order (fixed seed) for cache resistance and stable resume.

Input:  data/Markify tests.xlsx  (sheet: Speed_Tests)
Output: outputs/exp2_full_results.csv

Run: python -m scripts.run_experiment_2
"""

import json
import random
import time
from datetime import datetime, timezone

import pandas as pd

from src.config import INPUT_XLSX, OUTPUTS_DIR
from src.markify_client import QuotaExhaustedError, search

OUT_CSV = OUTPUTS_DIR / "exp2_full_results.csv"
SHUFFLE_SEED = 42  # reproducible order across re-runs
EXP2_HTTP_TIMEOUT = 30.0 # Generous timeout to avoid false "ok" from HTTP 401 responses


def safe_to_csv(df, path, *, mode: str, header: bool, max_retries: int = 6):
    """Append to CSV with retry-on-PermissionError (OneDrive sync locks)."""
    delay = 0.5
    for attempt in range(max_retries):
        try:
            df.to_csv(path, mode=mode, header=header, index=False)
            return
        except PermissionError as e:
            if attempt == max_retries - 1:
                print(f"  ✗ CSV write failed after {max_retries} retries: {e}")
                raise
            print(
                f"  ⚠ CSV locked (attempt {attempt+1}/{max_retries}), "
                f"sleeping {delay:.1f}s — close Excel / wait for OneDrive sync"
            )
            time.sleep(delay)
            delay *= 2


def normalize_value(v) -> str:
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v).strip()


def parse_databases(s: str) -> list[str]:
    return [d.strip() for d in s.split(",") if d.strip()]


def call_scenario(sc: dict) -> dict:
    """Run one scenario. Returns one row dict for CSV."""
    name = normalize_value(sc["Name"])
    classes = normalize_value(sc["Nice_Classes"])
    db_codes_raw = normalize_value(sc["Database_Codes"])
    dbs = parse_databases(db_codes_raw)

    # --- Attempt 1: original config ---
    r1 = search(
        mark=name, classes=classes, databases=db_codes_raw,
        mode="knockout", min_score=0.0, page_limit=100,
        http_timeout=EXP2_HTTP_TIMEOUT,
    )

    splittable = r1.status in ("timeout", "http_error") and len(dbs) > 1

    row = {
        # Sheet 2 metadata (every column — needed for factor analysis)
        "scenario_id": sc["Scenario_ID"],
        "name": name,
        "name_type": sc["Name_Type"],
        "name_length": sc["Name_Length"],
        "db_codes": db_codes_raw,
        "db_count": sc["Database_Count"],
        "db_scenario": sc["DB_Scenario"],
        "nice_classes": classes,
        "class_count": sc["Class_Count"],
        "class_scenario": sc["Class_Scenario"],
        # Attempt 1 (the natural call)
        "original_status": r1.status,
        "original_elapsed": round(r1.elapsed, 3),
        "original_http_code": r1.http_code,
        "original_error": (r1.error or "")[:200] if r1.error else None,
        "original_n_results": (
            len((r1.data or {}).get("result", [])) if r1.status == "ok" else 0
        ),
        # Final outcome (filled below if split)
        "was_split": False,
        "n_sub_calls": 0,
        "total_elapsed": round(r1.elapsed, 3),
        "attempt_status": r1.status,
        "sub_details_json": None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if not splittable:
        return row

    # --- Attempt 2: split per DB ---
    sub_results = []
    sub_total_elapsed = 0.0
    n_ok = 0
    for db in dbs:
        r_sub = search(
            mark=name, classes=classes, databases=db,
            mode="knockout", min_score=0.0, page_limit=100,
            http_timeout=EXP2_HTTP_TIMEOUT,
        )
        sub_total_elapsed += r_sub.elapsed
        if r_sub.status == "ok":
            n_ok += 1
        sub_results.append({
            "db": db,
            "status": r_sub.status,
            "elapsed": round(r_sub.elapsed, 3),
            "http_code": r_sub.http_code,
            "n_results": (
                len((r_sub.data or {}).get("result", [])) if r_sub.status == "ok" else 0
            ),
        })

    if n_ok == len(dbs):
        final_status = "split_ok"
    elif n_ok > 0:
        final_status = "split_partial"
    else:
        final_status = "split_failed"

    row.update({
        "was_split": True,
        "n_sub_calls": len(sub_results),
        # total_elapsed = sum of productive sub-call times. We deliberately
        # do NOT include original_elapsed (the failed first attempt) here:
        # for extrapolation we want the true cost of getting the data, with
        # the wasted original kept separate for "is multi-DB worth trying?"
        # analysis. Both numbers are in the CSV.
        "total_elapsed": round(sub_total_elapsed, 3),
        "attempt_status": final_status,
        "sub_details_json": json.dumps(sub_results),
    })
    return row


def main():
    df = pd.read_excel(INPUT_XLSX, sheet_name="Speed_Tests")
    print(f"Loaded {len(df)} scenarios from Sheet 2.")

    # Resume key is (scenario_id, name) because Scenario_ID is NOT unique
    # in Sheet 2 — each ID appears 21 times across different names (756 = 36 × 21).
    done_pairs = set()
    if OUT_CSV.exists():
        prev = pd.read_csv(OUT_CSV)
        done_pairs = set(zip(
            prev["scenario_id"].astype(int),
            prev["name"].astype(str).str.strip(),
        ))
        print(f"Resuming: {len(done_pairs)} (scenario_id, name) pairs already in CSV.")

    # Shuffle for cache resistance; seeded so resume order is consistent
    scenarios = df.to_dict("records")
    random.Random(SHUFFLE_SEED).shuffle(scenarios)
    todo = [
        sc for sc in scenarios
        if (int(sc["Scenario_ID"]), str(sc["Name"]).strip()) not in done_pairs
    ]
    print(f"Will process {len(todo)} scenarios.\n")

    start = time.monotonic()
    write_header = not OUT_CSV.exists()

    for i, sc in enumerate(todo, 1):
        try:
            row = call_scenario(sc)
        except QuotaExhaustedError as e:
            print(f"\n✗ Quota exhausted at scenario {i}/{len(todo)}: {e}")
            print(f"  CSV holds {i-1} new scenarios; resume after quota reset.")
            return
        safe_to_csv(
            pd.DataFrame([row]),
            OUT_CSV, mode="a", header=write_header,
        )
        write_header = False

        # One-line progress log
        if row["was_split"]:
            flag = f"  [SPLIT × {row['n_sub_calls']}, orig={row['original_status']}]"
        elif row["attempt_status"] == "ok":
            flag = ""
        else:
            flag = f"  [{row['attempt_status']}]"
        print(
            f"[{i}/{len(todo)}] sid={row['scenario_id']:>3} name={row['name']:<15} "
            f"db_count={row['db_count']:>2} class_count={row['class_count']:>2} "
            f"total={row['total_elapsed']:5.2f}s{flag}"
        )

        # Periodic ETA every 50 scenarios
        if i % 50 == 0 and i < len(todo):
            elapsed = time.monotonic() - start
            avg_per = elapsed / i
            eta_min = avg_per * (len(todo) - i) / 60
            print(
                f"   ... {i}/{len(todo)} done, "
                f"elapsed {elapsed/60:.1f} min, "
                f"avg {avg_per:.1f}s/scenario, "
                f"ETA {eta_min:.1f} min\n"
            )

    total = time.monotonic() - start
    print(f"\nDone in {total/60:.1f} min. Output: {OUT_CSV}")


if __name__ == "__main__":
    main()