#!/usr/bin/env python3
"""Genesis Imager: write Genesis OS to an SD card with your user, password and Wi-Fi.

The Genesis OS image ships without credentials. Its boot partition carries a
"setup area" (the file genesis-setup.txt, 16 blocks of 512 bytes, each starting
with a marker). While writing the image, Genesis Imager finds those blocks in
the boot partition and fills them with your settings; on the first boot the
image's genesis-firstboot service applies them (through Raspberry Pi's own
userconf-pi for the account) and wipes them from the card again.

Nothing is installed: this is one Python 3 file (3.9 or newer) using only the
standard library. Tk is needed for the window; the command line works without.

  python3 genesis_imager.py                      the window
  python3 genesis_imager.py --list-devices       cards and USB drives
  sudo python3 genesis_imager.py --image genesis.img.xz --device /dev/sdX --user nodoai
  python3 genesis_imager.py --image genesis.img.xz --output custom.img --user nodoai
        (writes a customised image file instead; flash it with any tool)

Writing to a card needs administrator rights: run it with sudo on Linux; the
window asks for them itself (pkexec on Linux, the macOS password prompt,
Windows' administrator prompt).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import lzma
import gzip
import os
import platform
import plistlib
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import BinaryIO, Callable, Optional

VERSION = "1.0.0"
RELEASES_API = "https://api.github.com/repos/acutotech/genesisOS/releases/latest"

SECTOR = 512
SETUP_FILE = "genesis-setup.txt"
SETUP_MAGIC = b"#GENESIS-SETUP-AREA:v1:"
SETUP_PARTS = 16
SETUP_SIZE = SETUP_PARTS * SECTOR
CHUNK = 4 * 1024 * 1024
HOLD_BACK = 1024 * 1024   # the partition table goes out last, so no OS mounts the card mid-write

DEFAULTS = {
    "user": "nodoai",
    "hostname": "nodobox",
    "wifi_ssid": "NodoAI",
    "wifi_country": "IN",
    "timezone": "Asia/Kolkata",
    "keyboard": "us",
    "ssh": True,
}

COUNTRIES = ["IN", "AE", "AU", "BD", "BR", "CA", "CH", "CN", "DE", "ES", "FR", "GB", "ID", "IE", "IT", "JP",
             "KR", "LK", "MY", "NL", "NP", "NZ", "PH", "PK", "QA", "SA", "SE", "SG", "TH", "US", "VN", "ZA"]
TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Asia/Tokyo", "Asia/Shanghai", "Asia/Riyadh",
             "Asia/Dhaka", "Asia/Kathmandu", "Asia/Colombo", "Asia/Karachi", "Asia/Bangkok", "Asia/Jakarta",
             "Australia/Sydney", "Europe/London", "Europe/Berlin", "Europe/Paris", "Europe/Amsterdam",
             "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
             "America/Sao_Paulo", "Africa/Johannesburg", "UTC"]
KEYBOARDS = ["us", "gb", "in", "de", "fr", "es", "it", "jp", "pt", "br", "ch", "se", "no", "dk", "fi", "ara"]

USER_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
TZ_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
KB_RE = re.compile(r"^[a-z]{2,10}$")
SSH_KEY_RE = re.compile(r"^(ssh-|ecdsa-|sk-)\S+ \S+")


class ImagerError(Exception):
    """A problem to show to the user as is."""


# ======================================================================== password hash
_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHA512_ORDER = [(0, 21, 42), (22, 43, 1), (44, 2, 23), (3, 24, 45), (25, 46, 4), (47, 5, 26), (6, 27, 48),
                 (28, 49, 7), (50, 8, 29), (9, 30, 51), (31, 52, 10), (53, 11, 32), (12, 33, 54), (34, 55, 13),
                 (56, 14, 35), (15, 36, 57), (37, 58, 16), (59, 17, 38), (18, 39, 60), (40, 61, 19), (62, 20, 41)]


def _b64_24(b2: int, b1: int, b0: int, n: int) -> str:
    w = (b2 << 16) | (b1 << 8) | b0
    out = []
    for _ in range(n):
        out.append(_ITOA64[w & 0x3F])
        w >>= 6
    return "".join(out)


def sha512_crypt(password: str, salt: Optional[str] = None, rounds: int = 100000) -> str:
    """SHA-512 crypt ("$6$"), as glibc and Debian's chpasswd -e understand it.

    The crypt module left the standard library in Python 3.13, so this is the
    published algorithm (Drepper, "Unix crypt using SHA-256 and SHA-512").
    """
    pw = password.encode("utf-8")
    if salt is None:
        salt = "".join(secrets.choice(_ITOA64) for _ in range(16))
    s = salt.encode("ascii")[:16]
    custom = rounds != 5000
    rounds = max(1000, min(999_999_999, rounds))

    b = hashlib.sha512(pw + s + pw).digest()
    a = hashlib.sha512(pw + s)
    n = len(pw)
    while n > 64:
        a.update(b)
        n -= 64
    a.update(b[:n])
    n = len(pw)
    while n > 0:
        a.update(b if n & 1 else pw)
        n >>= 1
    a_digest = a.digest()
    dp = hashlib.sha512(pw * len(pw)).digest()
    p_seq = (dp * (len(pw) // 64 + 1))[: len(pw)]
    ds = hashlib.sha512(s * (16 + a_digest[0])).digest()
    s_seq = (ds * (len(s) // 64 + 1))[: len(s)]
    c = a_digest
    for r in range(rounds):
        h = hashlib.sha512()
        h.update(p_seq if r & 1 else c)
        if r % 3:
            h.update(s_seq)
        if r % 7:
            h.update(p_seq)
        h.update(c if r & 1 else p_seq)
        c = h.digest()
    encoded = "".join(_b64_24(c[i], c[j], c[k], 4) for i, j, k in _SHA512_ORDER) + _b64_24(0, 0, c[63], 2)
    prefix = f"$6$rounds={rounds}$" if custom else "$6$"
    return f"{prefix}{s.decode('ascii')}${encoded}"


# ======================================================================== settings
@dataclass
class Settings:
    user: str = DEFAULTS["user"]
    password: str = ""                 # plain text, only in memory
    password_hash: str = ""            # used instead of `password` when set
    hostname: str = DEFAULTS["hostname"]
    wifi_ssid: str = DEFAULTS["wifi_ssid"]
    wifi_password: str = ""
    wifi_hidden: bool = False
    wifi_country: str = DEFAULTS["wifi_country"]
    timezone: str = DEFAULTS["timezone"]
    keyboard: str = DEFAULTS["keyboard"]
    keyboard_variant: str = ""
    ssh: bool = DEFAULTS["ssh"]
    ssh_password_auth: bool = True
    ssh_keys: list = field(default_factory=list)

    def validate(self) -> None:
        problems = []
        if not USER_RE.match(self.user) or self.user == "root":
            problems.append("User name: lower-case letters, digits and '-', starting with a letter, at most 32 characters.")
        if not (self.password or self.password_hash):
            problems.append("Password: required (the image has none; this is the only way in).")
        if self.password and len(self.password) < 4:
            problems.append("Password: at least 4 characters.")
        if self.hostname and not HOST_RE.match(self.hostname):
            problems.append("Host name: lower-case letters, digits and '-', at most 63 characters.")
        if self.wifi_ssid:
            if len(self.wifi_ssid.encode("utf-8")) > 32:
                problems.append("Wi-Fi name: at most 32 bytes.")
            if self.wifi_password and not (8 <= len(self.wifi_password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", self.wifi_password)):
                problems.append("Wi-Fi password: 8 to 63 characters (leave it empty for an open network).")
        if self.wifi_country and not re.fullmatch(r"[A-Z]{2}", self.wifi_country):
            problems.append("Wi-Fi country: a two-letter code such as IN.")
        if self.timezone and not TZ_RE.match(self.timezone):
            problems.append("Time zone: an Area/City name such as Asia/Kolkata.")
        if self.keyboard and not KB_RE.match(self.keyboard):
            problems.append("Keyboard: a layout code such as us, gb or in.")
        if self.keyboard_variant and not re.fullmatch(r"[a-z0-9_-]{1,24}", self.keyboard_variant):
            problems.append("Keyboard variant: letters, digits, '_' or '-'.")
        for key in self.ssh_keys:
            if not SSH_KEY_RE.match(key):
                problems.append("SSH key: each line must be a public key (ssh-ed25519 AAAA..., ssh-rsa AAAA...).")
                break
        if problems:
            raise ImagerError("\n".join(problems))

    def payload(self) -> bytes:
        """The setup area contents: KEY=VALUE lines, free text base64-encoded."""
        self.validate()
        pw_hash = self.password_hash or sha512_crypt(self.password)

        def b64(text: str) -> str:
            return base64.b64encode(text.encode("utf-8")).decode("ascii")

        lines = [
            "GENESIS_SETUP=1",
            f"CREATED_BY=Genesis Imager {VERSION}",
            f"USER={self.user}",
            f"PASSWORD_HASH={pw_hash}",
        ]
        if self.hostname:
            lines.append(f"HOSTNAME={self.hostname}")
        if self.wifi_country:
            lines.append(f"WIFI_COUNTRY={self.wifi_country}")
        if self.wifi_ssid:
            lines.append(f"WIFI_SSID_B64={b64(self.wifi_ssid)}")
            if self.wifi_password:
                lines.append(f"WIFI_PSK_B64={b64(self.wifi_password)}")
            lines.append(f"WIFI_HIDDEN={1 if self.wifi_hidden else 0}")
        if self.timezone:
            lines.append(f"TIMEZONE={self.timezone}")
        if self.keyboard:
            lines.append(f"KEYBOARD={self.keyboard}")
            lines.append(f"KEYBOARD_VARIANT={self.keyboard_variant}")
        lines.append(f"SSH={1 if self.ssh else 0}")
        lines.append(f"SSH_PASSWORD_AUTH={1 if self.ssh_password_auth else 0}")
        if self.ssh_keys:
            lines.append(f"SSH_KEYS_B64={b64(chr(10).join(self.ssh_keys))}")
        lines.append("END=1")
        data = ("\n".join(lines) + "\n").encode("utf-8")
        if len(data) > SETUP_SIZE:
            raise ImagerError(f"The settings are {len(data)} bytes; the setup area holds {SETUP_SIZE}. Use fewer or shorter SSH keys.")
        return data + b"\n" * (SETUP_SIZE - len(data))


def placeholder() -> bytes:
    """The inert setup area the image ships with (also restored after first boot)."""
    parts = []
    for i in range(1, SETUP_PARTS + 1):
        head = SETUP_MAGIC + f"{i:02d}:{SETUP_PARTS:02d}:".encode()
        text = b" Genesis OS setup area. Genesis Imager writes your settings here while flashing;" \
               b" the first boot applies and erases them. Leave this file in place." if i == 1 else b""
        body = head + text
        parts.append(body + b"#" * (SECTOR - 1 - len(body)) + b"\n")
    return b"".join(parts)


# ======================================================================== images
def _open_raw(path: Path) -> BinaryIO:
    """A readable stream of the uncompressed image, whatever the file format."""
    name = path.name.lower()
    if name.endswith(".zip"):
        zf = zipfile.ZipFile(path)
        members = [m for m in zf.namelist() if re.search(r"\.img(\.xz|\.gz)?$", m.lower())]
        if not members:
            raise ImagerError(f"{path.name} contains no .img, .img.xz or .img.gz file")
        inner = zf.open(sorted(members, key=len)[0])
        member = members[0].lower()
        if member.endswith(".xz"):
            return lzma.open(inner)
        if member.endswith(".gz"):
            return gzip.open(inner)
        return inner
    if name.endswith(".xz"):
        return lzma.open(path)
    if name.endswith(".gz"):
        return gzip.open(path)
    return open(path, "rb")


def _read_exact(stream: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        part = stream.read(n - len(buf))
        if not part:
            break
        buf += part
    return bytes(buf)


@dataclass
class ImageLayout:
    boot_start: int            # byte offset of partition 1 (the FAT boot partition)
    boot_end: int
    image_end: int             # end of the last partition: the image's minimum size
    setup_offsets: dict        # part number (1..16) -> byte offset in the image


def parse_mbr(sector0: bytes) -> list:
    """[(type, start_byte, size_bytes)] of the primary partitions."""
    if len(sector0) < SECTOR or sector0[510:512] != b"\x55\xaa":
        raise ImagerError("This is not a disk image (no partition table). Choose a Genesis OS .img or .img.xz file.")
    parts = []
    for i in range(4):
        entry = sector0[446 + 16 * i: 446 + 16 * (i + 1)]
        ptype = entry[4]
        start, count = struct.unpack("<II", entry[8:16])
        if ptype == 0xEE:
            raise ImagerError("This image uses a GPT partition table; Genesis OS images use MBR.")
        if ptype and count:
            parts.append((ptype, start * SECTOR, count * SECTOR))
    if not parts:
        raise ImagerError("The image's partition table is empty.")
    return parts


def scan_image(path: Path, progress: Optional[Callable[[str, float], None]] = None) -> ImageLayout:
    """First pass: read the partition table and find the setup area in the boot partition."""
    with _open_raw(path) as raw:
        sector0 = _read_exact(raw, SECTOR)
        parts = parse_mbr(sector0)
        ptype, boot_start, boot_size = parts[0]
        if ptype not in (0x0B, 0x0C, 0x0E, 0x06):
            raise ImagerError("The first partition is not a FAT boot partition; this is not a Raspberry Pi image.")
        boot_end = boot_start + boot_size
        image_end = max(start + size for _t, start, size in parts)
        found: dict = {}
        pos = SECTOR
        while pos < boot_end:
            chunk = _read_exact(raw, min(CHUNK, boot_end - pos))
            if not chunk:
                break
            if pos + len(chunk) > boot_start:
                first = max(0, boot_start - pos)
                first += (-(pos + first)) % SECTOR
                idx = chunk.find(SETUP_MAGIC, first)
                while idx != -1:
                    if (pos + idx) % SECTOR == 0:
                        m = re.match(rb"#GENESIS-SETUP-AREA:v1:(\d\d):(\d\d):", chunk[idx: idx + 32])
                        if m and int(m.group(2)) == SETUP_PARTS:
                            number = int(m.group(1))
                            if number in found:
                                raise ImagerError("The image has two setup areas; it cannot be customised safely.")
                            found[number] = pos + idx
                    idx = chunk.find(SETUP_MAGIC, idx + 1)
            pos += len(chunk)
            if progress:
                progress("Checking the image", min(1.0, pos / boot_end))
    missing = [n for n in range(1, SETUP_PARTS + 1) if n not in found]
    if missing:
        if not found:
            raise ImagerError("This image has no Genesis setup area: it is not a Genesis OS image, or one built before Genesis Imager existed.")
        raise ImagerError(f"The setup area in this image is incomplete (missing blocks {missing}).")
    return ImageLayout(boot_start, boot_end, image_end, found)


# ======================================================================== targets
class Target:
    """Where the image goes: a device or a file. Writes are sequential, in multiples of 512."""

    name = ""

    def open(self) -> None: ...
    def write_at(self, offset: int, data: bytes) -> None: ...
    def read_at(self, offset: int, size: int) -> bytes: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
    def finish(self) -> None: ...


class FileTarget(Target):
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.name = str(self.path)
        self.fd = -1

    def open(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        self.fd = os.open(self.path, flags, 0o644)

    def write_at(self, offset: int, data: bytes) -> None:
        os.lseek(self.fd, offset, os.SEEK_SET)
        view = memoryview(data)
        while view:
            n = os.write(self.fd, view)
            view = view[n:]

    def read_at(self, offset: int, size: int) -> bytes:
        os.lseek(self.fd, offset, os.SEEK_SET)
        buf = bytearray()
        while len(buf) < size:
            part = os.read(self.fd, size - len(buf))
            if not part:
                break
            buf += part
        return bytes(buf)

    def flush(self) -> None:
        os.fsync(self.fd)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


@dataclass
class Device:
    path: str            # /dev/sdb, /dev/disk4, \\.\PhysicalDrive2
    size: int
    description: str
    removable: bool
    system: bool         # holds the running system: never offered
    ident: str = ""      # disk number on Windows, diskN on macOS

    def label(self) -> str:
        return f"{self.description}  ({human(self.size)})  {self.path}"


class DeviceTarget(FileTarget):
    """A whole disk. Unmounted before writing; on Windows its partitions are cleared first."""

    def __init__(self, device: Device) -> None:
        self.device = device
        self.name = device.path
        self.fd = -1
        self.path = Path(device.path)
        self.write_path = device.path

    def open(self) -> None:
        system = platform.system()
        if system == "Linux":
            for mnt in _linux_mountpoints(self.device.path):
                subprocess.run(["umount", mnt], check=False, capture_output=True)
            self.fd = os.open(self.device.path, os.O_RDWR | os.O_EXCL | getattr(os, "O_SYNC", 0))
        elif system == "Darwin":
            subprocess.run(["diskutil", "unmountDisk", "force", self.device.path], check=False, capture_output=True)
            self.write_path = self.device.path.replace("/dev/disk", "/dev/rdisk")
            self.fd = os.open(self.write_path, os.O_RDWR)
        elif system == "Windows":
            script = f"select disk {self.device.ident}\nclean\nrescan\nexit\n"
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
                fh.write(script)
            try:
                res = subprocess.run(["diskpart", "/s", fh.name], capture_output=True, text=True)
            finally:
                os.unlink(fh.name)
            if res.returncode != 0:
                raise ImagerError(f"diskpart could not clear disk {self.device.ident}:\n{res.stdout}{res.stderr}")
            time.sleep(1)
            self.fd = os.open(self.device.path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        else:
            raise ImagerError(f"Writing to devices is not supported on {system}")

    def flush(self) -> None:
        try:
            os.fsync(self.fd)
        except OSError:
            pass  # some raw devices refuse fsync; their writes are unbuffered

    def finish(self) -> None:
        system = platform.system()
        if system == "Linux":
            subprocess.run(["sync"], check=False)
            if shutil.which("eject"):
                subprocess.run(["eject", self.device.path], check=False, capture_output=True)
        elif system == "Darwin":
            subprocess.run(["diskutil", "eject", self.device.path], check=False, capture_output=True)


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1000
    return f"{n} B"


def _linux_mountpoints(dev: str) -> list:
    out = subprocess.run(["lsblk", "-J", "-o", "PATH,MOUNTPOINT", dev], capture_output=True, text=True)
    mounts = []

    def walk(nodes):
        for node in nodes or []:
            if node.get("mountpoint"):
                mounts.append(node["mountpoint"])
            walk(node.get("children"))

    try:
        walk(json.loads(out.stdout).get("blockdevices"))
    except ValueError:
        pass
    return mounts


def list_devices(show_all: bool = False) -> list:
    """Disks that could take the image. The disk the running system lives on is never listed."""
    system = platform.system()
    devices: list = []
    if system == "Linux":
        out = subprocess.run(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,VENDOR,TRAN,RM,HOTPLUG,TYPE,RO,MOUNTPOINT"],
                             capture_output=True, text=True)
        try:
            tree = json.loads(out.stdout).get("blockdevices", [])
        except ValueError:
            tree = []
        for disk in tree:
            if disk.get("type") not in ("disk", "loop") or str(disk.get("ro")) in ("1", "True", "true"):
                continue
            mounts = []

            def walk(node):
                if node.get("mountpoint"):
                    mounts.append(node["mountpoint"])
                for child in node.get("children") or []:
                    walk(child)

            walk(disk)
            system_disk = any(m in ("/", "/boot", "/boot/efi", "/boot/firmware", "[SWAP]", "/usr", "/var", "/home") for m in mounts)
            removable = str(disk.get("rm")) in ("1", "True", "true") or str(disk.get("hotplug")) in ("1", "True", "true") \
                or (disk.get("tran") or "") in ("usb", "mmc")
            name = disk.get("name", "")
            if name.startswith(("ram", "zram")) or not int(disk.get("size") or 0):
                continue
            if name.startswith("loop") and not show_all:
                continue
            desc = " ".join(x for x in ((disk.get("vendor") or "").strip(), (disk.get("model") or "").strip()) if x) or disk.get("name", "")
            devices.append(Device(disk.get("path") or f"/dev/{disk['name']}", int(disk.get("size") or 0), desc, removable, system_disk))
    elif system == "Darwin":
        out = subprocess.run(["diskutil", "list", "-plist", "physical"], capture_output=True)
        try:
            listing = plistlib.loads(out.stdout)
        except Exception:  # noqa: BLE001
            listing = {}
        for entry in listing.get("AllDisksAndPartitions", []):
            ident = entry.get("DeviceIdentifier", "")
            info_out = subprocess.run(["diskutil", "info", "-plist", ident], capture_output=True)
            try:
                info = plistlib.loads(info_out.stdout)
            except Exception:  # noqa: BLE001
                continue
            internal = bool(info.get("Internal"))
            removable = bool(info.get("Removable") or info.get("RemovableMedia") or info.get("Ejectable")) or not internal
            system_disk = internal and any(p.get("MountPoint") == "/" for p in entry.get("Partitions", []) + entry.get("APFSVolumes", []))
            desc = info.get("MediaName") or info.get("IORegistryEntryName") or ident
            devices.append(Device(f"/dev/{ident}", int(info.get("TotalSize") or info.get("Size") or 0), desc, removable,
                                  system_disk or (internal and not show_all), ident))
    elif system == "Windows":
        ps = ("Get-Disk | Select-Object Number,FriendlyName,Size,BusType,IsBoot,IsSystem,IsReadOnly | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True)
        try:
            data = json.loads(out.stdout or "[]")
        except ValueError:
            data = []
        if isinstance(data, dict):
            data = [data]
        for disk in data:
            bus = str(disk.get("BusType", ""))
            removable = bus in ("USB", "SD", "MMC", "7", "12", "13")
            system_disk = bool(disk.get("IsBoot")) or bool(disk.get("IsSystem"))
            if disk.get("IsReadOnly"):
                continue
            num = str(disk.get("Number"))
            devices.append(Device(rf"\\.\PhysicalDrive{num}", int(disk.get("Size") or 0),
                                  f"{disk.get('FriendlyName', 'Disk ' + num)} [{bus}]", removable, system_disk, num))
    devices = [d for d in devices if not d.system]
    if not show_all:
        devices = [d for d in devices if d.removable]
    return devices


# ======================================================================== writing
@dataclass
class WriteResult:
    bytes_written: int
    sha256: str
    verified: bool
    seconds: float


def write_image(image: Path, target: Target, settings: Settings, *, verify: bool = True,
                progress: Optional[Callable[[str, float], None]] = None,
                cancel: Optional[threading.Event] = None, layout: Optional[ImageLayout] = None) -> WriteResult:
    """Write `image` to `target`, with the setup area replaced by `settings`."""
    report = progress or (lambda _stage, _fraction: None)
    payload = settings.payload()
    if layout is None:
        layout = scan_image(image, progress)
    replacements = {layout.setup_offsets[n]: payload[(n - 1) * SECTOR: n * SECTOR] for n in range(1, SETUP_PARTS + 1)}
    if isinstance(target, DeviceTarget) and target.device.size and target.device.size < layout.image_end:
        raise ImagerError(f"The card is too small: {human(target.device.size)}, the image needs {human(layout.image_end)}.")
    started = time.monotonic()
    digest = hashlib.sha256()      # the whole image as written: reported to the user
    body = hashlib.sha256()        # everything after the held-back first MiB: verified
    held = b""
    written = 0
    verified = False
    target.open()
    try:
        with _open_raw(image) as raw:
            pos = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise ImagerError("Cancelled. The card is incomplete; write it again before using it.")
                chunk = _read_exact(raw, CHUNK)
                if not chunk:
                    break
                if len(chunk) % SECTOR:
                    chunk += b"\0" * (SECTOR - len(chunk) % SECTOR)
                buf = bytearray(chunk)
                for offset, data in replacements.items():
                    if pos <= offset < pos + len(buf):
                        buf[offset - pos: offset - pos + SECTOR] = data
                chunk = bytes(buf)
                digest.update(chunk)
                keep = min(len(chunk), max(0, HOLD_BACK - pos))
                if keep:
                    held += chunk[:keep]
                if keep < len(chunk):
                    target.write_at(pos + keep, chunk[keep:])
                    body.update(chunk[keep:])
                pos += len(chunk)
                written = pos
                report("Writing", min(0.999, pos / max(layout.image_end, pos)))
        target.flush()
        # Verify while the card still has no partition table: once it has one,
        # Windows and macOS mount it and may touch the boot partition.
        if verify:
            check = hashlib.sha256()
            pos = len(held)
            while pos < written:
                if cancel is not None and cancel.is_set():
                    raise ImagerError("Verification cancelled. The card is incomplete; write it again before using it.")
                n = min(CHUNK, written - pos)
                data = target.read_at(pos, n)
                if len(data) != n:
                    raise ImagerError(f"Reading the card back stopped at {human(pos + len(data))}.")
                check.update(data)
                pos += n
                report("Verifying", pos / written)
            if check.hexdigest() != body.hexdigest():
                raise ImagerError("Verification failed: the card does not hold what was written. Try another card.")
        if held:
            target.write_at(0, held)   # the partition table last: the card is only usable when complete
            target.flush()
            if verify and target.read_at(0, len(held)) != held:
                raise ImagerError("Verification failed at the start of the card. Try another card.")
        verified = verify
    finally:
        target.close()
    target.finish()
    report("Done", 1.0)
    return WriteResult(written, digest.hexdigest(), verified, time.monotonic() - started)


# ======================================================================== downloads
def cache_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / "genesis-imager"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_latest(progress: Optional[Callable[[str, float], None]] = None, cancel: Optional[threading.Event] = None) -> Path:
    """Download the newest Genesis OS release image (checked against its SHA256SUMS)."""
    report = progress or (lambda _s, _f: None)
    req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json", "User-Agent": f"genesis-imager/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            release = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ImagerError("No Genesis OS release is published yet. Download the image from the build's "
                              "artifacts (genesis-os.zip) and use 'Choose file'.") from exc
        raise ImagerError(f"Could not reach GitHub: {exc}") from exc
    except OSError as exc:
        raise ImagerError(f"Could not reach GitHub: {exc}") from exc
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    images = [n for n in assets if n.endswith(".img.xz")]
    if not images:
        raise ImagerError(f"Release {release.get('tag_name')} has no .img.xz image.")
    name = images[0]
    expected = ""
    if "SHA256SUMS" in assets:
        with urllib.request.urlopen(assets["SHA256SUMS"], timeout=30) as resp:
            for line in resp.read().decode().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].lstrip("*") == name:
                    expected = parts[0]
    dest = cache_dir() / name
    if dest.exists() and expected and _sha256_file(dest) == expected:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(assets[name], timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        h = hashlib.sha256()
        while True:
            if cancel is not None and cancel.is_set():
                raise ImagerError("Download cancelled.")
            block = resp.read(1024 * 1024)
            if not block:
                break
            out.write(block)
            h.update(block)
            done += len(block)
            report(f"Downloading {name}", done / total if total else 0.0)
    if expected and h.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        raise ImagerError("The download is corrupt (checksum mismatch). Try again.")
    tmp.replace(dest)
    return dest


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


# ======================================================================== privileges
def is_admin() -> bool:
    if platform.system() == "Windows":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False
    return os.geteuid() == 0


def _self_command() -> list:
    if getattr(sys, "frozen", False):        # a PyInstaller build
        return [sys.executable]
    return [sys.executable, os.path.abspath(__file__)]


def run_privileged_worker(job: dict, on_progress: Callable[[str, float], None], cancel: threading.Event) -> dict:
    """Run the write as administrator in a helper process; progress comes back through a file."""
    workdir = Path(tempfile.mkdtemp(prefix="genesis-imager-"))
    os.chmod(workdir, 0o700)
    job_file = workdir / "job.json"
    progress_file = workdir / "progress.jsonl"
    old_umask = os.umask(0o077)
    try:
        job_file.write_text(json.dumps(job))
    finally:
        os.umask(old_umask)
    cmd = _self_command() + ["--worker", str(job_file), "--progress-file", str(progress_file)]
    system = platform.system()
    if system == "Linux":
        if not shutil.which("pkexec"):
            raise ImagerError("Writing needs administrator rights: run Genesis Imager with sudo.")
        proc = subprocess.Popen(["pkexec"] + cmd)
    elif system == "Darwin":
        quoted = " ".join("'" + c.replace("'", "'\\''") + "'" for c in cmd)
        script = f'do shell script "{quoted}" with administrator privileges'
        proc = subprocess.Popen(["osascript", "-e", script])
    else:
        raise ImagerError("Run Genesis Imager as administrator to write cards.")
    seen = 0
    result: dict = {}
    try:
        while True:
            done = proc.poll() is not None
            if progress_file.exists():
                lines = progress_file.read_text().splitlines()
                for line in lines[seen:]:
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if "stage" in msg:
                        on_progress(msg["stage"], float(msg.get("fraction", 0)))
                    if "result" in msg or "error" in msg:
                        result = msg
                seen = len(lines)
            if done:
                break
            if cancel.is_set():
                (workdir / "cancel").touch()
            time.sleep(0.3)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not result:
        raise ImagerError("The write was not authorised or the helper stopped unexpectedly.")
    if "error" in result:
        raise ImagerError(result["error"])
    return result["result"]


def worker_main(job_path: str, progress_path: str) -> int:
    """Privileged helper: the job file names the image, the device and the settings."""
    job = json.loads(Path(job_path).read_text())
    Path(job_path).unlink(missing_ok=True)   # holds the password hash: gone as soon as read
    cancel = threading.Event()
    cancel_file = Path(progress_path).parent / "cancel"

    def emit(obj: dict) -> None:
        with open(progress_path, "a") as fh:
            fh.write(json.dumps(obj) + "\n")
        if cancel_file.exists():
            cancel.set()

    try:
        settings = Settings(**job["settings"])
        device = Device(**job["device"])
        res = write_image(Path(job["image"]), DeviceTarget(device), settings, verify=job.get("verify", True),
                          progress=lambda s, f: emit({"stage": s, "fraction": f}), cancel=cancel)
        emit({"result": asdict(res)})
        return 0
    except ImagerError as exc:
        emit({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        emit({"error": f"{type(exc).__name__}: {exc}"})
    return 1


# ======================================================================== remembered settings
def settings_file() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "GenesisImager" / "settings.json"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "GenesisImager" / "settings.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "genesis-imager" / "settings.json"


REMEMBERED = ("user", "hostname", "wifi_ssid", "wifi_hidden", "wifi_country", "timezone", "keyboard",
              "keyboard_variant", "ssh", "ssh_password_auth", "ssh_keys")


def load_remembered() -> dict:
    try:
        data = json.loads(settings_file().read_text())
        return {k: v for k, v in data.items() if k in REMEMBERED}
    except (OSError, ValueError):
        return {}


def save_remembered(settings: Settings) -> None:
    """Remembers everything except the passwords."""
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: getattr(settings, k) for k in REMEMBERED}
    path.write_text(json.dumps(data, indent=2))


# ======================================================================== command line
def cli(argv: list) -> int:
    p = argparse.ArgumentParser(prog="genesis-imager", description="Write Genesis OS with your user, password and Wi-Fi.")
    p.add_argument("--version", action="version", version=f"Genesis Imager {VERSION}")
    p.add_argument("--list-devices", action="store_true", help="list cards and USB drives, then exit")
    p.add_argument("--all", action="store_true", help="with --list-devices or --device: include non-removable disks")
    p.add_argument("--image", help="image file (.img, .img.xz, .img.gz, or a .zip holding one); 'latest' downloads the newest release")
    target = p.add_mutually_exclusive_group()
    target.add_argument("--device", help="card or drive to write, e.g. /dev/sdb, /dev/disk4, \\\\.\\PhysicalDrive2")
    target.add_argument("--output", help="write a customised image file instead of a card")
    p.add_argument("--user", default=DEFAULTS["user"])
    p.add_argument("--password", help="the account password (prompted for if omitted)")
    p.add_argument("--password-file", help="read the password from a file ('-' = standard input)")
    p.add_argument("--hostname", default=DEFAULTS["hostname"])
    p.add_argument("--wifi-ssid", default=DEFAULTS["wifi_ssid"], help="empty = no Wi-Fi")
    p.add_argument("--wifi-password", default=None, help="prompted for if a Wi-Fi name is set and this is omitted")
    p.add_argument("--wifi-hidden", action="store_true")
    p.add_argument("--wifi-country", default=DEFAULTS["wifi_country"])
    p.add_argument("--timezone", default=DEFAULTS["timezone"])
    p.add_argument("--keyboard", default=DEFAULTS["keyboard"])
    p.add_argument("--keyboard-variant", default="")
    p.add_argument("--no-ssh", action="store_true", help="turn SSH off")
    p.add_argument("--ssh-key-file", action="append", default=[], help="public key file to authorise (repeatable)")
    p.add_argument("--ssh-keys-only", action="store_true", help="refuse SSH password logins (needs --ssh-key-file)")
    p.add_argument("--no-verify", action="store_true", help="skip reading the card back")
    p.add_argument("--yes", action="store_true", help="do not ask before erasing the device")
    p.add_argument("--write-placeholder", metavar="FILE", help=argparse.SUPPRESS)
    p.add_argument("--print-payload", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--worker", help=argparse.SUPPRESS)
    p.add_argument("--progress-file", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.worker:
        return worker_main(args.worker, args.progress_file)
    if args.write_placeholder:
        Path(args.write_placeholder).write_bytes(placeholder())
        return 0
    if args.list_devices:
        devices = list_devices(show_all=args.all)
        if not devices:
            print("No removable card or drive found." + ("" if args.all else " (--all also lists fixed disks)"))
        for d in devices:
            print(f"{d.path}\t{human(d.size)}\t{d.description}")
        return 0
    if not args.image:
        p.error("--image is required (or --list-devices)")
    if not (args.device or args.output or args.print_payload):
        p.error("give --device or --output")

    if args.password_file:
        password = (sys.stdin.readline() if args.password_file == "-" else Path(args.password_file).read_text()).rstrip("\r\n")
    elif args.password is not None:
        password = args.password
    else:
        password = getpass.getpass(f"Password for {args.user}: ")
        if getpass.getpass("Again: ") != password:
            print("The passwords differ.", file=sys.stderr)
            return 2
    wifi_password = args.wifi_password
    if args.wifi_ssid and wifi_password is None:
        wifi_password = getpass.getpass(f"Wi-Fi password for {args.wifi_ssid} (empty for an open network): ")
    keys = []
    for kf in args.ssh_key_file:
        keys += [line.strip() for line in Path(kf).read_text().splitlines() if line.strip() and not line.startswith("#")]
    settings = Settings(user=args.user, password=password, hostname=args.hostname, wifi_ssid=args.wifi_ssid,
                        wifi_password=wifi_password or "", wifi_hidden=args.wifi_hidden, wifi_country=args.wifi_country,
                        timezone=args.timezone, keyboard=args.keyboard, keyboard_variant=args.keyboard_variant,
                        ssh=not args.no_ssh, ssh_password_auth=not args.ssh_keys_only, ssh_keys=keys)
    try:
        settings.validate()
        if args.print_payload:
            sys.stdout.write(settings.payload().rstrip(b"\n").decode() + "\n")
            return 0
        last = {"stage": "", "t": 0.0}

        def progress(stage: str, fraction: float) -> None:
            now = time.monotonic()
            if stage != last["stage"] or now - last["t"] > 1 or fraction >= 1:
                print(f"\r{stage}: {fraction * 100:5.1f}%", end="" if fraction < 1 else "\n", flush=True)
                last.update(stage=stage, t=now)

        image = download_latest(progress) if args.image == "latest" else Path(args.image)
        if not image.exists():
            raise ImagerError(f"{image} does not exist")
        layout = scan_image(image, progress)
        print(f"\nImage: {image.name}, {human(layout.image_end)}; setup area found.")
        if args.output:
            target: Target = FileTarget(Path(args.output))
        else:
            devices = {d.path: d for d in list_devices(show_all=True)}
            device = devices.get(args.device)
            if device is None:
                raise ImagerError(f"{args.device} is not a disk that can be written (or it holds the running system).")
            if not device.removable and not args.all:
                raise ImagerError(f"{args.device} is not removable; add --all if you really mean it.")
            if not is_admin():
                raise ImagerError("Writing to a device needs administrator rights (run with sudo, or as administrator).")
            if not args.yes:
                answer = input(f"Erase {device.label()} and write Genesis OS? Type yes: ")
                if answer.strip().lower() != "yes":
                    print("Nothing written.")
                    return 1
            target = DeviceTarget(device)
        res = write_image(image, target, settings, verify=not args.no_verify, progress=progress, layout=layout)
        print(f"Wrote {human(res.bytes_written)} in {res.seconds:.0f} s"
              + (", verified." if res.verified else ".") + f" sha256 {res.sha256}")
        print(f"First boot: user {settings.user}, host {settings.hostname or '(unchanged)'}"
              + (f", Wi-Fi {settings.wifi_ssid}" if settings.wifi_ssid else "") + ".")
        return 0
    except ImagerError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


# ======================================================================== window
def gui() -> int:  # noqa: C901 - one screen, built in one place
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Genesis Imager")
    root.minsize(760, 640)
    bg, card, edge, text, dim, accent = "#0e1520", "#151e2c", "#253248", "#e9eff6", "#93a3b8", "#4f8cff"
    root.configure(bg=bg)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    font = ("Inter", 10) if "Inter" in root.tk.call("font", "families") else ("Segoe UI", 10) if platform.system() == "Windows" else ("TkDefaultFont", 10)
    style.configure(".", background=bg, foreground=text, fieldbackground=card, bordercolor=edge, lightcolor=edge,
                    darkcolor=edge, troughcolor=card, focuscolor=accent, font=font)
    style.configure("Card.TFrame", background=card)
    style.configure("Card.TLabel", background=card, foreground=text)
    style.configure("Dim.TLabel", background=card, foreground=dim)
    style.configure("Title.TLabel", background=bg, foreground=text, font=(font[0], 18, "bold"))
    style.configure("Sub.TLabel", background=bg, foreground=dim)
    style.configure("Section.TLabel", background=card, foreground=text, font=(font[0], 11, "bold"))
    style.configure("TEntry", fieldbackground="#0f1724", foreground=text, insertcolor=text, padding=6)
    style.configure("TCombobox", fieldbackground="#0f1724", foreground=text, arrowcolor=text, padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", "#0f1724")], foreground=[("readonly", text)])
    style.configure("TCheckbutton", background=card, foreground=text)
    style.map("TCheckbutton", background=[("active", card)])
    style.configure("TButton", background="#1d2838", foreground=text, padding=(14, 7), borderwidth=1)
    style.map("TButton", background=[("active", "#26344a"), ("disabled", "#151e2c")], foreground=[("disabled", dim)])
    style.configure("Accent.TButton", background=accent, foreground="#ffffff", padding=(22, 9), font=(font[0], 11, "bold"))
    style.map("Accent.TButton", background=[("active", "#6ea0ff"), ("disabled", "#2a3a58")])
    style.configure("TNotebook", background=card, borderwidth=0)
    style.configure("TNotebook.Tab", background="#111a27", foreground=dim, padding=(14, 6))
    style.map("TNotebook.Tab", background=[("selected", card)], foreground=[("selected", text)])
    style.configure("Horizontal.TProgressbar", background=accent, troughcolor="#0f1724", bordercolor=edge)
    root.option_add("*TCombobox*Listbox.background", "#0f1724")
    root.option_add("*TCombobox*Listbox.foreground", text)
    root.option_add("*TCombobox*Listbox.selectBackground", accent)

    remembered = {**DEFAULTS, **load_remembered()}
    state = {"image": None, "devices": [], "busy": False, "cancel": threading.Event()}

    # --- header -----------------------------------------------------------------------
    header = ttk.Frame(root, padding=(24, 18, 24, 6))
    header.pack(fill="x")
    logo_path = None
    here = Path(__file__).resolve().parent
    for candidate in (Path(getattr(sys, "_MEIPASS", "")) / "genesis-os.png", here / "genesis-os.png",
                      here.parent / "stage-custom/10-branding/files/icons/hicolor/64x64/apps/genesis-os.png"):
        if candidate.is_file():
            logo_path = candidate
            break
    if logo_path:
        try:
            logo = tk.PhotoImage(file=str(logo_path))   # 64 px; Tk only shrinks by whole factors
            if logo.width() > 96:
                factor = logo.width() // 64
                logo = logo.subsample(factor, factor)
            ttk.Label(header, image=logo, background=bg).pack(side="left", padx=(0, 14))
            root._logo = logo  # keep a reference
            try:
                root.iconphoto(True, tk.PhotoImage(file=str(logo_path)))
            except tk.TclError:
                pass
        except tk.TclError:
            pass
    titles = ttk.Frame(header)
    titles.pack(side="left", fill="x")
    ttk.Label(titles, text="Genesis Imager", style="Title.TLabel").pack(anchor="w")
    ttk.Label(titles, text="Write Genesis OS to a card, with your user, password and Wi-Fi ready on first boot.",
              style="Sub.TLabel").pack(anchor="w")

    body = ttk.Frame(root, padding=(24, 10, 24, 0))
    body.pack(fill="both", expand=True)

    def card_frame(parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame", padding=16)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        return frame

    # --- image and device ---------------------------------------------------------------
    source = card_frame(body, "1  Operating system")
    source.pack(fill="x", pady=(0, 12))
    image_var = tk.StringVar(value="No image chosen")
    ttk.Label(source, textvariable=image_var, style="Card.TLabel").grid(row=1, column=0, sticky="w")
    source.columnconfigure(0, weight=1)

    def choose_file():
        path = filedialog.askopenfilename(title="Choose a Genesis OS image",
                                          filetypes=[("Disk images", "*.img *.img.xz *.img.gz *.zip *.xz *.gz"), ("All files", "*")])
        if path:
            set_image(Path(path))

    def set_image(path: Path):
        state["image"] = path
        image_var.set(f"{path.name}  ({human(path.stat().st_size)})")
        refresh_write_button()

    def download():
        run_task("Downloading", lambda prog, cancel: download_latest(prog, cancel), on_done=set_image)

    ttk.Button(source, text="Download latest", command=download).grid(row=1, column=1, padx=(8, 0))
    ttk.Button(source, text="Choose file…", command=choose_file).grid(row=1, column=2, padx=(8, 0))

    storage = card_frame(body, "2  Storage")
    storage.pack(fill="x", pady=(0, 12))
    storage.columnconfigure(0, weight=1)
    device_var = tk.StringVar()
    device_box = ttk.Combobox(storage, textvariable=device_var, state="readonly")
    device_box.grid(row=1, column=0, sticky="ew")
    show_all = tk.BooleanVar(value=False)

    def refresh_devices():
        try:
            state["devices"] = list_devices(show_all=show_all.get())
        except Exception as exc:  # noqa: BLE001
            state["devices"] = []
            status_var.set(f"Could not list disks: {exc}")
        device_box["values"] = [d.label() for d in state["devices"]]
        if state["devices"]:
            device_box.current(0)
        else:
            device_var.set("Insert a card, then Refresh")
        refresh_write_button()

    ttk.Button(storage, text="Refresh", command=refresh_devices).grid(row=1, column=1, padx=(8, 0))
    ttk.Checkbutton(storage, text="Show fixed disks", variable=show_all, command=refresh_devices).grid(row=1, column=2, padx=(8, 0))

    # --- settings ------------------------------------------------------------------------
    settings_card = card_frame(body, "3  Settings")
    settings_card.pack(fill="both", expand=True)
    tabs = ttk.Notebook(settings_card)
    tabs.grid(row=1, column=0, columnspan=4, sticky="nsew")
    settings_card.columnconfigure(0, weight=1)
    settings_card.rowconfigure(1, weight=1)
    vars_: dict = {}

    def form(tab, rows):
        for r, (label, key, kind, extra) in enumerate(rows):
            ttk.Label(tab, text=label, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=6, padx=(0, 16))
            if kind == "check":
                var = tk.BooleanVar(value=bool(remembered.get(key, extra)))
                ttk.Checkbutton(tab, variable=var).grid(row=r, column=1, sticky="w")
            elif kind == "combo":
                var = tk.StringVar(value=str(remembered.get(key, "")))
                ttk.Combobox(tab, textvariable=var, values=extra, width=30).grid(row=r, column=1, sticky="ew")
            elif kind == "secret":
                var = tk.StringVar()
                ttk.Entry(tab, textvariable=var, show="•", width=32).grid(row=r, column=1, sticky="ew")
            else:
                var = tk.StringVar(value=str(remembered.get(key, "")))
                ttk.Entry(tab, textvariable=var, width=32).grid(row=r, column=1, sticky="ew")
            if kind != "check" and extra and isinstance(extra, str):
                ttk.Label(tab, text=extra, style="Dim.TLabel").grid(row=r, column=2, sticky="w", padx=(12, 0))
            vars_[key] = var
        tab.columnconfigure(1, weight=1)

    general = ttk.Frame(tabs, style="Card.TFrame", padding=14)
    form(general, [
        ("User name", "user", "text", "lower case, e.g. nodoai"),
        ("Password", "password", "secret", ""),
        ("Password again", "password2", "secret", ""),
        ("Host name", "hostname", "text", "the Pi answers on <name>.local"),
        ("Time zone", "timezone", "combo", TIMEZONES),
        ("Keyboard", "keyboard", "combo", KEYBOARDS),
    ])
    wifi = ttk.Frame(tabs, style="Card.TFrame", padding=14)
    form(wifi, [
        ("Network name", "wifi_ssid", "text", "empty = no Wi-Fi"),
        ("Password", "wifi_password", "secret", ""),
        ("Country", "wifi_country", "combo", COUNTRIES),
        ("Hidden network", "wifi_hidden", "check", False),
    ])
    services = ttk.Frame(tabs, style="Card.TFrame", padding=14)
    form(services, [
        ("SSH", "ssh", "check", True),
        ("Key logins only", "ssh_keys_only", "check", False),
    ])
    ttk.Label(services, text="SSH public keys (one per line)", style="Card.TLabel").grid(row=2, column=0, sticky="nw", pady=6)
    keys_text = tk.Text(services, height=4, width=48, bg="#0f1724", fg=text, insertbackground=text, relief="flat",
                        highlightthickness=1, highlightbackground=edge, highlightcolor=accent, font=("TkFixedFont", 9))
    keys_text.grid(row=2, column=1, sticky="ew", pady=6)
    keys_text.insert("1.0", "\n".join(remembered.get("ssh_keys") or []))
    if not remembered.get("ssh_password_auth", True):
        vars_["ssh_keys_only"].set(True)
    tabs.add(general, text="General")
    tabs.add(wifi, text="Wi-Fi")
    tabs.add(services, text="Services")

    # --- footer -------------------------------------------------------------------------
    footer = ttk.Frame(root, padding=(24, 12, 24, 20))
    footer.pack(fill="x")
    footer.columnconfigure(0, weight=1)
    status_var = tk.StringVar(value="Choose an image and a card.")
    ttk.Label(footer, textvariable=status_var, style="Sub.TLabel").grid(row=0, column=0, sticky="w")
    bar = ttk.Progressbar(footer, mode="determinate", maximum=1000)
    bar.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    cancel_btn = ttk.Button(footer, text="Cancel", state="disabled", command=lambda: state["cancel"].set())
    cancel_btn.grid(row=1, column=1, padx=(12, 0), pady=(8, 0))
    write_btn = ttk.Button(footer, text="Write", style="Accent.TButton", command=lambda: start_write())
    write_btn.grid(row=1, column=2, padx=(12, 0), pady=(8, 0))

    def refresh_write_button():
        ready = state["image"] is not None and bool(state["devices"]) and not state["busy"]
        write_btn.configure(state="normal" if ready else "disabled")

    def collect() -> Settings:
        if vars_["password"].get() != vars_["password2"].get():
            raise ImagerError("The two passwords differ.")
        keys = [ln.strip() for ln in keys_text.get("1.0", "end").splitlines() if ln.strip()]
        s = Settings(user=vars_["user"].get().strip(), password=vars_["password"].get(), hostname=vars_["hostname"].get().strip(),
                     wifi_ssid=vars_["wifi_ssid"].get(), wifi_password=vars_["wifi_password"].get(),
                     wifi_hidden=vars_["wifi_hidden"].get(), wifi_country=vars_["wifi_country"].get().strip().upper(),
                     timezone=vars_["timezone"].get().strip(), keyboard=vars_["keyboard"].get().strip(),
                     ssh=vars_["ssh"].get(), ssh_password_auth=not vars_["ssh_keys_only"].get(), ssh_keys=keys)
        s.validate()
        return s

    def run_task(label, fn, on_done=None):
        """Run fn(progress, cancel) on a thread; progress and the result come back through the Tk loop."""
        state["busy"] = True
        state["cancel"] = threading.Event()
        cancel_btn.configure(state="normal")
        refresh_write_button()
        updates: list = []

        def progress(stage, fraction):
            updates.append(("progress", stage, fraction))

        def body_():
            try:
                updates.append(("done", fn(progress, state["cancel"]), None))
            except ImagerError as exc:
                updates.append(("error", str(exc), None))
            except Exception as exc:  # noqa: BLE001
                updates.append(("error", f"{type(exc).__name__}: {exc}", None))

        threading.Thread(target=body_, daemon=True).start()
        status_var.set(f"{label}…")

        def poll():
            while updates:
                kind, a, b = updates.pop(0)
                if kind == "progress":
                    status_var.set(f"{a}  {b * 100:.0f}%")
                    bar["value"] = b * 1000
                    continue
                state["busy"] = False
                cancel_btn.configure(state="disabled")
                refresh_write_button()
                if kind == "error":
                    status_var.set("Stopped.")
                    messagebox.showerror("Genesis Imager", a)
                elif on_done:
                    on_done(a)
                return
            root.after(150, poll)

        poll()

    def start_write():
        try:
            settings = collect()
        except ImagerError as exc:
            messagebox.showerror("Check the settings", str(exc))
            return
        device = state["devices"][device_box.current()] if device_box.current() >= 0 else None
        if device is None:
            return
        if not messagebox.askyesno("Erase the card?", f"Everything on\n\n{device.label()}\n\nwill be erased and replaced with Genesis OS.\n\nContinue?",
                                   icon="warning"):
            return
        save_remembered(settings)
        image = state["image"]

        def job(prog, cancel):
            if is_admin():
                return asdict(write_image(image, DeviceTarget(device), settings, progress=prog, cancel=cancel))
            data = {"image": str(image), "device": asdict(device), "verify": True,
                    "settings": {**asdict(settings), "password": "", "password_hash": sha512_crypt(settings.password)}}
            return run_privileged_worker(data, prog, cancel)

        def done(result):
            status_var.set(f"Done: {human(result['bytes_written'])} written" + (" and verified." if result.get("verified") else "."))
            bar["value"] = 1000
            messagebox.showinfo("Genesis Imager", f"The card is ready. Put it in the Pi and power it on.\n\n"
                                f"First boot logs in as {settings.user}"
                                + (f" and joins {settings.wifi_ssid}" if settings.wifi_ssid else "") + ".")
            refresh_devices()

        run_task("Writing", job, on_done=done)

    refresh_devices()
    root.mainloop()
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if argv:
        return cli(argv)
    if platform.system() == "Windows" and not is_admin() and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, "", None, 1)
        return 0
    try:
        return gui()
    except ImportError:
        print("Tk is not available; use the command line (--help).", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
