"""Detect Qualcomm EDL (9008) devices and the system edl tool.
The EDL tool is integrated through the 'edl' pip package (bkerler/edl)."""
import os
import sys
import shutil
import subprocess
import logging
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import get_startup_info

# Qualcomm EDL USB IDs
EDL_VID = "05C6"
EDL_PID = "9008"


def detect_edl_devices():
    """Detect Qualcomm 9008 devices on Windows through WMI.
    Returns a list of dicts: [{"name": ..., "device_id": ..., "port": ...}]
    """
    devices = []
    if sys.platform != 'win32':
        # Linux: check through lsusb
        try:
            result = subprocess.run(
                ["lsusb", "-d", f"{EDL_VID}:{EDL_PID}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    devices.append({
                        "name": line.strip(),
                        "device_id": f"{EDL_VID}:{EDL_PID}",
                        "port": ""
                    })
        except Exception:
            pass
        return devices

    # Windows: use PowerShell + WMI
    try:
        ps_cmd = (
            "Get-CimInstance -ClassName Win32_PnPEntity | "
            f"Where-Object {{ $_.DeviceID -like '*VID_{EDL_VID}*PID_{EDL_PID}*' }} | "
            "Select-Object Name, DeviceID, Status | "
            "ForEach-Object { $_.Name + '|' + $_.DeviceID + '|' + $_.Status }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, encoding='utf-8', errors='ignore',
            startupinfo=get_startup_info(), timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                parts = line.split("|")
                if len(parts) >= 2:
                    name = parts[0].strip()
                    device_id = parts[1].strip()
                    # Extract the COM port from the name if present
                    port = ""
                    if "COM" in name:
                        import re
                        m = re.search(r'(COM\d+)', name)
                        if m:
                            port = m.group(1)
                    devices.append({
                        "name": name or "Qualcomm HS-USB QDLoader 9008",
                        "device_id": device_id,
                        "port": port
                    })
    except Exception as e:
        logging.warning(f"Failed to detect EDL device: {e}")

    return devices


def _check_edl_module():
    """Check whether the edlclient module is installed through pip.
    Returns True if it can be imported."""
    try:
        import importlib
        spec = importlib.util.find_spec("edlclient")
        return spec is not None
    except Exception:
        return False


def detect_edl_tool():
    """Find the edl tool path on the system.
    Priority: integrated pip module -> local directory -> PATH.
    Returns (path_or_cmd, source) or (None, None).
    source: 'integrated', 'local', 'path'
    """
    # 1. Priority: integrated pip module (edlclient)
    if _check_edl_module():
        # Use python -m edlclient
        return f"{sys.executable} -m edlclient", "integrated"

    # Resolve the app directory
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    exe_name = "edl.exe" if sys.platform == 'win32' else "edl"

    # 2. edl/ directory in the project
    local_path = os.path.join(app_dir, "edl", exe_name)
    if os.path.isfile(local_path):
        return local_path, "local"

    # 3. edl/ directory containing edl.py
    local_py = os.path.join(app_dir, "edl", "edl.py")
    if os.path.isfile(local_py):
        return local_py, "local"

    # 4. System PATH (edl command)
    found = shutil.which("edl")
    if found:
        return found, "path"

    return None, None


def build_edl_cmd(edl_path):
    """Build a command prefix from edl_path.
    If it is 'python -m edlclient', split it.
    If it is a .py file, run it through python.
    If it is an executable, use it directly.
    """
    if edl_path is None:
        return []
    if "-m edlclient" in edl_path:
        return edl_path.split()
    if edl_path.endswith(".py"):
        return [sys.executable, edl_path]
    return [edl_path]


class EdlDeviceMonitor(QThread):
    """Monitor Qualcomm 9008 devices every 3 seconds."""
    devices_signal = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self._running = True
        self._last = None

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            try:
                devs = detect_edl_devices()
            except Exception:
                devs = []
            if devs != self._last:
                self._last = devs
                self.devices_signal.emit(devs)
            self.msleep(3000)
