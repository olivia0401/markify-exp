import argparse
import time

import pandas as pd

from src.config import (
    HTTP_TIMEOUT,
    INPUT_XLSX,
    KNOWN_BAD_CODES_FILE,
    MARKIFY_API_KEY,
    OUTPUTS_DIR,
    RATE_LIMIT_SECONDS,
)
from src.markify_client import (
    CallCapReachedError,
    MarkifyClient,
    QuotaExhaustedError,
)
from src.utils import normalize_value, parse_databases, safe_to_csv

OUT_CSV = OUTPUTS_DIR / "exp1_results.csv"
MAX_PAGES_PER_DB = 100
DONE_STATUSES = {"ok", "ok_empty"}


def call_one_db(
    client: MarkifyClient, name: str, classes: str, db: str
) -> tuple[list[dict], dict]:
    r = client.search_all(
        mark=name,
        classes=classes,
        databases=db,
        mode="knockout",
        min_score=0.0,
        page_limit=100,
        max_pages=MAX_PAGES_PER_DB,
    )

    base = {
        "input_name": name,
        "db_called": db,
        "call_elapsed": round(r.elapsed, 3),
        "http_code": r.http_code,
        "pages_fetched": r.pages_fetched,
    }

    if r.status in ("ok", "ok_truncated"):
        items = (r.data or {}).get("result", [])
        if items:
            rows = [
                {
                    **base,
                    "related_trademark": item.get("mark"),
                    "score": item.get("distanceScore"),
                    "market": item.get("market"),
                    "trademark_status": item.get("currentTrademarkStatus"),
                    "call_status": r.status,
                    "error": r.error,
                }
                for item in items
            ]
        else:
            rows = [
                {
                    **base,
                    "related_trademark": None,
                    "score": None,
                    "market": None,
                    "trademark_status": None,
                    "call_status": "ok_empty",
                    "error": None,
                }
            ]
        n_results = len(items)
    else:
        rows = [
            {
                **base,
                "related_trademark": None,
                "score": None,
                "market": None,
                "trademark_status": None,
                "call_status": r.status,
                "error": (r.error or "")[:200],
            }
        ]
        n_results = 0

    return rows, {
        "n_results": n_results,
        "status": r.status,
        "elapsed": r.elapsed,
        "pages": r.pages_fetched,
    }


def load_existing_state():
    if not OUT_CSV.exists():
        return set(), set(), None
    existing = pd.read_csv(OUT_CSV, encoding="latin-1")
    done = set()
    redo = set()
    pair_status = existing.groupby(["input_name", "db_called"])["call_status"].first()
    for (name, db), status in pair_status.items():
        if isinstance(status, str) and status in DONE_STATUSES:
            done.add((name, db))
        else:
            redo.add((name, db))
    return done, redo, existing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=1900)
    ap.add_argument("--max-names", type=int, default=None)
    args = ap.parse_args()

    client = MarkifyClient(
        api_key=MARKIFY_API_KEY,
        rate_limit_seconds=RATE_LIMIT_SECONDS,
        http_timeout=HTTP_TIMEOUT,
        call_cap=args.max_calls,
        bad_codes_file=KNOWN_BAD_CODES_FILE,
    )

    df = pd.read_excel(INPUT_XLSX, sheet_name="Distance_Score_Tests")
    print(f"Loaded {len(df)} names. Cap: {args.max_calls} calls.")

    done_pairs, redo_pairs, existing = load_existing_state()
    if existing is not None:
        print(f"Existing CSV: {len(done_pairs)} pairs done, {len(redo_pairs)} need redo")
        if redo_pairs:
            existing["_pair"] = list(zip(existing["input_name"], existing["db_called"]))
            cleaned = existing[~existing["_pair"].isin(redo_pairs)].drop(columns="_pair")
            cleaned.to_csv(OUT_CSV, index=False)
            print(f"Removed {len(existing) - len(cleaned)} old rows for redo pairs")

    todo = []
    for _, row in df.iterrows():
        name = row["Name"]
        classes = normalize_value(row["Nice Classes"])
        databases = parse_databases(normalize_value(row["Databases"]))
        for db in databases:
            if (name, db) in done_pairs:
                continue
            todo.append((name, classes, db))

    if args.max_names is not None:
        names_in_todo = []
        for n, _, _ in todo:
            if n not in names_in_todo:
                names_in_todo.append(n)
        keep = set(names_in_todo[: args.max_names])
        todo = [t for t in todo if t[0] in keep]
        print(f"--max-names: capping to {len(keep)} names ({len(todo)} pairs)")

    print(f"Will process {len(todo)} (name, db) pairs.\n")

    write_header = not OUT_CSV.exists() or OUT_CSV.stat().st_size == 0
    start = time.monotonic()

    for i, (name, classes, db) in enumerate(todo, 1):
        try:
            sub_rows, summary = call_one_db(client, name, classes, db)
        except (QuotaExhaustedError, CallCapReachedError) as e:
            print(f"\nStopped at {i}/{len(todo)}: {e}")
            print(f"Calls used: {client.call_count}. Resume tomorrow with same command.")
            return

        safe_to_csv(pd.DataFrame(sub_rows), OUT_CSV, mode="a", header=write_header)
        write_header = False

        print(
            f"[{i}/{len(todo)}] {name} x {db}: "
            f"{summary['n_results']} results, {summary['pages']} pages, "
            f"{summary['elapsed']:.1f}s "
            f"[calls: {client.call_count}/{args.max_calls}]"
        )

    total = time.monotonic() - start
    print(f"\nDone in {total/60:.1f} min. Calls used: {client.call_count}.")


if __name__ == "__main__":
    main()
