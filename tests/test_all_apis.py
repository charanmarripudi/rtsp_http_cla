#!/usr/bin/env python3
"""
Test all primary API endpoints against the live backend server.
Tests both local LAN IP and public Tailscale Funnel domain.
"""
import sys
import json
import urllib.request
import urllib.error

def test_endpoint(base_url, method, path, data=None):
    url = f"{base_url}{path}"
    req = urllib.request.Request(url, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    
    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            status = res.status
            body = res.read().decode("utf-8", errors="ignore")
            try:
                parsed = json.loads(body)
                summary = f"JSON ({type(parsed).__name__}, len={len(parsed) if hasattr(parsed, '__len__') else 'N/A'})"
            except Exception:
                summary = f"Text ({len(body)} chars)"
            return status, True, summary
    except urllib.error.HTTPError as e:
        return e.code, False, f"HTTP Error {e.code}: {e.reason}"
    except Exception as e:
        return None, False, f"Connection Failed: {e}"

def run_suite(base_url):
    print(f"\n==================================================")
    print(f" TESTING APIs on: {base_url}")
    print(f"==================================================")

    endpoints = [
        ("GET", "/index.html", None),
        ("GET", "/api/models", None),
        ("GET", "/api/model-classes", None),
        ("GET", "/api/locations", None),
        ("GET", "/api/streams", None),
        ("GET", "/api/camera-models", None),
        ("GET", "/api/devices", None),
        ("GET", "/api/status", None),
        ("GET", "/api/detection/status", None),
        ("GET", "/api/cameras/with-models", None),
        ("GET", "/api/alerts", None),
        ("GET", "/api/speed/config", None),
        ("GET", "/api/crowd/status", None),
        ("GET", "/api/ptz/cameras", None),
        ("POST", "/api/analytics/by-location", {"location": "Location 20"}),
        ("POST", "/api/analytics/by-usecase", {"usecase": ["hansung_ppe_violations"]}),
    ]

    all_passed = True
    for method, path, data in endpoints:
        code, ok, info = test_endpoint(base_url, method, path, data)
        status_sym = "[PASS]" if ok and code in (200, 201) else "[FAIL]"
        print(f" {status_sym} {method:4s} {path:30s} -> {code} ({info})")
        if not ok or code not in (200, 201):
            all_passed = False

    return all_passed

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "https://raspberrypi.tail43e835.ts.net"
    success = run_suite(target)
    sys.exit(0 if success else 1)
