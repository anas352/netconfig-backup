import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import netbackup  # noqa: E402

CISCO = """Building configuration...

Current configuration : 1234 bytes
!
! Last configuration change at 10:01:02 UTC Mon Nov 9 2026 by admin
! NVRAM config last updated at 10:01:05 UTC Mon Nov 9 2026
!
hostname core-sw1   \r
ntp clock-period 17179870
interface Gi0/1
 description uplink
"""

MIKROTIK = """# 2026-11-09 10:00:00 by RouterOS 7.16
# software id = ABCD-1234
/ip address add address=192.168.88.1/24 interface=bridge
"""


class CleanTests(unittest.TestCase):
    def test_cisco_noise_removed(self):
        out = netbackup.clean(CISCO)
        self.assertNotIn("Last configuration change", out)
        self.assertNotIn("Current configuration", out)
        self.assertNotIn("clock-period", out)
        self.assertIn("hostname core-sw1\n", out)  # trailing spaces and \r stripped
        self.assertTrue(out.startswith("!"))

    def test_mikrotik_header_removed(self):
        out = netbackup.clean(MIKROTIK)
        self.assertNotIn("by RouterOS", out)
        self.assertIn("software id", out)

    def test_clean_is_stable(self):
        once = netbackup.clean(CISCO)
        self.assertEqual(once, netbackup.clean(once))


class BackupFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.dev = netbackup.Device("sw/1", "10.0.0.2", "cisco_ios")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, config):
        with mock.patch.object(netbackup, "fetch", return_value=config):
            return netbackup.backup_one(self.dev, self.out, 5, False)

    def test_new_changed_unchanged(self):
        base = "hostname a\ninterface Gi0/1\n description one\n"
        self.assertEqual(self.run_with(base).status, "new")
        self.assertTrue((self.out / "sw_1.cfg").exists())  # unsafe characters replaced
        self.assertEqual(self.run_with(base).status, "unchanged")
        r = self.run_with(base.replace("one", "two") + " shutdown\n")
        self.assertEqual((r.status, r.added, r.removed), ("changed", 2, 1))

    def test_failure_is_reported_not_raised(self):
        with mock.patch.object(netbackup, "fetch", side_effect=TimeoutError("timed out")):
            r = netbackup.backup_one(self.dev, self.out, 5, False)
        self.assertEqual(r.status, "failed")
        self.assertIn("timed out", r.message)

    def test_git_commit(self):
        self.run_with("hostname a\ninterface x\n")
        msg = netbackup.commit(self.out, [netbackup.Result(self.dev, True, "new")])
        self.assertIn("sw/1", msg)
        self.assertIsNone(netbackup.commit(self.out, []))  # nothing new to commit


class InventoryTests(unittest.TestCase):
    def test_env_credentials(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        with tempfile.TemporaryDirectory() as d:
            inv = Path(d) / "inv.yaml"
            inv.write_text("defaults: {device_type: cisco_ios}\ndevices:\n  - {name: r1, host: 1.1.1.1}\n"
                           "  - {name: mt, host: 2.2.2.2, device_type: mikrotik_routeros, password_env: MT_PW}\n")
            with mock.patch.dict(os.environ, {"NET_USERNAME": "u", "NET_PASSWORD": "p", "MT_PW": "mt"}):
                r1, mt = netbackup.load_inventory(inv)
        self.assertEqual((r1.username, r1.password, r1.device_type), ("u", "p", "cisco_ios"))
        self.assertEqual((mt.password, mt.device_type), ("mt", "mikrotik_routeros"))


if __name__ == "__main__":
    unittest.main()
