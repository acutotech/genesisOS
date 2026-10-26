#!/usr/bin/env python3
"""Unit tests for the Genesis Shell logic that runs without a display.

    python3 scripts/test-shell-logic.py

Runs against the same stubbed PyGObject as check-shell-imports.py, so only code
that does not need GTK is exercised here: parsing i2cdetect output, the sensor
debounce and notification texts, the sensor service fed from a fake bus, and the
desktop label contrast and icon grid. The GTK side is covered by the shell
preview (files/preview.sh), which runs the real shell headless. Used by `make test`.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.join(HERE, "..", "stage-custom", "11-genesis-shell", "files", "genesis-shell")
sys.path.insert(0, os.path.join(HERE, "gi-stub"))
sys.path.insert(0, PACKAGE_DIR)

from genesis_shell import desktop  # noqa: E402
from genesis_shell.services import sensors  # noqa: E402

# `i2cdetect -y 1 0x10 0x3a` with boxes A (0x10), C (0x19) and H (0x2c) on the bus,
# and a driver holding 0x34 (box J): addresses outside the range print as blanks
RANGED_SCAN = """\
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:
10: 10 -- -- -- -- -- -- -- -- 19 -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- -- -- -- 2c -- -- --
30: -- -- -- -- UU -- -- -- -- -- --
40:
50:
60:
70:
"""


class ParseI2cdetect(unittest.TestCase):
    def test_ranged_scan(self):
        self.assertEqual(sensors.parse_i2cdetect(RANGED_SCAN), {0x10, 0x19, 0x2C, 0x34})

    def test_driver_claimed_address_counts(self):
        self.assertIn(0x34, sensors.parse_i2cdetect(RANGED_SCAN))

    def test_empty_bus_and_garbage(self):
        self.assertEqual(sensors.parse_i2cdetect(RANGED_SCAN.replace("10 --", "-- --").replace("19", "--")
                                                 .replace("2c", "--").replace("UU", "--")), set())
        self.assertEqual(sensors.parse_i2cdetect("Error: Could not open file `/dev/i2c-1': No such file\n"), set())
        self.assertEqual(sensors.parse_i2cdetect(""), set())

    def test_full_scan_without_trailing_spaces(self):
        text = "     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f\n10: -- -- -- -- -- 15\n"
        self.assertEqual(sensors.parse_i2cdetect(text), {0x15})

    def test_sensor_addresses(self):
        found = sensors.parse_i2cdetect(RANGED_SCAN)
        boxes = sensors.sensor_addresses(found)
        self.assertEqual(boxes["A"], 0x10)
        self.assertEqual(boxes["C"], 0x19)
        self.assertEqual(boxes["H"], 0x2C)
        self.assertEqual(boxes["J"], 0x34)
        self.assertIsNone(boxes["B"])
        self.assertEqual(len(boxes), 11)
        # a box answering on two of its addresses is one box, on its first address
        self.assertEqual(sensors.sensor_addresses({0x12, 0x11})["A"], 0x11)

    def test_address_table(self):
        self.assertEqual(sensors.LETTERS, tuple("ABCDEFGHIJK"))
        self.assertEqual((sensors.FIRST_ADDR, sensors.LAST_ADDR), (0x10, 0x3A))
        every = [a for addrs in sensors.SENSOR_ADDRS.values() for a in addrs]
        self.assertEqual(len(every), len(set(every)), "two boxes share an address")


class Tracker(unittest.TestCase):
    def letters(self, states):
        return [st.letter for st in states]

    def test_first_scan_is_a_silent_baseline(self):
        t = sensors.SensorTracker()
        connected, disconnected, changed = t.update({0x10, 0x19}, now=1.0)
        self.assertEqual((connected, disconnected), ([], []))
        self.assertTrue(changed)
        self.assertEqual(self.letters(t.connected()), ["A", "C"])

    def test_connect_then_debounced_disconnect(self):
        t = sensors.SensorTracker()
        t.update(set(), now=0.0)
        connected, disconnected, changed = t.update({0x2C}, now=2.0)
        self.assertEqual(self.letters(connected), ["H"])
        self.assertEqual(connected[0].address, 0x2C)
        self.assertEqual(connected[0].since, 2.0)
        self.assertTrue(changed)
        # one lost probe is not a disconnect
        connected, disconnected, changed = t.update(set(), now=4.0)
        self.assertEqual((connected, disconnected, changed), ([], [], False))
        self.assertEqual(self.letters(t.connected()), ["H"])
        # seen again: the miss count starts over
        t.update({0x2C}, now=6.0)
        t.update(set(), now=8.0)
        self.assertEqual(self.letters(t.connected()), ["H"])
        connected, disconnected, changed = t.update(set(), now=10.0)
        self.assertEqual([(st.letter, addr) for st, addr in disconnected], [("H", 0x2C)])
        self.assertTrue(changed)
        self.assertEqual(t.connected(), [])
        self.assertEqual(t.states["H"].last_seen, 6.0)

    def test_address_move_is_not_an_event(self):
        t = sensors.SensorTracker()
        t.update({0x10}, now=0.0)
        connected, disconnected, changed = t.update({0x11}, now=2.0)
        self.assertEqual((connected, disconnected), ([], []))
        self.assertTrue(changed)
        self.assertEqual(t.states["A"].address, 0x11)
        self.assertEqual(t.states["A"].since, 0.0)

    def test_nothing_changes(self):
        t = sensors.SensorTracker()
        t.update({0x10}, now=0.0)
        self.assertEqual(t.update({0x10}, now=2.0), ([], [], False))

    def test_misses_limit_one(self):
        t = sensors.SensorTracker(misses_to_disconnect=0)   # clamped to 1
        t.update({0x10}, now=0.0)
        _c, disconnected, _ch = t.update(set(), now=2.0)
        self.assertEqual(len(disconnected), 1)


class Texts(unittest.TestCase):
    def state(self, letter, address):
        st = sensors.SensorState(letter)
        st.address = address
        return st

    def test_single_events(self):
        events = sensors.describe_events([self.state("A", 0x10)], [(self.state("C", None), 0x19)], 1)
        self.assertEqual([e["kind"] for e in events], ["connected", "disconnected"])
        self.assertEqual(events[0]["summary"], "Sensor A connected")
        self.assertEqual(events[0]["body"], "Sensor box A is on I²C address 0x10 (bus 1).")
        self.assertEqual(events[0]["letters"], ["A"])
        self.assertEqual(events[1]["summary"], "Sensor C disconnected")
        self.assertEqual(events[1]["body"], "Sensor box C (0x19) is no longer on I²C bus 1.")

    def test_aggregated_events(self):
        events = sensors.describe_events([self.state(x, 0x10) for x in "ACH"], [], 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "3 sensors connected")
        self.assertEqual(events[0]["body"], "Sensor boxes A, C and H are on I²C bus 3.")
        self.assertEqual(events[0]["letters"], ["A", "C", "H"])
        gone = sensors.describe_events([], [(self.state("B", None), 0x14), (self.state("K", None), 0x38)], 1)
        self.assertEqual(gone[0]["summary"], "2 sensors disconnected")
        self.assertEqual(gone[0]["body"], "Sensor boxes B and K are no longer on I²C bus 1.")

    def test_nothing(self):
        self.assertEqual(sensors.describe_events([], [], 1), [])

    def test_join_letters(self):
        self.assertEqual(sensors.join_letters(["A"]), "A")
        self.assertEqual(sensors.join_letters(["A", "B"]), "A and B")
        self.assertEqual(sensors.join_letters(["A", "B", "C", "D"]), "A, B, C and D")


class FakeConfig:
    def __init__(self, **sensors_section):
        self.values = {"enabled": "true", "bus": "1", "poll_ms": "2000", "notify": "true"}
        self.values.update(sensors_section)

    def getbool(self, _section, key):
        return self.values[key] == "true"

    def getint(self, _section, key):
        return int(self.values[key])


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = os.path.join(self.tmp.name, "bus")
        self.events = []
        self.notified = 0
        self.saved_env = os.environ.get("GENESIS_SENSORS_FAKE")

    def tearDown(self):
        if self.saved_env is None:
            os.environ.pop("GENESIS_SENSORS_FAKE", None)
        else:
            os.environ["GENESIS_SENSORS_FAKE"] = self.saved_env
        self.tmp.cleanup()

    def service(self, fake=True, **config):
        if fake:
            os.environ["GENESIS_SENSORS_FAKE"] = self.fake
        else:
            os.environ.pop("GENESIS_SENSORS_FAKE", None)
        svc = sensors.SensorService(FakeConfig(**config), run_async=self.fail_run)
        svc.on_events(lambda c, d: self.events.append(([s.letter for s in c], [s.letter for s, _a in d])))
        svc.connect(self.count)
        return svc

    def count(self, *_a):
        self.notified += 1

    def fail_run(self, *_a, **_k):
        raise AssertionError("the fake bus must not run i2cdetect")

    def write(self, text):
        with open(self.fake, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_fake_bus(self):
        svc = self.service()
        self.assertTrue(svc.available)
        self.assertEqual(svc.interval_ms(), 500)
        self.write("0x10")
        svc._poll()
        self.assertEqual(self.events, [])                     # baseline
        self.write("0x10 19 nonsense 0x2c 0x7ff")
        svc._poll()
        self.assertEqual(self.events, [(["C", "H"], [])])
        self.assertEqual([s.letter for s in svc.connected()], ["A", "C", "H"])
        os.remove(self.fake)                                   # a missing file is an empty bus
        svc._poll()
        svc._poll()
        self.assertEqual(self.events[-1], ([], ["A", "C", "H"]))
        self.assertEqual(svc.connected(), [])

    def test_disabled(self):
        svc = self.service(enabled="false")
        self.assertFalse(svc.available)

    def test_poll_interval_bounds(self):
        self.assertEqual(self.service(fake=False, poll_ms="10").interval_ms(), 500)
        self.assertEqual(self.service(fake=False, poll_ms="5000000000").interval_ms(), sensors.MAX_POLL_MS)
        self.assertLess(sensors.MAX_POLL_MS, 2 ** 32)          # GLib.timeout_add takes a guint

    def test_missing_tool_and_device_are_reported(self):
        svc = self.service(fake=False)
        if svc.tool is None:
            self.assertIn("i2c-tools", svc.error)
        svc.tool = "/usr/sbin/i2cdetect"
        svc.bus = 99                                           # /dev/i2c-99: I2C is off
        svc._poll()
        self.assertFalse(svc.available)
        self.assertEqual(svc.error, "/dev/i2c-99 does not exist (I2C is off)")

    def test_stuck_scan_counts_as_missed(self):
        svc = self.service(fake=False)
        svc.tool = "/usr/sbin/i2cdetect"
        svc._scanned(0, RANGED_SCAN)                           # baseline: A, C, H, J
        svc._busy = True                                       # the next scan never returns
        self.assertTrue(svc._scan_overdue())                   # keeps firing while stuck
        self.assertEqual(self.events, [])
        svc._scan_overdue()
        self.assertEqual(self.events, [([], ["A", "C", "H", "J"])])
        self.assertIn("did not finish", svc.error)
        self.assertTrue(svc._busy)                             # no second scan stacked on the stuck one

    def test_unreadable_bus_takes_boxes_off(self):
        svc = self.service(fake=False)
        svc.tool = "/usr/sbin/i2cdetect"
        svc._scanned(0, RANGED_SCAN)                           # baseline: A, C, H, J
        svc._scanned(0, RANGED_SCAN.replace("19", "--"))
        svc._scanned(0, RANGED_SCAN.replace("19", "--"))
        self.assertEqual(self.events, [([], ["C"])])
        svc._scanned(1, "Error: Read failed")
        self.assertTrue(svc.error.startswith("i2cdetect exited with status 1"))
        self.assertEqual(len(self.events), 1)                  # one failure is one miss
        svc._scanned(1, "Error: Read failed")
        self.assertEqual(self.events[-1], ([], ["A", "H", "J"]))
        svc._scanned(0, RANGED_SCAN)
        self.assertEqual(svc.error, "")
        self.assertEqual(self.events[-1], (["A", "C", "H", "J"], []))

    def test_failing_bus_from_the_start_sets_an_empty_baseline(self):
        svc = self.service(fake=False)
        svc.tool = "/usr/sbin/i2cdetect"
        svc._scanned(2, "")
        svc._scanned(2, "")
        self.assertTrue(svc.tracker.baseline)
        svc._scanned(0, RANGED_SCAN)
        self.assertEqual(self.events, [(["A", "C", "H", "J"], [])])


class LabelContrast(unittest.TestCase):
    def contrast(self, a, b):
        hi, lo = max(a, b), min(a, b)
        return (hi + 0.05) / (lo + 0.05)

    def test_luminance(self):
        self.assertAlmostEqual(desktop.relative_luminance(255, 255, 255), 1.0)
        self.assertEqual(desktop.relative_luminance(0, 0, 0), 0.0)
        self.assertAlmostEqual(desktop.relative_luminance(0, 255, 0), 0.7152)

    def test_threshold_is_equal_contrast(self):
        # (L + 0.05) / 0.05 == 1.05 / (L + 0.05)  =>  L = sqrt(0.0525) - 0.05
        self.assertAlmostEqual(desktop.EQUAL_CONTRAST, 0.0525 ** 0.5 - 0.05, places=3)

    def test_tone_picks_the_higher_contrast(self):
        for grey in range(0, 256, 5):
            lum = desktop.relative_luminance(grey, grey, grey)
            tone = desktop.label_tone(lum)
            black, white = self.contrast(lum, 0.0), self.contrast(lum, 1.0)
            self.assertEqual(tone, "dark-text" if black > white else "light-text", grey)
            self.assertGreaterEqual(max(black, white), 4.5)   # either choice reaches WCAG AA

    def test_named_backgrounds(self):
        self.assertEqual(desktop.label_tone(desktop.relative_luminance(0x12, 0x18, 0x24)), "light-text")   # Genesis night
        self.assertEqual(desktop.label_tone(desktop.relative_luminance(0xEC, 0xE8, 0xDE)), "dark-text")    # paper
        self.assertEqual(desktop.label_tone(desktop.relative_luminance(0x15, 0x65, 0xC0)), "light-text")   # deep blue
        self.assertEqual(desktop.label_tone(desktop.relative_luminance(0x1E, 0x88, 0xE5)), "dark-text")    # mid blue: black reads better
        self.assertEqual(desktop.label_tone(desktop.relative_luminance(0xFF, 0xD5, 0x4F)), "dark-text")    # yellow


class FakePixbuf:
    """Just enough of GdkPixbuf.Pixbuf for region_luminance: a grid of solid columns."""

    def __init__(self, width, height, colour_at):
        self.width, self.height, self.colour_at = width, height, colour_at
        self.x0 = self.y0 = 0
        self.requests = []

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def new_subpixbuf(self, x, y, w, h):
        assert 0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= self.width and y + h <= self.height, (x, y, w, h)
        self.requests.append((x, y, w, h))
        sub = FakePixbuf(w, h, self.colour_at)
        sub.x0, sub.y0 = self.x0 + x, self.y0 + y
        return sub

    def scale_simple(self, w, h, _interp):
        # nearest sample of each output cell's centre, in source coordinates
        rows = []
        for row in range(h):
            for col in range(w):
                sx = self.x0 + int((col + 0.5) * self.width / w)
                sy = self.y0 + int((row + 0.5) * self.height / h)
                rows.append(bytes(self.colour_at(sx, sy)))
        small = FakePixbuf(w, h, self.colour_at)
        small.data = b"".join(rows)
        return small

    def get_pixels(self):
        return self.data

    def get_rowstride(self):
        return self.width * 3

    def get_n_channels(self):
        return 3


class RegionLuminance(unittest.TestCase):
    def split(self, x, _y):
        return (255, 255, 255) if x >= 100 else (0, 0, 0)    # black left half, white right half

    def test_no_wallpaper(self):
        self.assertEqual(desktop.region_luminance(None, 0, 0, 10, 10), 0.01)
        self.assertEqual(desktop.region_luminance(None, 0, 0, 10, 10, fallback=0.5), 0.5)

    def test_measures_only_the_label_region(self):
        pix = FakePixbuf(200, 100, self.split)
        self.assertEqual(desktop.region_luminance(pix, 10, 10, 60, 20), 0.0)
        self.assertAlmostEqual(desktop.region_luminance(pix, 120, 10, 60, 20), 1.0)
        self.assertAlmostEqual(desktop.region_luminance(pix, 70, 10, 60, 20), 0.5)

    def test_clamped_to_the_image(self):
        pix = FakePixbuf(200, 100, self.split)
        self.assertAlmostEqual(desktop.region_luminance(pix, 180, 90, 60, 60), 1.0)
        self.assertEqual(desktop.region_luminance(pix, -50, -50, 60, 60), 0.0)
        desktop.region_luminance(pix, 500, 500, 10, 10)       # wholly outside: still a valid 1x1 region
        for x, y, w, h in pix.requests:
            self.assertTrue(x + w <= 200 and y + h <= 100)


class LauncherTrust(unittest.TestCase):
    def test_executable_or_system_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            system = os.path.join(tmp, "share")
            os.makedirs(os.path.join(system, "applications"))
            entry = os.path.join(system, "applications", "app.desktop")
            open(entry, "w").close()
            os.chmod(entry, 0o644)
            desk = os.path.join(tmp, "Desktop")
            os.makedirs(desk)
            plain = os.path.join(desk, "plain.desktop")
            open(plain, "w").close()
            os.chmod(plain, 0o644)
            link = os.path.join(desk, "link.desktop")
            os.symlink(entry, link)
            with mock.patch.object(desktop.GLib, "get_system_data_dirs", lambda: [system + "/"], create=True):
                self.assertFalse(desktop.launcher_trusted(plain))
                os.chmod(plain, 0o755)
                self.assertTrue(desktop.launcher_trusted(plain))
                # a link into a system applications folder is trusted when root owns the target
                self.assertEqual(desktop.launcher_trusted(link), os.stat(entry).st_uid == 0)
                os.chmod(entry, 0o755)
                self.assertTrue(desktop.launcher_trusted(link))
                self.assertFalse(desktop.launcher_trusted(os.path.join(desk, "missing.desktop")))


class Grid(unittest.TestCase):
    def test_one_column_then_wrap(self):
        top, bottom = 60, desktop.EDGE
        rows = (1080 - top - bottom) // desktop.CELL_H
        cells = desktop.grid_positions(rows + 2, 1920, 1080, top, bottom)
        self.assertEqual(cells[0], (desktop.EDGE, top))
        self.assertEqual(cells[1], (desktop.EDGE, top + desktop.CELL_H))
        self.assertEqual(cells[rows], (desktop.EDGE + desktop.CELL_W, top))
        for _x, y in cells:
            self.assertLessEqual(y + desktop.CELL_H, 1080 - bottom)

    def test_tiny_screen_still_lays_out(self):
        self.assertEqual(desktop.grid_positions(2, 100, 50, 40, 40), [(desktop.EDGE, 40), (desktop.EDGE + desktop.CELL_W, 40)])

    def test_nothing(self):
        self.assertEqual(desktop.grid_positions(0, 1920, 1080, 0, 0), [])


class SensorIcons(unittest.TestCase):
    def test_every_box_has_an_icon(self):
        icons = os.path.join(PACKAGE_DIR, "genesis_shell", "icons")
        for letter in sensors.LETTERS:
            path = os.path.join(icons, "sensors", f"sensor-{letter.lower()}.svg")
            with open(path, encoding="utf-8") as fh:
                svg = fh.read()
            self.assertIn(f">{letter}</text>", svg, path)
        self.assertTrue(os.path.isfile(os.path.join(icons, "genesis-sensors-symbolic.svg")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
