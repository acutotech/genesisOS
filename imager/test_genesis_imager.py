"""Tests for Genesis Imager: python3 -m unittest discover -s imager -v

Everything runs on synthetic disk images built here, so the tests need no
device, no root and no tools beyond Python (openssl, if present, is used as a
second opinion on the password hash).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import lzma
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import genesis_imager as g  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SECTOR = g.SECTOR
BOOT_START = 8192            # sectors, as pi-gen lays out the boot partition
BOOT_SECTORS = 16384         # 8 MiB: small enough to be quick, larger than one chunk + hold-back
ROOT_SECTORS = 4096


def mbr(entries) -> bytes:
    sector = bytearray(SECTOR)
    for i, (ptype, start, count) in enumerate(entries):
        sector[446 + 16 * i + 4] = ptype
        sector[446 + 16 * i + 8: 446 + 16 * i + 16] = struct.pack("<II", start, count)
    sector[510:512] = b"\x55\xaa"
    return bytes(sector)


def synthetic_image(placement=None, *, extra_magic_at=None, ptype=0x0C) -> bytes:
    """An MBR disk with a 'FAT' partition holding the setup area and a second partition.

    placement maps setup-area part number -> sector inside the boot partition
    (default: contiguous at sector 2000, the way a fresh FAT file usually lies).
    """
    rng = hashlib.sha256(b"genesis").digest()
    size = (BOOT_START + BOOT_SECTORS + ROOT_SECTORS) * SECTOR
    filler = (rng * (size // len(rng) + 1))[:size]
    img = bytearray(filler)
    img[0:SECTOR] = mbr([(ptype, BOOT_START, BOOT_SECTORS), (0x83, BOOT_START + BOOT_SECTORS, ROOT_SECTORS)])
    ph = g.placeholder()
    if placement is None:
        placement = {n: 2000 + n - 1 for n in range(1, g.SETUP_PARTS + 1)}
    for n, sector in placement.items():
        off = (BOOT_START + sector) * SECTOR
        img[off: off + SECTOR] = ph[(n - 1) * SECTOR: n * SECTOR]
    if extra_magic_at is not None:
        img[extra_magic_at: extra_magic_at + SECTOR] = ph[:SECTOR]
    return bytes(img)


def settings(**kw) -> g.Settings:
    base = dict(user="tester", password="correct horse", hostname="nodobox", wifi_ssid="NodoAI",
                wifi_password="wifi-password-1", wifi_country="IN", timezone="Asia/Kolkata", keyboard="us")
    base.update(kw)
    return g.Settings(**base)


def parse_payload(data: bytes) -> dict:
    out = {}
    for line in data.decode().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


class RecordingTarget(g.FileTarget):
    """A file target that records the order of writes and reads."""

    def __init__(self, path):
        super().__init__(path)
        self.ops = []

    def write_at(self, offset, data):
        self.ops.append(("write", offset, len(data)))
        super().write_at(offset, data)

    def read_at(self, offset, size):
        self.ops.append(("read", offset, size))
        return super().read_at(offset, size)


class CorruptingTarget(g.FileTarget):
    """Reads back one flipped byte at `bad_offset`, like failing media."""

    bad_offset = 0

    def read_at(self, offset, size):
        data = bytearray(super().read_at(offset, size))
        if offset <= self.bad_offset < offset + len(data):
            data[self.bad_offset - offset] ^= 0xFF
        return bytes(data)


class Sha512CryptTest(unittest.TestCase):
    # glibc's own test vectors (crypt/sha512c-test.c)
    VECTORS = [
        ("saltstring", 5000, "Hello world!",
         "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1"),
        ("saltstringsaltstring", 10000, "Hello world!",
         "$6$rounds=10000$saltstringsaltst$OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/YTBmSK6H9qs/y3RnOaw5v."),
        ("anotherlongsaltstring", 1400, "a very much longer text to encrypt.  This one even stretches over morethan one line.",
         "$6$rounds=1400$anotherlongsalts$POfYwTEok97VWcjxIiSOjiykti.o/pQs.wPvMxQ6Fm7I6IoYN3CmLs66x9t0oSwbtEW7o7UmJEiDwGqd8p4ur1"),
        ("short", 77777, "we have a short salt string but not a short password",
         "$6$rounds=77777$short$WuQyW2YR.hBNpjjRhpYD/ifIw05xdfeEyQoMxIXbkvr0gge1a1x3yRULJ5CCaUeOxFmtlcGZelFl5CxtgfiAc0"),
        ("asaltof16chars..", 123456, "a short string",
         "$6$rounds=123456$asaltof16chars..$BtCwjqMJGx5hrJhZywWvt0RLE8uZ4oPwcelCjmw2kSYu.Ec6ycULevoBK25fs2xXgMNrCzIMVcgEJAstJeonj1"),
        ("roundstoolow", 10, "the minimum number is still observed",
         "$6$rounds=1000$roundstoolow$kUMsbe306n21p9R.FRkW3IGn.S9NPN0x50YhH1xhLsPuWGsUSklZt58jaTfF4ZEQpyUNGc0dqbpBYYBaHHrsX."),
    ]

    def test_glibc_vectors(self):
        for salt, rounds, pw, expected in self.VECTORS:
            with self.subTest(salt=salt):
                self.assertEqual(g.sha512_crypt(pw, salt, rounds), expected)

    def test_default_is_salted_and_strong(self):
        a, b = g.sha512_crypt("pw"), g.sha512_crypt("pw")
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^\$6\$rounds=100000\$[./0-9A-Za-z]{16}\$[./0-9A-Za-z]{86}$")

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_matches_openssl(self):
        passwords = ["nodoai pass", "x" * 130, "a"]
        if sys.platform != "win32":       # Windows hands openssl its arguments in the ANSI code page
            passwords.append("ünïcødé ✓")
        for pw in passwords:
            ours = g.sha512_crypt(pw, "Genesis.Salt/42", 100000)
            ref = subprocess.run(["openssl", "passwd", "-6", "-salt", "rounds=100000$Genesis.Salt/42", pw],
                                 capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(ours, ref)


class SetupAreaTest(unittest.TestCase):
    def test_placeholder_layout(self):
        ph = g.placeholder()
        self.assertEqual(len(ph), 16 * 512)
        for n in range(1, 17):
            part = ph[(n - 1) * 512: n * 512]
            self.assertTrue(part.startswith(f"#GENESIS-SETUP-AREA:v1:{n:02d}:16:".encode()))
            self.assertTrue(part.endswith(b"\n"))
        self.assertFalse(ph.startswith(b"GENESIS_SETUP=1"))

    def test_committed_placeholder_is_current(self):
        committed = REPO / "stage-custom" / "14-first-boot" / "files" / "genesis-setup.txt"
        self.assertEqual(committed.read_bytes(), g.placeholder(),
                         "regenerate with: python3 imager/genesis_imager.py --write-placeholder " + str(committed))

    def test_payload_contents(self):
        s = settings(wifi_ssid=" Café \"5G\"", wifi_hidden=True, ssh_keys=["ssh-ed25519 AAAAkey me@pc"],
                     ssh_password_auth=False, keyboard_variant="extd")
        data = s.payload()
        self.assertEqual(len(data), g.SETUP_SIZE)
        self.assertTrue(data.startswith(b"GENESIS_SETUP=1\n"))
        fields = parse_payload(data)
        self.assertEqual(fields["END"], "1")
        self.assertEqual(fields["USER"], "tester")
        self.assertEqual(base64.b64decode(fields["WIFI_SSID_B64"]).decode(), " Café \"5G\"")
        self.assertEqual(base64.b64decode(fields["WIFI_PSK_B64"]).decode(), "wifi-password-1")
        self.assertEqual(base64.b64decode(fields["SSH_KEYS_B64"]).decode(), "ssh-ed25519 AAAAkey me@pc")
        self.assertEqual(fields["WIFI_HIDDEN"], "1")
        self.assertEqual(fields["SSH_PASSWORD_AUTH"], "0")
        self.assertEqual(fields["KEYBOARD_VARIANT"], "extd")
        self.assertNotIn(b"correct horse", data)
        self.assertNotIn(b"wifi-password-1", data)
        self.assertTrue(fields["PASSWORD_HASH"].startswith("$6$rounds=100000$"))
        self.assertTrue(data.rstrip(b"\n").endswith(b"END=1"))

    def test_password_hash_verifies(self):
        fields = parse_payload(settings(password="Open Sesame 9").payload())
        h = fields["PASSWORD_HASH"]
        salt = h.split("$")[3]
        self.assertEqual(g.sha512_crypt("Open Sesame 9", salt, 100000), h)

    def test_precomputed_hash_used_as_is(self):
        h = g.sha512_crypt("pw12", "abc", 5000)
        fields = parse_payload(settings(password="", password_hash=h).payload())
        self.assertEqual(fields["PASSWORD_HASH"], h)

    def test_no_wifi(self):
        fields = parse_payload(settings(wifi_ssid="", wifi_password="").payload())
        self.assertNotIn("WIFI_SSID_B64", fields)
        self.assertNotIn("WIFI_PSK_B64", fields)

    def test_validation(self):
        bad = [
            dict(user="Root"), dict(user="root"), dict(user="1abc"), dict(user="a" * 33), dict(user="bad name"),
            dict(password=""), dict(password="abc"),
            dict(hostname="Bad_Host"), dict(hostname="-x"),
            dict(wifi_ssid="x" * 33), dict(wifi_ssid="ééééééééééééééééé"), dict(wifi_password="short"),
            dict(wifi_password="x" * 64), dict(wifi_country="india"), dict(timezone="Asia/Kolkata; rm"),
            dict(timezone="../etc"), dict(keyboard="US!"), dict(keyboard_variant="a b"), dict(ssh_keys=["not a key"]),
        ]
        for kw in bad:
            with self.subTest(**{k: str(v)[:20] for k, v in kw.items()}):
                with self.assertRaises(g.ImagerError):
                    settings(**kw).validate()
        settings(wifi_password="").validate()                       # open network
        settings(wifi_password="ab" * 32).validate()                # 64 hex digits: a raw PSK
        settings(hostname="").validate()                            # keep the image's host name

    def test_payload_size_limit(self):
        keys = [f"ssh-ed25519 {'A' * 400} key{i}" for i in range(30)]
        with self.assertRaises(g.ImagerError):
            settings(ssh_keys=keys).payload()


class ImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-imager-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, data: bytes) -> Path:
        path = self.tmp / name
        if name.endswith(".xz"):
            data = lzma.compress(data, preset=0)
        elif name.endswith(".gz"):
            data = gzip.compress(data, compresslevel=1)
        elif name.endswith(".zip"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("README.txt", "readme")
                zf.writestr("genesis-os.img", data)
            data = buf.getvalue()
        path.write_bytes(data)
        return path

    def check_output(self, original: bytes, out: Path, s: g.Settings, placement=None):
        written = out.read_bytes()
        self.assertEqual(len(written), len(original))
        if placement is None:
            placement = {n: 2000 + n - 1 for n in range(1, 17)}
        payload = bytearray()
        expected = bytearray(original)
        for n in range(1, 17):
            off = (BOOT_START + placement[n]) * SECTOR
            payload += written[off: off + SECTOR]
            expected[off: off + SECTOR] = written[off: off + SECTOR]
        self.assertEqual(bytes(expected), written, "only the setup area may change")
        self.assertTrue(payload.startswith(b"GENESIS_SETUP=1\n"))
        fields = parse_payload(bytes(payload))
        self.assertEqual(fields["USER"], s.user)
        self.assertEqual(fields["END"], "1")

    def test_scan_finds_setup_area(self):
        img = self.write("a.img", synthetic_image())
        layout = g.scan_image(img)
        self.assertEqual(layout.boot_start, BOOT_START * SECTOR)
        self.assertEqual(layout.image_end, (BOOT_START + BOOT_SECTORS + ROOT_SECTORS) * SECTOR)
        self.assertEqual(sorted(layout.setup_offsets), list(range(1, 17)))
        self.assertEqual(layout.setup_offsets[1], (BOOT_START + 2000) * SECTOR)

    def test_write_every_format(self):
        original = synthetic_image()
        s = settings()
        for name in ("plain.img", "comp.img.xz", "comp.img.gz", "bundle.zip"):
            with self.subTest(name=name):
                src = self.write(name, original)
                out = self.tmp / f"out-{name}.img"
                res = g.write_image(src, g.FileTarget(out), s)
                self.assertTrue(res.verified)
                self.assertEqual(res.bytes_written, len(original))
                self.assertEqual(res.sha256, hashlib.sha256(out.read_bytes()).hexdigest())
                self.check_output(original, out, s)

    def test_fragmented_and_out_of_order_setup_area(self):
        # the FAT file split over clusters far apart, second half before the first
        placement = {n: (9000 + n) if n > 8 else (300 + n) for n in range(1, 17)}
        placement.update({9: 12000, 10: 12001})
        original = synthetic_image(placement)
        src = self.write("frag.img", original)
        out = self.tmp / "frag-out.img"
        s = settings()
        g.write_image(src, g.FileTarget(out), s)
        self.check_output(original, out, s, placement)

    def test_setup_area_across_chunk_boundary(self):
        # The boot partition starts at 4 MiB; the write pass reads 4 MiB chunks from
        # offset 0, the scan reads them from offset 512. Straddle a boundary of each.
        for name, first_sector in (("write", 2 * g.CHUNK // SECTOR - 1), ("scan", (g.CHUNK + SECTOR) // SECTOR + g.CHUNK // SECTOR - 1)):
            with self.subTest(boundary=name):
                placement = {n: first_sector - BOOT_START + n - 1 for n in range(1, 17)}
                self.assertLess(max(placement.values()), BOOT_SECTORS)
                original = synthetic_image(placement)
                src = self.write(f"edge-{name}.img.xz", original)
                out = self.tmp / f"edge-{name}-out.img"
                s = settings()
                g.write_image(src, g.FileTarget(out), s)
                self.check_output(original, out, s, placement)

    def test_magic_outside_boot_partition_or_unaligned_is_ignored(self):
        root_off = (BOOT_START + BOOT_SECTORS + 10) * SECTOR
        img = self.write("x.img", synthetic_image(extra_magic_at=root_off))
        self.assertEqual(len(g.scan_image(img).setup_offsets), 16)
        unaligned = bytearray(synthetic_image())
        off = (BOOT_START + 5000) * SECTOR + 7
        unaligned[off: off + 64] = g.placeholder()[:64]
        self.assertEqual(len(g.scan_image(self.write("u.img", bytes(unaligned))).setup_offsets), 16)

    def test_duplicate_setup_area_refused(self):
        dup = (BOOT_START + 6000) * SECTOR
        img = self.write("dup.img", synthetic_image(extra_magic_at=dup))
        with self.assertRaisesRegex(g.ImagerError, "two setup areas"):
            g.scan_image(img)

    def test_incomplete_or_missing_setup_area(self):
        placement = {n: 2000 + n for n in range(1, 16)}       # part 16 missing
        with self.assertRaisesRegex(g.ImagerError, "incomplete"):
            g.scan_image(self.write("inc.img", synthetic_image(placement)))
        with self.assertRaisesRegex(g.ImagerError, "no Genesis setup area"):
            g.scan_image(self.write("none.img", synthetic_image({})))

    def test_not_a_pi_image(self):
        with self.assertRaisesRegex(g.ImagerError, "not a disk image"):
            g.scan_image(self.write("junk.img", b"\0" * 4096))
        with self.assertRaisesRegex(g.ImagerError, "GPT"):
            g.scan_image(self.write("gpt.img", synthetic_image(ptype=0xEE)))
        with self.assertRaisesRegex(g.ImagerError, "not a FAT boot partition"):
            g.scan_image(self.write("ext.img", synthetic_image(ptype=0x83)))

    def test_partition_table_written_last_and_body_verified_before(self):
        src = self.write("order.img", synthetic_image())
        target = RecordingTarget(self.tmp / "order-out.img")
        g.write_image(src, target, settings())
        writes = [op for op in target.ops if op[0] == "write"]
        self.assertEqual(writes[-1], ("write", 0, g.HOLD_BACK))
        self.assertTrue(all(off >= g.HOLD_BACK for _, off, _ in writes[:-1]))
        mbr_write = target.ops.index(("write", 0, g.HOLD_BACK))
        body_reads = [i for i, op in enumerate(target.ops) if op[0] == "read" and op[1] >= g.HOLD_BACK]
        self.assertTrue(body_reads and max(body_reads) < mbr_write, "the body is verified before the partition table exists")
        self.assertEqual(target.ops[-1], ("read", 0, g.HOLD_BACK))

    def test_verification_catches_bad_media(self):
        src = self.write("v.img", synthetic_image())
        for bad in (100, g.HOLD_BACK + 12345, (BOOT_START + BOOT_SECTORS) * SECTOR + 7):
            with self.subTest(offset=bad):
                target = CorruptingTarget(self.tmp / "v-out.img")
                target.bad_offset = bad
                with self.assertRaisesRegex(g.ImagerError, "Verification failed"):
                    g.write_image(src, target, settings())

    def test_cancel(self):
        src = self.write("c.img", synthetic_image())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(g.ImagerError, "Cancelled"):
            g.write_image(src, g.FileTarget(self.tmp / "c-out.img"), settings(), cancel=cancel)

    def test_card_too_small(self):
        src = self.write("s.img", synthetic_image())
        dev = g.Device(str(self.tmp / "small.bin"), 1024 * 1024, "Tiny card", True, False)
        with self.assertRaisesRegex(g.ImagerError, "too small"):
            g.write_image(src, g.DeviceTarget(dev), settings())

    def test_invalid_settings_write_nothing(self):
        src = self.write("i.img", synthetic_image())
        out = self.tmp / "i-out.img"
        with self.assertRaises(g.ImagerError):
            g.write_image(src, g.FileTarget(out), settings(password=""))
        self.assertFalse(out.exists())

    def test_cli_output(self):
        original = synthetic_image()
        src = self.write("cli.img.xz", original)
        out = self.tmp / "cli-out.img"
        pwfile = self.tmp / "pw.txt"
        pwfile.write_text("cli password\n")
        keyfile = self.tmp / "id.pub"
        keyfile.write_text("# comment\nssh-ed25519 AAAAcli user@pc\n")
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = g.cli(["--image", str(src), "--output", str(out), "--user", "cliuser", "--password-file", str(pwfile),
                        "--wifi-ssid", "", "--hostname", "box2", "--ssh-key-file", str(keyfile), "--ssh-keys-only"])
        self.assertEqual(rc, 0)
        s = settings(user="cliuser")
        self.check_output(original, out, s)
        data = out.read_bytes()
        off = (BOOT_START + 2000) * SECTOR
        fields = parse_payload(data[off: off + g.SETUP_SIZE])
        self.assertEqual(fields["HOSTNAME"], "box2")
        self.assertNotIn("WIFI_SSID_B64", fields)
        self.assertEqual(fields["SSH_PASSWORD_AUTH"], "0")
        salt = fields["PASSWORD_HASH"].split("$")[3]
        self.assertEqual(g.sha512_crypt("cli password", salt, 100000), fields["PASSWORD_HASH"])

    def test_cli_rejects_bad_settings(self):
        src = self.write("bad.img", synthetic_image())
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err, mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = g.cli(["--image", str(src), "--output", str(self.tmp / "o.img"), "--user", "Bad User",
                        "--password", "pw1234", "--wifi-ssid", ""])
        self.assertEqual(rc, 1)
        self.assertIn("User name", err.getvalue())

    def test_worker_round_trip(self):
        # what the window hands the privileged helper: settings with only the hash
        s = settings()
        job_settings = {**g.asdict(s), "password": "", "password_hash": g.sha512_crypt(s.password)}
        clone = g.Settings(**json.loads(json.dumps(job_settings)))
        self.assertEqual(parse_payload(clone.payload())["PASSWORD_HASH"], job_settings["password_hash"])


class DeviceListTest(unittest.TestCase):
    def run_mock(self, outputs):
        def fake_run(cmd, *a, **kw):
            key = cmd[0] if cmd[0] != "diskutil" else " ".join(cmd[:3])
            out = outputs[key] if not callable(outputs[key]) else outputs[key](cmd)
            return subprocess.CompletedProcess(cmd, 0, out, "" if kw.get("text") else b"")
        return mock.patch("subprocess.run", side_effect=fake_run)

    def test_linux(self):
        lsblk = {"blockdevices": [
            {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 512110190592, "model": "Samsung SSD", "vendor": None,
             "tran": "nvme", "rm": False, "hotplug": False, "type": "disk", "ro": False, "mountpoint": None,
             "children": [{"name": "nvme0n1p2", "mountpoint": "/"}]},
            {"name": "sdb", "path": "/dev/sdb", "size": 31914983424, "model": "SD Card Reader", "vendor": "Generic ",
             "tran": "usb", "rm": True, "hotplug": True, "type": "disk", "ro": False, "mountpoint": None,
             "children": [{"name": "sdb1", "mountpoint": "/media/me/bootfs"}]},
            {"name": "sdc", "path": "/dev/sdc", "size": 2000398934016, "model": "Backup USB HDD", "vendor": "WD",
             "tran": "usb", "rm": False, "hotplug": True, "type": "disk", "ro": False, "mountpoint": None},
            {"name": "sda", "path": "/dev/sda", "size": 1000204886016, "model": "Data HDD", "vendor": "ATA",
             "tran": "sata", "rm": False, "hotplug": False, "type": "disk", "ro": False, "mountpoint": None},
            {"name": "sr0", "path": "/dev/sr0", "size": 1073741312, "model": "DVD", "tran": "sata", "rm": True,
             "type": "rom", "ro": False},
        ]}
        with mock.patch("platform.system", return_value="Linux"), self.run_mock({"lsblk": json.dumps(lsblk)}):
            removable = g.list_devices()
            everything = g.list_devices(show_all=True)
        self.assertEqual([d.path for d in removable], ["/dev/sdb", "/dev/sdc"])
        self.assertEqual(removable[0].description, "Generic SD Card Reader")
        self.assertEqual([d.path for d in everything], ["/dev/sdb", "/dev/sdc", "/dev/sda"])   # never the system disk

    def test_windows(self):
        disks = [
            {"Number": 0, "FriendlyName": "NVMe SSD", "Size": 512110190592, "BusType": "NVMe", "IsBoot": True,
             "IsSystem": True, "IsReadOnly": False},
            {"Number": 2, "FriendlyName": "Mass Storage Device", "Size": 31914983424, "BusType": "USB", "IsBoot": False,
             "IsSystem": False, "IsReadOnly": False},
            {"Number": 3, "FriendlyName": "Locked card", "Size": 31914983424, "BusType": "SD", "IsBoot": False,
             "IsSystem": False, "IsReadOnly": True},
        ]
        with mock.patch("platform.system", return_value="Windows"), self.run_mock({"powershell": json.dumps(disks)}):
            devices = g.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, r"\\.\PhysicalDrive2")
        self.assertEqual(devices[0].ident, "2")
        # PowerShell prints a single object, not a list, when there is one disk
        with mock.patch("platform.system", return_value="Windows"), self.run_mock({"powershell": json.dumps(disks[1])}):
            self.assertEqual(len(g.list_devices()), 1)

    def test_macos(self):
        listing = {"AllDisksAndPartitions": [
            {"DeviceIdentifier": "disk0", "Partitions": [{"MountPoint": "/"}]},
            {"DeviceIdentifier": "disk4", "Partitions": [{"MountPoint": "/Volumes/bootfs"}]},
        ]}
        infos = {
            "disk0": {"Internal": True, "Removable": False, "TotalSize": 500277790720, "MediaName": "APPLE SSD"},
            "disk4": {"Internal": False, "Removable": True, "TotalSize": 31914983424, "MediaName": "SD Card Reader"},
        }
        outputs = {
            "diskutil list -plist": plistlib.dumps(listing),
            "diskutil info -plist": lambda cmd: plistlib.dumps(infos[cmd[3]]),
        }
        with mock.patch("platform.system", return_value="Darwin"), self.run_mock(outputs):
            devices = g.list_devices(show_all=True)
        self.assertEqual([d.path for d in devices], ["/dev/disk4"])
        self.assertEqual(devices[0].ident, "disk4")


class MiscTest(unittest.TestCase):
    def test_human(self):
        self.assertEqual(g.human(512), "512 B")
        self.assertEqual(g.human(31914983424), "31.9 GB")

    def test_remembered_settings_never_hold_passwords(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            with mock.patch.object(g, "settings_file", return_value=tmp / "s" / "settings.json"):
                g.save_remembered(settings(password="TopSecret!", wifi_password="WifiSecret!"))
                text = (tmp / "s" / "settings.json").read_text()
                self.assertNotIn("TopSecret", text)
                self.assertNotIn("WifiSecret", text)
                self.assertEqual(g.load_remembered()["user"], "tester")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
