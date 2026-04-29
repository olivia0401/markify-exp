"""Verify Markify pagination using 'startIndex' (discovered from meta field)."""

import hashlib
import json
import pprint
import random

from src.config import MARKIFY_API_KEY, MARKIFY_BASE_URL, HTTP_TIMEOUT
from src.markify_client import _wait_for_rate_limit, _session, _NO_CACHE_HEADERS


def fingerprint(item: dict) -> str:
    return hashlib.md5(json.dumps(item, sort_keys=True).encode()).hexdigest()[:8]


def raw_call(extra_params: dict) -> dict:
    _wait_for_rate_limit()
    params = {
        "api_key": MARKIFY_API_KEY,
        "mark": "INTEL",
        "class": "9",
        "database": "USPTO",
        "mode": "knockout",
        "minScore": 0.0,
        "pageLimit": 5,
        "_cb": random.randint(1, 10**9),
        **extra_params,
    }
    r = _session.get(f"{MARKIFY_BASE_URL}/search.json",
                     params=params, headers=_NO_CACHE_HEADERS, timeout=HTTP_TIMEOUT)
    return r.json() if r.status_code == 200 else {"_err": r.status_code}


def main():
    print("Verifying startIndex (per meta field 'startIndex')\n")

    base = raw_call({})
    base_fps = [fingerprint(x) for x in base.get("result", [])]
    print(f"Page 1 (no startIndex): meta={base.get('meta')}")
    print(f"  fingerprints: {base_fps}\n")

    for value in [0, 1, 5, 6, 10]:
        result = raw_call({"startIndex": value})
        items = result.get("result", [])
        meta = result.get("meta", {})
        fps = [fingerprint(x) for x in items]
        overlap = len(set(fps) & set(base_fps))
        if overlap == 0:
            verdict = "✅ DISJOINT"
        elif overlap < len(fps):
            verdict = f"⚠️ partial ({overlap}/{len(fps)})"
        else:
            verdict = "❌ same as baseline"
        print(f"  startIndex={value:<3} → meta.startIndex='{meta.get('startIndex')}', "
              f"{len(items)} items, overlap={overlap}/{len(fps)}  {verdict}")


if __name__ == "__main__":
    main()