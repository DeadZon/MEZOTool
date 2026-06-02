"""Phát hiện thiết bị Qualcomm EDL (9008) và công cụ edl trên hệ thống.
EDL tool được tích hợp sẵn qua pip package 'edl' (bkerler/edl)."""
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
    """Phát hiện thiết bị Qualcomm 9008 trên Windows qua WMI.
    Trả về list of dict: [{"name": ..., "device_id": ..., "port": ...}]
    """
    devices = []
    if sys.platform != 'win32':
        # Linux: kiểm tra qua lsusb
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

    # Windows: dùng PowerShell + WMI
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
                    # Trích xuất COM port từ tên (nếu có)
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
        logging.warning(f"Lỗi detect EDL device: {e}")

    return devices


def _check_edl_module():
    """Kiểm tra xem module edlclient đã cài qua pip chưa.
    Trả về True nếu có thể import được."""
    try:
        import importlib
        spec = importlib.util.find_spec("edlclient")
        return spec is not None
    except Exception:
        return False


def detect_edl_tool():
    """Tìm đường dẫn edl tool trên hệ thống.
    Ưu tiên: pip module tích hợp → local directory → PATH.
    Trả về (path_or_cmd, source) hoặc (None, None).
    source: 'integrated', 'local', 'path'
    """
    # 1. Ưu tiên: pip module tích hợp sẵn (edlclient)
    if _check_edl_module():
        # Dùng python -m edlclient
        return f"{sys.executable} -m edlclient", "integrated"

    # Xác định thư mục app
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    exe_name = "edl.exe" if sys.platform == 'win32' else "edl"

    # 2. Thư mục edl/ trong project
    local_path = os.path.join(app_dir, "edl", exe_name)
    if os.path.isfile(local_path):
        return local_path, "local"

    # 3. Thư mục edl/ chứa edl.py
    local_py = os.path.join(app_dir, "edl", "edl.py")
    if os.path.isfile(local_py):
        return local_py, "local"

    # 4. PATH hệ thống (edl command)
    found = shutil.which("edl")
    if found:
        return found, "path"

    return None, None


def build_edl_cmd(edl_path):
    """Tạo command prefix từ edl_path.
    Nếu là 'python -m edlclient' → split.
    Nếu là .py file → chạy qua python.
    Nếu là executable → dùng trực tiếp.
    """
    if edl_path is None:
        return []
    if "-m edlclient" in edl_path:
        return edl_path.split()
    if edl_path.endswith(".py"):
        return [sys.executable, edl_path]
    return [edl_path]


class EdlDeviceMonitor(QThread):
    """Giám sát thiết bị Qualcomm 9008 mỗi 3 giây."""
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
