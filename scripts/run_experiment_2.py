import argparse
import json
import random
import time
from datetime import datetime, timezone

import pandas as pd

from src.config import HTTP_TIMEOUT, INPUT_XLSX, OUTPUTS_DIR
from src.markify_client import (
    CallCapReachedError,
    QuotaExhaustedError,
    get_call_count,
    search,
    set_call_cap,
)

OUT_CSV = OUTPUTS_DIR / "exp2_full_results.csv"
SHUFFLE_SEED = 42


def safe_to_csv(df, path, *, mode: str, header: bool, max_retries: int = 6):
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


def normalize_value(v) -> str:
    if isinstance(v, (int, float)):
        return str(int(v))
    return str(v).strip()


def parse_databases(s: str) -> list[str]:
    return [d.strip() for d in s.split(",") if d.strip()]


def call_scenario(sc: dict) -> dict:
    name = normalize_value(sc["Name"])
    classes = normalize_value(sc["Nice_Classes"])
    db_codes_raw = normalize_value(sc["Database_Codes"])
    dbs = parse_databases(db_codes_raw)

    r1 = search(
        mark=name,
        classes=classes,
        databases=db_codes_raw,
        mode="knockout",
        min_score=0.0,
        page_limit=100,
    )

    splittable = r1.status in ("timeout", "http_error") and len(dbs) > 1

    row = {
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
        "original_status": r1.status,
        "original_elapsed": round(r1.elapsed, 3),
        "original_http_code": r1.http_code,
        "original_error": (r1.error or "")[:200] if r1.error else None,
        "original_n_results": (
            len((r1.data or {}).get("result", [])) if r1.status == "ok" else 0
        ),
        "was_split": False,
        "n_sub_calls": 0,
        "total_elapsed": round(r1.elapsed, 3),
        "attempt_status": r1.status,
        "sub_details_json": None,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if not splittable:
        return row

    sub_results = []
    sub_total_elapsed = 0.0
    n_ok = 0
    for db in dbs:
        r_sub = search(
            mark=name,
            classes=classes,
            databases=db,
            mode="knockout",
            min_score=0.0,
            page_limit=100,
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
                len((r_sub.data or {}).get("result", []))
                if r_sub.status == "ok"
                else 0
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
        "total_elapsed": round(sub_total_elapsed, 3),
        "attempt_status": final_status,
        "sub_details_json": json.dumps(sub_results),
    })
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=1900,
                    help="Hard cap on API calls in this run (default 1900).")
    ap.add_argument("--max-scenarios", type=int, default=None,
                    help="Process at most N scenarios (for lightweight testing).")
    args = ap.parse_args()

    set_call_cap(args.max_calls)

    df = pd.read_excel(INPUT_XLSX, sheet_name="Speed_Tests")
    print(f"Loaded {len(df)} scenarios. Cap: {args.max_calls} calls. Timeout: {HTTP_TIMEOUT}s.")

    done_pairs = set()
    if OUT_CSV.exists():
        prev = pd.read_csv(OUT_CSV)
        done_pairs = set(zip(
            prev["scenario_id"].astype(int),
            prev["name"].astype(str).str.strip(),
        ))
        print(f"Resuming: {len(done_pairs)} pairs already done.")

    scenarios = df.to_dict("records")
    random.Random(SHUFFLE_SEED).shuffle(scenarios)
    todo = [
        sc for sc in scenarios
        if (int(sc["Scenario_ID"]), str(sc["Name"]).strip()) not in done_pairs
    ]
    if args.max_scenarios is not None:
        todo = todo[: args.max_scenarios]
        print(f"--max-scenarios: capping to first {len(todo)} scenarios.")
    print(f"Will process {len(todo)} scenarios.\n")

    start = time.monotonic()
    write_header = not OUT_CSV.exists()

    for i, sc in enumerate(todo, 1):
        try:
            row = call_scenario(sc)
        except (QuotaExhaustedError, CallCapReachedError) as e:
            print(f"\nStopped at {i}/{len(todo)}: {e}")
            print(f"Calls used: {get_call_count()}. Resume tomorrow with same command.")
            return
        safe_to_csv(pd.DataFrame([row]), OUT_CSV, mode="a", header=write_header)
        write_header = False

        if row["was_split"]:
            flag = f"  [SPLIT x{row['n_sub_calls']}]"
        elif row["attempt_status"] == "ok":
            flag = ""
        else:
            flag = f"  [{row['attempt_status']}]"
        print(
            f"[{i}/{len(todo)}] sid={row['scenario_id']:>3} name={row['name']:<15} "
            f"db={row['db_count']:>2} cls={row['class_count']:>2} "
            f"t={row['total_elapsed']:5.2f}s{flag} [calls: {get_call_count()}/{args.max_calls}]"
        )

        if i % 50 == 0 and i < len(todo):
            elapsed = time.monotonic() - start
            avg = elapsed / i
            eta = avg * (len(todo) - i) / 60
            print(
                f"   {i}/{len(todo)} done, {elapsed/60:.1f} min, "
                f"avg {avg:.1f}s, ETA {eta:.1f} min\n"
            )

    total = time.monotonic() - start
    print(f"\nDone in {total/60:.1f} min. Calls used: {get_call_count()}. Output: {OUT_CSV}")


if __name__ == "__main__":
    main()
