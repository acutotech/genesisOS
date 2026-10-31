import asyncio
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone

from . import DEFAULT_SERVER
from .config import config_file, delete_config, load_config, save_config
from .http import request

USAGE = f"""Acu Connect client

Usage:
  acu-connect signin [--server URL] [--name NAME] [--force]   link this device to your account
  acu-connect run                                             start the client (keeps running)
  acu-connect status                                          show link and server status
  acu-connect signout                                         unlink this device

Environment:
  ACU_SERVER       server URL (default {DEFAULT_SERVER})
  ACU_CONFIG_DIR   where the device token is stored (default ~/.config/acu-connect)
  ACU_NETWORK_LOCATION=0   don't look up this device's approximate location (ipinfo.io)

© 2026 Acutro Technologies Pvt Ltd. All rights reserved. https://acutrotech.com
"""


def parse_args(argv):
    args = {"_": []}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            following = argv[i + 1] if i + 1 < len(argv) else None
            if following is not None and not following.startswith("--"):
                args[arg[2:]] = following
                i += 1
            else:
                args[arg[2:]] = True
        else:
            args["_"].append(arg)
        i += 1
    return args


def server_url(args, config):
    chosen = args.get("server") or os.environ.get("ACU_SERVER") or (config or {}).get("server") or DEFAULT_SERVER
    return str(chosen).rstrip("/")


def out(text=""):
    print(text, flush=True)


def platform_name():
    arch = {"aarch64": "arm64", "x86_64": "x64", "armv7l": "arm"}.get(os.uname().machine, os.uname().machine)
    return f"linux-{arch}"


def signin(args):
    existing = load_config()
    if existing and not args.get("force"):
        out(f'This device is already linked to {existing["server"]} as "{existing["name"]}". Use --force to link it again.')
        return 0
    server = server_url(args, None)
    name = str(args.get("name") or socket.gethostname())
    status, data = request(f"{server}/api/pair/request", "POST", {"name": name, "platform": platform_name()})
    if status != 201:
        raise RuntimeError(data.get("error") or f"pairing request failed (HTTP {status})")

    out("\nTo link this device to your Acu Connect account, open:\n")
    out(f"  {data['verifyUrl']}\n")
    out(f"or sign in to the dashboard and enter code: {data['code']}\n")
    out("Waiting for approval...")

    deadline = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
    while datetime.now(timezone.utc) < deadline:
        time.sleep(int(os.environ.get("ACU_PAIR_POLL_MS") or 2000) / 1000)
        status, result = request(f"{server}/api/pair/status/{data['pairingId']}", headers={"X-Pairing-Secret": data["secret"]})
        if status == 200 and result.get("status") == "approved":
            save_config({
                "server": server,
                "name": name,
                "deviceId": result["deviceId"],
                "deviceToken": result["deviceToken"],
                "linkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            })
            out(f"\nDevice linked (id {result['deviceId']}). Credentials saved to {config_file()}")
            out("Start the client with: acu-connect run")
            return 0
        if status in (404, 410):
            break
    raise RuntimeError("the pairing code expired before it was used; run signin again")


def log(message):
    out(f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z {message}")


async def run_agent():
    from .daemon import Agent  # needs the installed packages; the other commands don't

    config = load_config()
    if not config:
        raise RuntimeError('this device is not linked yet; run "acu-connect signin" first')
    agent = Agent(config["server"], config["deviceToken"], log)
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping.set)
    running = asyncio.create_task(agent.run())
    stop_wait = asyncio.create_task(stopping.wait())
    await asyncio.wait({running, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
    if stopping.is_set():
        running.cancel()
        await agent.stop()
        return 0
    return running.result()


def status(args):
    config = load_config()
    if not config:
        out('Not linked. Run "acu-connect signin".')
        return 1
    out(f"Linked to:  {config['server']}")
    out(f"Device:     {config['name']} ({config['deviceId']})")
    try:
        code, data = request(f"{server_url(args, config)}/api/device/me", headers={"Authorization": f"Bearer {config['deviceToken']}"})
        if code == 200:
            state = "online" if data["device"]["online"] else "offline (client not running?)"
            out(f"Server:     reachable, device is {state}")
        elif code == 401:
            out("Server:     this device was removed from the account; run signin again")
        else:
            out(f"Server:     HTTP {code}")
    except OSError as e:
        out(f"Server:     unreachable ({e})")
    return 0


def signout(args):
    config = load_config()
    if not config:
        out("Not linked.")
        return 0
    try:
        request(f"{server_url(args, config)}/api/device/me", "DELETE", headers={"Authorization": f"Bearer {config['deviceToken']}"})
    except OSError as e:
        out(f"Warning: could not reach the server ({e}); removing local credentials anyway.")
    delete_config()
    out("Signed out. This device is no longer linked.")
    return 0


def main():
    args = parse_args(sys.argv[1:])
    command = args["_"][0] if args["_"] else None
    commands = {"signin": lambda: signin(args), "run": lambda: asyncio.run(run_agent()), "status": lambda: status(args), "signout": lambda: signout(args)}
    if command not in commands or args.get("help"):
        out(USAGE)
        return 0 if args.get("help") else 1
    return commands[command]()


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        code = 1
    sys.stdout.flush()
    # A web request may still be waiting in a thread; don't wait for it to time out.
    os._exit(code)
