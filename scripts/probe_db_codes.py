"""Verify whether EUIPO / USPTO codes work, and measure their latency.

Background: Sheet 2 used the codes `EU` and `US`, both of which return
HTTP 401 ("disallowed parameter values"). Exp 2 findings hypothesised
that the long-form codes `EUIPO` and `USPTO` would work — this script
tests that hypothesis directly so we know whether the 'single-call subset'
extrapolation column is reasonable.

Run: python -m scripts.probe_db_codes
"""

import time

from src.markify_client import search

# Test mark/class — chosen to be common enough to return real results but
# not so common that any single DB will time out.
TEST_MARK = "Apple"
TEST_CLASS = "9"

# Codes to test:
#   - Known-good (baselines): UK, DE, FR
#   - Suspect long-forms:     EUIPO, USPTO
#   - Confirmed-failing (sanity check): EU, US
PROBES = [
    ("UK", "baseline"),
    ("DE", "baseline"),
    ("FR", "baseline"),
    ("EUIPO", "untested long-form"),
    ("USPTO", "untested long-form"),
    ("EU", "known-failing (sanity)"),
    ("US", "known-failing (sanity)"),
]


def main():
    print(f"Probing DB codes with mark='{TEST_MARK}', class='{TEST_CLASS}'\n")
    print(f"{'Code':<8} {'Category':<25} {'Status':<14} {'HTTP':<6} "
          f"{'Elapsed':<10} {'n_results':<10} {'Note'}")
    print("-" * 100)

    results = []
    for code, category in PROBES:
        r = search(
            mark=TEST_MARK, classes=TEST_CLASS, databases=code,
            min_score=0.0, http_timeout=30,
        )
        n = len((r.data or {}).get("result", [])) if r.status == "ok" else 0
        note = ""
        if r.status == "ok" and n > 0:
            note = "✓ works, has data"
        elif r.status == "ok":
            note = "✓ works but empty"
        elif r.http_code == 401:
            note = "✗ rejected (401)"
        else:
            note = f"✗ {r.error[:40] if r.error else r.status}"

        print(f"{code:<8} {category:<25} {r.status:<14} {str(r.http_code):<6} "
              f"{r.elapsed:>6.2f}s   {n:<10} {note}")
        results.append((code, category, r.status, r.http_code, r.elapsed, n))
        time.sleep(0.1)  # tiny extra cushion on top of the 2s rate limit

    print()
    working_long = [r for r in results
                    if r[0] in ("EUIPO", "USPTO") and r[2] == "ok"]
    if len(working_long) == 2:
        print("✓ Both EUIPO and USPTO work — the 'single-call subset' "
              "extrapolation in Exp 2 is now backed by real data.")
        avg = sum(r[4] for r in working_long) / 2
        print(f"   Mean elapsed for these two: {avg:.2f}s "
              "(compare with §4 of exp2_findings.md)")
    elif len(working_long) == 1:
        ok_code = working_long[0][0]
        bad_code = "USPTO" if ok_code == "EUIPO" else "EUIPO"
        print(f"⚠ Only {ok_code} works; {bad_code} still fails. "
              "Update the 'single-call subset' caveat accordingly.")
    else:
        print("✗ Neither EUIPO nor USPTO works at this endpoint either. "
              "Drop the optimistic extrapolation column entirely or contact "
              "Markify support for the correct code names.")


if __name__ == "__main__":
    main()
