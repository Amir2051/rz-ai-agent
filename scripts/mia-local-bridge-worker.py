#!/usr/bin/env python3
"""Supervised MIA local bridge worker.

Only executes the exact read-only commands listed below after the hosted MIA UI
has recorded an explicit approval. Never evaluates model-provided shell text.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

BASE_URL = os.getenv("RZ_AI_URL", "https://rz-ai.safenestt.com").rstrip("/")
BRIDGE_TOKEN = os.getenv("MIA_TERMINAL_BRIDGE_TOKEN", "").strip()
POLL_SECONDS = 2
MAX_OUTPUT = 6000
WORKSPACES = {
    "dev-terminal": "/home/ronzoro/dev-terminal",
    "rz-ai-agent": "/home/ronzoro/rz-ai-agent-integrations/.worktrees/terminal-bridge",
}
COMMANDS = {
    "pwd": ["pwd"],
    "python3 --version": ["python3", "--version"],
    "uname -a": ["uname", "-a"],
    "git status --short": ["git", "status", "--short"],
    "git log -1 --oneline": ["git", "log", "-1", "--oneline"],
    "git branch --show-current": ["git", "branch", "--show-current"],
    "ls -la": ["ls", "-la"],
    "power-outage-status": None,
}


def validate_config() -> None:
    parsed = urlparse(BASE_URL)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise SystemExit("RZ_AI_URL must use HTTPS (HTTP is allowed only for localhost testing).")
    if not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit("RZ_AI_URL must be a plain base URL without credentials, query, or fragment.")
    if not BRIDGE_TOKEN:
        raise SystemExit("MIA_TERMINAL_BRIDGE_TOKEN is not set; worker will not start.")


def api(method: str, path: str, payload: dict | None = None, timeout: int = 32) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={
            "X-Bridge-Token": BRIDGE_TOKEN,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "MIA-Supervised-Local-Bridge/1.0",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(12000)
    result = json.loads(raw.decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Bridge returned an invalid response.")
    return result


def run_fixed(argv: list[str], cwd: str | None = None, timeout: int = 8) -> str:
    completed = subprocess.run(
        argv, cwd=cwd, shell=False, capture_output=True, text=True,
        timeout=timeout, check=False,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
    )
    output = (completed.stdout + completed.stderr).strip()
    return output or f"(no output; exit code {completed.returncode})"


def command_output(command: str, workspace: str) -> str:
    if command not in COMMANDS:
        raise ValueError("Command is not on the local read-only allowlist.")
    if workspace not in WORKSPACES:
        raise ValueError("Workspace is not allowlisted.")
    cwd = WORKSPACES[workspace]
    if not os.path.isdir(cwd):
        raise ValueError("The selected workspace is not present on this machine.")
    if command == "power-outage-status":
        return power_outage_status()
    return run_fixed(COMMANDS[command], cwd=cwd)


def power_outage_status() -> str:
    """Collect local host/network/UPS telemetry; never changes device state."""
    report: dict = {
        "scope": "This Kali host only; not the utility grid or remote building systems.",
        "host": platform.node(),
        "platform": platform.platform(),
        "checks": {},
        "limitations": [
            "CCTV and access-control systems are not connected to this local bridge.",
            "No breaker, generator, inverter, or utility control commands are available.",
        ],
    }
    try:
        report["checks"]["uptime"] = run_fixed(["uptime", "-p"], timeout=3)
    except Exception as exc:
        report["checks"]["uptime"] = f"unavailable: {type(exc).__name__}"

    routes = []
    if shutil.which("ip"):
        try:
            raw = run_fixed(["ip", "-j", "route", "show", "default"], timeout=3)
            routes = json.loads(raw) if raw.startswith("[") else []
            report["checks"]["default_routes"] = routes[:4]
        except Exception as exc:
            report["checks"]["default_routes"] = f"unavailable: {type(exc).__name__}"
        try:
            links = json.loads(run_fixed(["ip", "-j", "link", "show"], timeout=3))
            report["checks"]["network_interfaces"] = [
                {"name": item.get("ifname"), "state": item.get("operstate")}
                for item in links[:12]
            ]
        except Exception as exc:
            report["checks"]["network_interfaces"] = f"unavailable: {type(exc).__name__}"
    else:
        report["checks"]["network"] = "ip utility not installed"

    gateway = next((r.get("gateway") for r in routes if r.get("gateway")), None)
    if gateway and shutil.which("ping"):
        try:
            report["checks"]["default_gateway_reachability"] = run_fixed(
                ["ping", "-c", "1", "-W", "2", gateway], timeout=4
            )
        except Exception as exc:
            report["checks"]["default_gateway_reachability"] = f"unavailable: {type(exc).__name__}"
    elif not gateway:
        report["checks"]["default_gateway_reachability"] = "no default gateway detected"

    for service in ("NetworkManager", "systemd-networkd", "nut-monitor"):
        if shutil.which("systemctl"):
            try:
                report["checks"][f"service_{service}"] = run_fixed(
                    ["systemctl", "is-active", service], timeout=3
                )
            except Exception as exc:
                report["checks"][f"service_{service}"] = f"unavailable: {type(exc).__name__}"

    if shutil.which("upsc"):
        try:
            names = run_fixed(["upsc", "-l"], timeout=4)
            ups_names = [line.strip() for line in names.splitlines() if line.strip() and "exit code" not in line][:4]
            ups = []
            allowed_keys = {
                "ups.status", "battery.charge", "battery.runtime",
                "input.voltage", "ups.load", "battery.voltage",
            }
            for name in ups_names:
                try:
                    raw = run_fixed(["upsc", name], timeout=4)
                    values = {}
                    for line in raw.splitlines():
                        if ": " in line:
                            key, value = line.split(": ", 1)
                            if key in allowed_keys:
                                values[key] = value[:100]
                    ups.append({"name": name[:80], "telemetry": values})
                except Exception as exc:
                    ups.append({"name": name[:80], "error": type(exc).__name__})
            report["checks"]["ups"] = ups if ups else "no UPS devices reported by NUT"
        except Exception as exc:
            report["checks"]["ups"] = f"unavailable: {type(exc).__name__}"
    else:
        report["checks"]["ups"] = "NUT upsc utility not installed; UPS telemetry unavailable"

    return json.dumps(report, indent=2, ensure_ascii=False)[:MAX_OUTPUT]


def handle_job(job: dict) -> None:
    job_id = job.get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
        return
    command = job.get("command")
    workspace = job.get("workspace")
    try:
        if not isinstance(command, str) or not isinstance(workspace, str):
            raise ValueError("Invalid job payload.")
        output = command_output(command, workspace)
        result = {"status": "completed", "output": output[:MAX_OUTPUT]}
    except Exception as exc:
        result = {"status": "blocked", "message": f"{type(exc).__name__}: {str(exc)[:350]}"}
    try:
        api("POST", f"/v1/local-terminal/results/{job_id}", result, timeout=10)
        print(f"[{time.strftime('%H:%M:%S')}] job {job_id[:8]}: {result['status']}", flush=True)
    except Exception as exc:
        print(f"[{time.strftime('%H:%M:%S')}] result delivery failed: {type(exc).__name__}", flush=True)


def main() -> int:
    validate_config()
    print("MIA supervised local bridge worker", flush=True)
    print("Read-only allowlist active; each job requires approval in the authenticated MIA UI.", flush=True)
    print("No arbitrary shell commands are accepted. Press Ctrl+C to stop.", flush=True)
    while True:
        try:
            response = api("GET", "/v1/local-terminal/next", timeout=32)
            job = response.get("job")
            if isinstance(job, dict):
                handle_job(job)
        except KeyboardInterrupt:
            print("\nWorker stopped.", flush=True)
            return 0
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            print(f"[{time.strftime('%H:%M:%S')}] bridge unavailable: {type(exc).__name__}", flush=True)
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] worker error: {type(exc).__name__}", flush=True)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
