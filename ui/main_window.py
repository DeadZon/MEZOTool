from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentWindow, NavigationItemPosition, setTheme, Theme
from qfluentwidgets import FluentIcon as FIF

from core.adb_fastboot import ADBFastbootManager, load_config
from core.device_monitor import DeviceMonitor
from ui.pages.dashboard_page import DashboardPage
from ui.pages.edl_page import EdlPage
from ui.pages.settings_page import SettingsPage
from ui.pages.terminal_page import TerminalPage
from ui.pages.driver_page import DriverPage
from ui.pages.scrcpy_page import ScrcpyPage


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.manager = ADBFastbootManager()

        # Theme
        config = load_config()
        t = {"Light": Theme.LIGHT, "Dark": Theme.DARK}.get(config.get("theme"), Theme.AUTO)
        setTheme(t)

        # Window
        self.setWindowTitle("DeadZone Flash Tool")
        self.resize(1150, 750)
        self.setMinimumSize(950, 620)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move((screen.width() - self.width()) // 2,
                  (screen.height() - self.height()) // 2)

        # Pages
        self.dashboard = DashboardPage(self.manager, self)
        self.edl = EdlPage(self)
        self.terminal = TerminalPage(self.manager, self)
        self.scrcpy = ScrcpyPage(self.manager, self)
        self.driver = DriverPage(self)
        self.settings = SettingsPage(self.manager, self)
        self.settings.settings_changed.connect(self._on_settings)

        self.addSubInterface(self.dashboard, FIF.HOME, "Dashboard")
        self.addSubInterface(self.edl, FIF.DEVELOPER_TOOLS, "EDL Flash")
        self.addSubInterface(self.terminal, FIF.COMMAND_PROMPT, "Terminal")
        self.addSubInterface(self.scrcpy, FIF.PHONE, "Scrcpy")
        self.addSubInterface(self.driver, FIF.CONNECT, "USB Driver")
        self.addSubInterface(self.settings, FIF.SETTING, "Settings",
                           NavigationItemPosition.BOTTOM)

        # Monitor
        self.monitor = DeviceMonitor(self.manager)
        self.monitor.devices_signal.connect(self._on_devices)
        self.monitor.start()

        # Attach the monitor to the dashboard so it can pause/resume during flashing
        self.dashboard.set_monitor(self.monitor)

    def _on_devices(self, devices):
        self.dashboard.update_devices(devices)

    def _on_settings(self):
        self.manager.detect_paths()
        self.monitor._last = None  # Force re-scan

    def closeEvent(self, event):
        self.monitor.stop()
        self.monitor.wait(3000)

        if hasattr(self.settings, 'downloader') and self.settings.downloader:
            if self.settings.downloader.isRunning():
                self.settings.downloader.cancel()
                self.settings.downloader.wait(2000)

        if hasattr(self.dashboard, 'worker') and self.dashboard.worker and self.dashboard.worker.isRunning():
            self.dashboard.worker.cancel()
            self.dashboard.worker.wait(2000)

        # Clean up pages
        self.terminal.cleanup()
        self.scrcpy.cleanup()
        self.edl.cleanup()

        super().closeEvent(event)

