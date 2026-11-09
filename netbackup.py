#!/usr/bin/env python3
"""netbackup: back up network device configs in parallel, with git version history.

Pulls the running config from Cisco / MikroTik / Juniper / Arista / Huawei / FortiGate
over SSH (Netmiko), strips noise lines (timestamps, uptime), stores one file per device
and commits changes to a git repository so every change has a diff and a date.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# command that prints the full configuration, per Netmiko device_type
COMMANDS = {
    "cisco_ios": "show running-config",
    "cisco_xe": "show running-config",
    "cisco_nxos": "show running-config",
    "cisco_asa": "show running-config",
    "arista_eos": "show running-config",
    "juniper_junos": "show configuration | display set",
    "mikrotik_routeros": "/export terse",
    "huawei": "display current-configuration",
    "fortinet": "show",
    "hp_procurve": "show running-config",
}

# lines that change on every run and would create fake diffs
NOISE = [
    r"^! Last configuration change at .*",
    r"^! NVRAM config last updated at .*",
    r"^ntp clock-period .*",
    r"^Building configuration\.\.\.",
    r"^Current configuration : \d+ bytes",
    r"^# \w{3}/\d{2}/\d{4} \d{2}:\d{2}:\d{2} by RouterOS .*",   # RouterOS v6 export header
    r"^# \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} by RouterOS .*",   # RouterOS v7 export header
    r"^## Last commit: .*",                                    # Junos
    r"^Time: .*",
]
NOISE_RE = re.compile("|".join(f"(?:{p})" for p in NOISE))


def clean(config: str) -> str:
    """Remove volatile lines and normalise whitespace so diffs show real changes only."""
    lines = [line.rstrip() for line in config.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if not NOISE_RE.match(line)]
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class Device:
    name: str
    host: str
    device_type: str
    port: int = 22
    username: str = ""
    password: str = ""
    secret: str = ""
    command: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Result:
    device: Device
    ok: bool
    status: str           # new / changed / unchanged / failed
    message: str = ""
    added: int = 0
    removed: int = 0
    seconds: float = 0.0


def load_inventory(path: Path) -> list[Device]:
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML is missing: pip install -r requirements.txt")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults", {})
    devices = []
    for raw in data.get("devices", []):
        item = {**defaults, **raw}
        # credentials never live in the file: they are read from env variables
        user_env = item.pop("username_env", "NET_USERNAME")
        pass_env = item.pop("password_env", "NET_PASSWORD")
        secret_env = item.pop("secret_env", "NET_SECRET")
        known = {"name", "host", "device_type", "port", "command"}
        devices.append(Device(
            name=item.get("name") or item["host"],
            host=item["host"],
            device_type=item.get("device_type", "cisco_ios"),
            port=int(item.get("port", 22)),
            username=os.environ.get(user_env, ""),
            password=os.environ.get(pass_env, ""),
            secret=os.environ.get(secret_env, ""),
            command=item.get("command", ""),
            extra={k: v for k, v in item.items() if k not in known},
        ))
    return devices


def fetch(device: Device, timeout: int) -> str:
    from netmiko import ConnectHandler  # imported lazily so tests run without it

    params = dict(device_type=device.device_type, host=device.host, port=device.port,
                  username=device.username, password=device.password, secret=device.secret,
                  conn_timeout=timeout, fast_cli=False, **device.extra)
    command = device.command or COMMANDS.get(device.device_type, "show running-config")
    with ConnectHandler(**params) as conn:
        if device.secret and device.device_type.startswith("cisco"):
            conn.enable()
        return conn.send_command(command, read_timeout=max(60, timeout * 3))


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def backup_one(device: Device, out: Path, timeout: int, show_diff: bool) -> Result:
    start = time.time()
    try:
        config = clean(fetch(device, timeout))
    except Exception as exc:  # noqa: BLE001 - report any connection/auth error per device
        return Result(device, False, "failed", f"{type(exc).__name__}: {exc}".splitlines()[0],
                      seconds=time.time() - start)
    if len(config.strip()) < 20:
        return Result(device, False, "failed", "empty output (privilege level too low?)", seconds=time.time() - start)

    path = out / f"{safe_name(device.name)}.cfg"
    old = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(config, encoding="utf-8")
    if old is None:
        return Result(device, True, "new", f"{len(config.splitlines())} lines", seconds=time.time() - start)
    if old == config:
        return Result(device, True, "unchanged", seconds=time.time() - start)

    diff = list(difflib.unified_diff(old.splitlines(), config.splitlines(), "before", "after", lineterm=""))
    added = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
    removed = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
    if show_diff:
        print(f"\n===== {device.name} =====\n" + "\n".join(diff))
    return Result(device, True, "changed", added=added, removed=removed, seconds=time.time() - start)


def git(out: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(out), *args], capture_output=True, text=True)


def commit(out: Path, results: list[Result]) -> str | None:
    if not (out / ".git").exists():
        git(out, "init", "-q")
    git(out, "add", "-A")
    if not git(out, "status", "--porcelain").stdout.strip():
        return None
    changed = [r.device.name for r in results if r.status in ("new", "changed")]
    msg = f"backup {time.strftime('%Y-%m-%d %H:%M')}: {', '.join(changed) or 'update'}"
    res = git(out, "-c", "user.name=netbackup", "-c", "user.email=netbackup@localhost", "commit", "-q", "-m", msg)
    return msg if res.returncode == 0 else None


def notify_telegram(text: str):
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=10)
    except OSError as exc:
        print(f"telegram notification failed: {exc}", file=sys.stderr)


ICONS = {"new": "🆕", "changed": "✏️ ", "unchanged": "✅", "failed": "❌"}


def summary(results: list[Result]) -> str:
    width = max([len(r.device.name) for r in results] + [6])
    lines = []
    for r in sorted(results, key=lambda r: (r.status != "failed", r.device.name)):
        detail = r.message or (f"+{r.added} / -{r.removed} lines" if r.status == "changed" else "")
        lines.append(f"{ICONS[r.status]} {r.device.name:<{width}}  {r.status:<9} {r.seconds:5.1f}s  {detail}")
    counts = {s: sum(r.status == s for r in results) for s in ICONS}
    lines.append(f"\n{len(results)} devices: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Back up network device configs with git history")
    p.add_argument("-i", "--inventory", default="inventory.yaml", type=Path)
    p.add_argument("-o", "--output", default="backups", type=Path)
    p.add_argument("-w", "--workers", type=int, default=10, help="parallel SSH sessions")
    p.add_argument("-t", "--timeout", type=int, default=15)
    p.add_argument("-l", "--limit", help="only devices whose name contains this text")
    p.add_argument("--diff", action="store_true", help="print the diff of changed devices")
    p.add_argument("--no-git", action="store_true", help="do not commit to git")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        import netmiko  # noqa: F401
    except ImportError:
        print("Netmiko is missing: pip install -r requirements.txt", file=sys.stderr)
        return 2

    devices = load_inventory(args.inventory)
    if args.limit:
        devices = [d for d in devices if args.limit.lower() in d.name.lower()]
    if not devices:
        print("No devices in inventory")
        return 1
    missing = [d.name for d in devices if not (d.username and d.password)]
    if missing:
        print(f"⚠️  No credentials for: {', '.join(missing)} (set NET_USERNAME / NET_PASSWORD)", file=sys.stderr)

    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(backup_one, d, args.output, args.timeout, args.diff) for d in devices]
        for fut in as_completed(futures):
            results.append(fut.result())

    committed = None if args.no_git else commit(args.output, results)
    if args.json:
        print(json.dumps([{"device": r.device.name, "host": r.device.host, "status": r.status,
                           "message": r.message, "added": r.added, "removed": r.removed} for r in results], indent=2))
    else:
        print(summary(results))
        if committed:
            print(f"📝 git: {committed}")

    text = summary(results)
    if any(r.status in ("changed", "failed") for r in results):
        notify_telegram("🔧 Network backup\n" + text)
    return 1 if any(not r.ok for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
