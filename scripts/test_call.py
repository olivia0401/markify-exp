"""Smoke test: verify markify_client works with one real API call.

Run: python -m scripts.test_call
"""
from src.markify_client import search

print("=" * 60)
print("Markify Smoke Test")
print("=" * 60)
print("Calling: mark='Apple', classes='9', databases='USPTO'")
print()

r = search(mark="Apple", classes="9", databases="USPTO")

print(f"Status:    {r.status}")
print(f"HTTP Code: {r.http_code}")
print(f"Elapsed:   {r.elapsed:.2f} seconds")
print()

if r.status == "ok":
    meta = r.data.get("meta", {})
    results = r.data.get("result", [])
    print(f"Total Found:   {meta.get('totalFound')}")
    print(f"Returned:      {len(results)} items")

    if results:
        first = results[0]
        print()
        print("First result:")
        print(f"  Mark:           {first.get('mark')}")
        print(f"  DistanceScore:  {first.get('distanceScore')}")
        print(f"  Market:         {first.get('market')}")
        print(f"  Status:         {first.get('currentTrademarkStatus')}")
    print()
    print("Smoke test passed!")
else:
    print(f"Error: {r.error}")