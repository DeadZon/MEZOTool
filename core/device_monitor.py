import logging
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import ADBFastbootManager


class DeviceMonitor(QThread):
    """Giám sát thiết bị ngầm mỗi 3 giây, chỉ phát signal khi có thay đổi.
    Hỗ trợ pause/resume để tránh poll thiết bị khi đang flash."""
    devices_signal = pyqtSignal(list)

    def __init__(self, manager: ADBFastbootManager):
        super().__init__()
        self.manager = manager
        self._running = True
        self._paused = False
        self._last = None

    def stop(self):
        self._running = False

    def pause(self):
        """Tạm dừng giám sát (dùng khi đang flash)."""
        self._paused = True

    def resume(self):
        """Tiếp tục giám sát sau khi flash xong."""
        self._paused = False
        self._last = None  # Force re-scan ngay lần tiếp

    def run(self):
        while self._running:
            if not self._paused:
                try:
                    devs = self.manager.get_devices() if self.manager.is_available() else []
                except Exception:
                    devs = []
                if devs != self._last:
                    self._last = devs
                    self.devices_signal.emit(devs)
            self.msleep(3000)
