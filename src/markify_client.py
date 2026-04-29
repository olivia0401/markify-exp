import json
import random
import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import (
    HTTP_TIMEOUT,
    MARKIFY_API_KEY,
    MARKIFY_BASE_URL,
    PROJECT_ROOT,
    RATE_LIMIT_SECONDS,
)


_last_call_time: float = 0.0
_quota_exhausted: bool = False
_call_count: int = 0
_call_cap: Optional[int] = None

_session = requests.Session()


def set_call_cap(n: Optional[int]) -> None:
    global _call_cap, _call_count
    _call_cap = n
    _call_count = 0


def get_call_count() -> int:
    return _call_count


def get_call_cap() -> Optional[int]:
    return _call_cap

_BAD_CODES_FILE = PROJECT_ROOT / "data" / "known_bad_db_codes.json"


def _load_known_bad() -> set:
    if _BAD_CODES_FILE.exists():
        try:
            return set(json.loads(_BAD_CODES_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


_known_bad_db_codes: set = _load_known_bad()


def _save_known_bad() -> None:
    _BAD_CODES_FILE.parent.mkdir(exist_ok=True)
    _BAD_CODES_FILE.write_text(
        json.dumps(sorted(_known_bad_db_codes), indent=2),
        encoding="utf-8",
    )


def get_known_bad_db_codes() -> set:
    return set(_known_bad_db_codes)


def classify_error(error_text: Optional[str]) -> str:
    if not error_text:
        return "unknown"
    s = str(error_text)
    if "Quota" in s and "exceeded" in s:
        return "quota_exceeded"
    if "disallowed parameter" in s and "database" in s:
        return "bad_db_code"
    return "other"


class QuotaExhaustedError(RuntimeError):
    pass


class CallCapReachedError(RuntimeError):
    pass


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

PAGE_START_PARAM = "startIndex"
PAGE_START_BASE = 0

DB_CODE_ALIASES = {
    "EU": "EUIPO",
    "US": "USPTO",
}


def _translate_aliases(databases: str) -> str:
    parts = [p.strip() for p in databases.split(",") if p.strip()]
    return ",".join(DB_CODE_ALIASES.get(p, p) for p in parts)


@dataclass
class Result:
    status: str
    elapsed: float
    data: Optional[dict] = None
    error: Optional[str] = None
    http_code: Optional[int] = None
    pages_fetched: int = 1


def _wait_for_rate_limit() -> None:
    global _last_call_time
    now = time.monotonic()
    elapsed_since_last_call = now - _last_call_time
    if elapsed_since_last_call < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed_since_last_call)
    _last_call_time = time.monotonic()


def search(
    mark: str,
    classes: str,
    databases: str,
    mode: str = "knockout",
    min_score: float = 0.0,
    page_limit: int = 100,
    page_start: int = PAGE_START_BASE,
    http_timeout: Optional[float] = None,
    gas: int = 1,
) -> Result:
    global _call_count, _quota_exhausted
    if _quota_exhausted:
        raise QuotaExhaustedError(
            "Markify quota exhausted. Wait for reset or use a fresh API key."
        )
    if _call_cap is not None and _call_count >= _call_cap:
        raise CallCapReachedError(
            f"Reached --max-calls cap ({_call_cap}). Resume tomorrow."
        )

    timeout = http_timeout if http_timeout is not None else HTTP_TIMEOUT
    _wait_for_rate_limit()
    _call_count += 1

    databases = _translate_aliases(databases)

    params = {
        "api_key": MARKIFY_API_KEY,
        "mark": mark,
        "class": classes,
        "database": databases,
        "mode": mode,
        "gas": gas,
        "minScore": min_score,
        "pageLimit": page_limit,
        PAGE_START_PARAM: page_start,
        "_cb": random.randint(1, 10**9),
    }

    url = f"{MARKIFY_BASE_URL}/search.json"
    start = time.monotonic()

    try:
        response = _session.get(
            url, params=params, headers=_NO_CACHE_HEADERS, timeout=timeout
        )
    except requests.exceptions.Timeout:
        return Result(
            status="timeout",
            elapsed=time.monotonic() - start,
            error=f"Timeout after {timeout}s",
        )
    except requests.exceptions.RequestException as e:
        return Result(
            status="network_error",
            elapsed=time.monotonic() - start,
            error=str(e),
        )

    elapsed = time.monotonic() - start

    if response.status_code != 200:
        err_kind = classify_error(response.text)
        if err_kind == "quota_exceeded":
            _quota_exhausted = True
        elif err_kind == "bad_db_code" and "," not in databases:
            db_key = databases.strip()
            if db_key and db_key not in _known_bad_db_codes:
                _known_bad_db_codes.add(db_key)
                _save_known_bad()
        return Result(
            status="http_error",
            elapsed=elapsed,
            error=response.text[:200],
            http_code=response.status_code,
        )

    try:
        data = response.json()
    except ValueError as e:
        return Result(
            status="parse_error",
            elapsed=elapsed,
            error=f"Invalid JSON: {e}",
            http_code=200,
        )

    return Result(status="ok", elapsed=elapsed, data=data, http_code=200)


def search_all(
    mark: str,
    classes: str,
    databases: str,
    mode: str = "knockout",
    min_score: float = 0.0,
    page_limit: int = 100,
    max_pages: int = 10,
    http_timeout: Optional[float] = None,
    gas: int = 1,
) -> Result:
    all_items: list = []
    total_elapsed = 0.0
    last_data: Optional[dict] = None

    for page_idx in range(max_pages):
        page_start = PAGE_START_BASE + page_idx * page_limit
        r = search(
            mark=mark,
            classes=classes,
            databases=databases,
            mode=mode,
            min_score=min_score,
            page_limit=page_limit,
            page_start=page_start,
            http_timeout=http_timeout,
            gas=gas,
        )
        total_elapsed += r.elapsed

        if r.status != "ok":
            r.elapsed = total_elapsed
            r.pages_fetched = page_idx + 1
            if all_items:
                r.data = {"result": all_items, **(last_data or {})}
            return r

        items = (r.data or {}).get("result", []) or []
        last_data = r.data
        all_items.extend(items)

        if len(items) < page_limit:
            return Result(
                status="ok",
                elapsed=total_elapsed,
                data={**r.data, "result": all_items},
                http_code=r.http_code,
                pages_fetched=page_idx + 1,
            )

    return Result(
        status="ok_truncated",
        elapsed=total_elapsed,
        data={**(last_data or {}), "result": all_items},
        http_code=200,
        error=f"Hit max_pages={max_pages}",
        pages_fetched=max_pages,
    )
