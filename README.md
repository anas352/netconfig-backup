# 🗄️ netconfig-backup

**Back up every router and switch in parallel, keep a git history of every config change, and get a Telegram alert when something changes.**

![tests](https://github.com/anas352/netconfig-backup/actions/workflows/tests.yml/badge.svg)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![netmiko](https://img.shields.io/badge/built%20with-Netmiko-orange)

```
$ python netbackup.py --diff
===== edge-rtr1 =====
--- before
+++ after
@@ -42,6 +42,7 @@
 interface GigabitEthernet0/2
- description to-ISP-A
+ description to-ISP-B
+ ip access-group EDGE-IN in

❌ srx-dc           failed      15.0s  NetmikoTimeoutException: TCP connection to device failed.
✏️  edge-rtr1        changed      3.1s  +2 / -1 lines
✅ core-sw1         unchanged    2.4s
🆕 mikrotik-office  new          1.8s  212 lines

4 devices: 1 new, 1 changed, 1 unchanged, 1 failed
📝 git: backup 2026-11-09 02:00: edge-rtr1, mikrotik-office
```

## ✨ Why it's useful
- **Multi-vendor**: Cisco IOS/XE/NX-OS/ASA, MikroTik RouterOS, Juniper, Arista, Huawei, FortiGate and HP
- **Parallel**: backs up 100 devices about as fast as 10 (`--workers`)
- **No fake diffs**: strips lines that change on every run (`! Last configuration change`, RouterOS export timestamp, `ntp clock-period`…)
- **Git history**: `git log -p backups/edge-rtr1.cfg` shows who changed what and when
- **No passwords in files**: credentials come from environment variables, including per-device accounts
- **Alerts**: optional Telegram message when a config changes or a device fails
- **Automation-friendly**: `--json` output, and a non-zero exit code if any device fails

## 🚀 Usage
```bash
pip install -r requirements.txt
cp inventory.example.yaml inventory.yaml     # add your devices
export NET_USERNAME=backup NET_PASSWORD='...'
python netbackup.py
```

| Option | Description |
|---|---|
| `-i inventory.yaml` | Inventory file |
| `-o backups` | Output folder (becomes a git repo) |
| `-w 10` | Parallel sessions |
| `-l core` | Only devices whose name contains `core` |
| `--diff` | Print what changed |
| `--json` | JSON output for other tools |
| `--no-git` | Skip the git commit |

### Inventory
```yaml
defaults:
  device_type: cisco_ios
devices:
  - name: core-sw1
    host: 10.0.0.2
  - name: mikrotik-office
    host: 192.168.88.1
    device_type: mikrotik_routeros
    password_env: MT_PASSWORD     # different credentials for this device
```

### Schedule it (every night at 02:00)
```cron
0 2 * * * cd /opt/netconfig-backup && set -a && . ./.env && python3 netbackup.py >> backup.log 2>&1
```

## 🔒 Security tips
- Use a **read-only** account on the devices (Cisco `privilege 15 view`, or a MikroTik `read` group).
- RouterOS v7 `/export` hides secrets by default. Use `command: /export show-sensitive` only if you really need them.
- Keep the `backups/` repo **private**, because configs reveal your network.

## 🧪 Tests
```bash
python -m unittest -v     # no real devices needed: Netmiko is mocked
```

---

## 🇸🇦 بالعربي
أداة بتاخد **نسخة احتياطية** من إعدادات كل الراوترات والسويتشات **بنفس الوقت**، وبتحفظها بـ **git**،
فبتعرف شو تغيّر وإيمتى. وبتبعتلك **تنبيه على تيليجرام** إذا تغيّر شي أو فشل جهاز.
بتدعم Cisco وMikroTik وJuniper وHuawei وFortiGate وغيرهم، والباسووردات بتنقرأ من متغيرات البيئة، مش من الملفات.

MIT © 2026 Anas
