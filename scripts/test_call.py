from src.markify_client import search

def main():
    print("Smoke test: mark='Apple', classes='9', databases='USPTO'")
    r = search(mark="Apple", classes="9", databases="USPTO")
    print(f"Status: {r.status}; HTTP: {r.http_code}; elapsed: {r.elapsed:.2f}s")
    if r.status == "ok":
        print(f"Total Found: {r.data.get('meta', {}).get('totalFound')}")
        print(f"Returned: {len(r.data.get('result', []))} items")
    else:
        print(f"Error: {r.error}")
    return 0 if r.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
