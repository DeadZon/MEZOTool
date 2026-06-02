import logging
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import ADBFastbootManager


class DeviceMonitor(QThread):
    """Monitor devices in the background every 3 seconds and emit only when changes occur.
    Supports pause/resume to avoid polling the device during flashing."""
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
        """Pause monitoring during flashing."""
        self._paused = True

    def resume(self):
        """Resume monitoring after flashing completes."""
        self._paused = False
        self._last = None  # Force a re-scan on the next pass

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
