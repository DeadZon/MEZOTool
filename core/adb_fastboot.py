import os
import sys
import subprocess
import shutil
import json
import logging
import platform

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

# ── OS-specific executable names ──
IS_WINDOWS = sys.platform == 'win32'
ADB_NAME = "adb.exe" if IS_WINDOWS else "adb"
FASTBOOT_NAME = "fastboot.exe" if IS_WINDOWS else "fastboot"


def get_startup_info():
    """Create startup info to hide the black console window on Windows.
    Returns None on Linux/macOS because it is not needed."""
    if IS_WINDOWS:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    return None


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Failed to save config: {e}")


class ADBFastbootManager:
    def __init__(self):
        self.adb_path = None
        self.fastboot_path = None
        # When running from PyInstaller (frozen), use the executable directory
        if getattr(sys, 'frozen', False):
            self._app_dir = os.path.dirname(sys.executable)
        else:
            self._app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.detect_paths()

    def detect_paths(self):
        """Detect ADB/Fastboot, compatible with Windows/Linux/macOS."""
        config = load_config()
        custom = config.get("platform_tools_path", "")

        # 1. From user configuration
        if custom and os.path.isdir(custom):
            adb = os.path.join(custom, ADB_NAME)
            fb = os.path.join(custom, FASTBOOT_NAME)
            if os.path.isfile(adb) and os.path.isfile(fb):
                self._ensure_executable(adb, fb)
                self.adb_path, self.fastboot_path = adb, fb
                return

        # 2. From local platform-tools directory
        local = os.path.join(self._app_dir, "platform-tools")
        adb = os.path.join(local, ADB_NAME)
        fb = os.path.join(local, FASTBOOT_NAME)
        if os.path.isfile(adb) and os.path.isfile(fb):
            self._ensure_executable(adb, fb)
            self.adb_path, self.fastboot_path = adb, fb
            return

        # 3. From system PATH
        adb_sys = shutil.which("adb")
        fb_sys = shutil.which("fastboot")
        if adb_sys and fb_sys:
            self.adb_path, self.fastboot_path = adb_sys, fb_sys
            return

        self.adb_path = self.fastboot_path = None

    @staticmethod
    def _ensure_executable(*paths):
        """Ensure files are executable on Linux/macOS."""
        if IS_WINDOWS:
            return
        import stat
        for p in paths:
            try:
                st = os.stat(p)
                if not (st.st_mode & stat.S_IXUSR):
                    os.chmod(p, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass

    def is_available(self):
        return self.adb_path is not None and self.fastboot_path is not None

    def run_cmd(self, executable, args, timeout=15):
        if not executable:
            return "", "Tool is not available.", -1
        cmd = [executable] + args
        try:
            r = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore',
                startupinfo=get_startup_info(), timeout=timeout
            )
            return r.stdout.strip(), r.stderr.strip(), r.returncode
        except subprocess.TimeoutExpired:
            return "", "Timeout.", -2
        except Exception as e:
            return "", str(e), -3

    def run_adb(self, args, timeout=15):
        return self.run_cmd(self.adb_path, args, timeout)

    def run_fastboot(self, args, timeout=15):
        return self.run_cmd(self.fastboot_path, args, timeout)

    def get_version(self):
        if not self.is_available():
            return "N/A", "N/A"
        a, _, _ = self.run_adb(["version"])
        f, _, _ = self.run_fastboot(["--version"])
        return (a.splitlines()[0] if a else "N/A"), (f.splitlines()[0] if f else "N/A")

    def get_devices(self):
        devices = []
        if self.adb_path:
            out, _, rc = self.run_adb(["devices"], timeout=5)
            if rc == 0:
                for line in out.splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 2:
                        devices.append({"serial": parts[0], "state": parts[1], "mode": "ADB"})
        if self.fastboot_path:
            out, _, rc = self.run_fastboot(["devices"], timeout=5)
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        devices.append({"serial": parts[0], "state": parts[1], "mode": "FASTBOOT"})
        return devices

    def get_device_info(self, serial, mode):
        info = {"serial": serial, "mode": mode, "name": "N/A", "codename": "N/A",
                "bootloader": "N/A", "slot": "N/A"}
        if mode == "ADB":
            # Get product name and codename
            n, _, _ = self.run_adb(["-s", serial, "shell", "getprop", "ro.product.name"])
            c, _, _ = self.run_adb(["-s", serial, "shell", "getprop", "ro.product.device"])
            if n: info["name"] = n
            if c: info["codename"] = c
            s, _, _ = self.run_adb(["-s", serial, "shell", "getprop", "ro.boot.flash.locked"])
            info["bootloader"] = "Locked 🔒" if s == "1" else ("Unlocked ✔" if s == "0" else "N/A")
        elif mode == "FASTBOOT":
            _, err, _ = self.run_fastboot(["-s", serial, "getvar", "product"])
            for l in (err or "").splitlines():
                if "product:" in l:
                    product_val = l.split(":", 1)[1].strip()
                    info["name"] = product_val
                    info["codename"] = product_val
            _, err, _ = self.run_fastboot(["-s", serial, "getvar", "unlocked"])
            for l in (err or "").splitlines():
                if "unlocked:" in l:
                    v = l.split(":", 1)[1].strip().lower()
                    info["bootloader"] = "Unlocked ✔" if v in ("yes", "true") else "Locked 🔒"
            _, err, _ = self.run_fastboot(["-s", serial, "getvar", "current-slot"])
            for l in (err or "").splitlines():
                if "current-slot:" in l:
                    cs = l.split(":", 1)[1].strip()
                    if cs: info["slot"] = f"Slot {cs.upper()} (A/B)"
        return info

    def reboot_to_bootloader(self, serial, mode):
        """Reboot the device into bootloader. Returns (success, error_msg)."""
        if mode == "ADB":
            _, err, rc = self.run_adb(["-s", serial, "reboot", "bootloader"], timeout=30)
        else:
            _, err, rc = self.run_fastboot(["-s", serial, "reboot", "bootloader"], timeout=30)
        return rc == 0, err
