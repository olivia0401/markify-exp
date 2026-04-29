import time

import pandas as pd

from src.config import INPUT_XLSX, OUTPUTS_DIR
from src.markify_client import QuotaExhaustedError, search_all

OUT_CSV = OUTPUTS_DIR / "exp1_results.csv"
MAX_PAGES_PER_DB = 3


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


def call_one_db(name: str, classes: str, db: str) -> tuple[list[dict], dict]:
    r = search_all(
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


def main():
    df = pd.read_excel(INPUT_XLSX, sheet_name="Distance_Score_Tests")
    print(f"Loaded {len(df)} names.")

    done = set()
    if OUT_CSV.exists():
        done = set(pd.read_csv(OUT_CSV, encoding="latin-1")["input_name"].unique())
        print(f"Resuming: {len(done)} names already in CSV.")

    start = time.monotonic()
    write_header = not OUT_CSV.exists()

    for i, row in df.iterrows():
        name = row["Name"]
        if name in done:
            continue

        classes = normalize_value(row["Nice Classes"])
        databases = parse_databases(normalize_value(row["Databases"]))

        all_rows = []
        summaries = []
        try:
            for db in databases:
                sub_rows, summary = call_one_db(name, classes, db)
                all_rows.extend(sub_rows)
                summaries.append((db, summary))
        except QuotaExhaustedError as e:
            print(f"\nQuota exhausted at name {i+1}/{len(df)}: {e}")
            return

        n_ok = sum(1 for _, s in summaries if s["status"] in ("ok", "ok_truncated"))
        n_results = sum(s["n_results"] for _, s in summaries)
        n_pages = sum(s["pages"] for _, s in summaries)
        elapsed_total = sum(s["elapsed"] for _, s in summaries)
        print(
            f"[{i+1}/{len(df)}] {name} ({len(databases)} DBs, {n_pages} pages): "
            f"{n_ok}/{len(databases)} ok, {n_results} results, {elapsed_total:.1f}s"
        )

        safe_to_csv(pd.DataFrame(all_rows), OUT_CSV, mode="a", header=write_header)
        write_header = False

    total = time.monotonic() - start
    print(f"\nDone in {total/60:.1f} min. Output: {OUT_CSV}")


if __name__ == "__main__":
    main()
