from src.markify_client import search

print("Smoke test: mark='Apple', classes='9', databases='USPTO'")
r = search(mark="Apple", classes="9", databases="USPTO")

print(f"Status:    {r.status}")
print(f"HTTP Code: {r.http_code}")
print(f"Elapsed:   {r.elapsed:.2f}s")

if r.status == "ok":
    meta = r.data.get("meta", {})
    results = r.data.get("result", [])
    print(f"Total Found: {meta.get('totalFound')}")
    print(f"Returned:    {len(results)} items")
    if results:
        first = results[0]
        print(f"First mark:  {first.get('mark')} (score={first.get('distanceScore')})")
else:
    print(f"Error: {r.error}")
