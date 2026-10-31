import json
import os
import platform
import re
import socket
import subprocess
import time
from datetime import datetime, timezone

from . import VERSION

ARCH_NAMES = {"aarch64": "arm64", "x86_64": "x64", "armv7l": "arm"}


def read(path):
    try:
        with open(path, errors="replace") as f:
            return f.read().replace("\0", "").strip() or None
    except OSError:
        return None


def run(*argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=3, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def key_values(text, sep=":"):
    """'Key : value' lines (as in /proc/cpuinfo or lscpu) into a dict; first one wins."""
    out = {}
    for line in (text or "").split("\n"):
        key, found, value = line.partition(sep)
        key = key.strip()
        if found and key and key not in out:
            out[key] = value.strip()
    return out


def parse_network(ip_json):
    """`ip -j addr` output, without loopback and link-local-only (virtual / tunnel) interfaces."""
    out = []
    for link in json.loads(ip_json or "[]"):
        addresses = [
            a["local"]
            for a in link.get("addr_info", [])
            if a.get("scope") == "global" or (a.get("scope") not in ("host", "link") and a.get("local"))
        ]
        if not addresses:
            continue
        mac = link.get("address")
        out.append({"name": link.get("ifname"), "mac": mac if mac != "00:00:00:00:00:00" else None, "addresses": addresses})
    return out


def disk():
    try:
        s = os.statvfs("/")
        return {"total": s.f_blocks * s.f_frsize, "free": s.f_bavail * s.f_frsize}
    except OSError:
        return None


def memory():
    info = key_values(read("/proc/meminfo"))
    kb = lambda key: int(info.get(key, "0 kB").split()[0]) * 1024  # noqa: E731
    return kb("MemTotal"), kb("MemAvailable")


def uptime():
    text = read("/proc/uptime")
    return float(text.split()[0]) if text else 0.0


def cpu_temperature():
    t = read("/sys/class/thermal/thermal_zone0/temp")
    return round(int(t) / 100) / 10 if t and t.isdigit() else None


def time_zone():
    name = read("/etc/timezone")
    if name:
        return name
    try:
        m = re.search(r"zoneinfo/(.+)$", os.readlink("/etc/localtime"))
        return m.group(1) if m else None
    except OSError:
        return None


def collect_info(env=os.environ):
    """Hardware and OS details. Sent once per connection."""
    uname = platform.uname()
    release = key_values(read("/etc/os-release"), "=")
    cpuinfo = key_values(read("/proc/cpuinfo"))
    lscpu = key_values(run("lscpu"))
    model = read("/proc/device-tree/model") or read("/sys/class/dmi/id/product_name") or cpuinfo.get("Model")
    max_khz = read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    max_mhz = round(int(max_khz) / 1000) if max_khz and max_khz.isdigit() else None
    if max_mhz is None and lscpu.get("CPU max MHz"):
        max_mhz = round(float(lscpu["CPU max MHz"]))
    cpu_model = lscpu.get("Model name") or cpuinfo.get("model name")
    info = {
        "hostname": socket.gethostname(),
        "platform": "linux",
        "os": (release.get("PRETTY_NAME") or release.get("NAME") or "").strip('"') or None,
        "kernel": f"{uname.system} {uname.release}",
        "kernelArch": uname.machine,
        "agentArch": ARCH_NAMES.get(uname.machine, uname.machine),
        "agentVersion": VERSION,
        "pythonVersion": platform.python_version(),
        "model": model,
        "manufacturer": "Raspberry Pi Ltd" if model and re.search("raspberry pi", model, re.I) else read("/sys/class/dmi/id/sys_vendor"),
        "serial": read("/proc/device-tree/serial-number") or cpuinfo.get("Serial") or read("/sys/class/dmi/id/product_serial"),
        "revision": cpuinfo.get("Revision"),
        "cpuModel": cpu_model,
        "cpuCores": os.cpu_count(),
        "cpuMaxMHz": max_mhz,
        "memoryTotal": memory()[0],
        "diskTotal": (disk() or {}).get("total"),
        "display": "Wayland" if env.get("WAYLAND_DISPLAY") else "X11" if env.get("DISPLAY") else "Headless",
        "timezone": time_zone(),
        "machineId": read("/etc/machine-id"),
        "network": parse_network(run("ip", "-j", "addr")),
        "bootedAt": datetime.fromtimestamp(time.time() - uptime(), timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    vendor = lscpu.get("Vendor ID")
    if vendor and cpu_model and vendor not in cpu_model:
        info["cpuVendor"] = vendor
    return info


def collect_metrics():
    """Values that change; sent every minute."""
    total, free = memory()
    d = disk() or {}
    return {
        "uptimeSec": round(uptime()),
        "load": [round(n, 2) for n in os.getloadavg()],
        "memTotal": total,
        "memFree": free,
        "diskTotal": d.get("total"),
        "diskFree": d.get("free"),
        "cpuTempC": cpu_temperature(),
    }
