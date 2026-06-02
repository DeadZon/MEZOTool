import sys
import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

# Hide the CMD window on Windows
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass


def _optimize_windows_timer():
    """Reduce the Windows timer resolution from ~15.6ms to 1ms.
    Windows normally uses a 15.625ms tick, which can limit QTimer, animation,
    and repaint work to ~64Hz and make rendering feel choppy. timeBeginPeriod(1)
    requests a 1ms timer for smoother animation."""
    if sys.platform != 'win32':
        return
    try:
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
    except Exception:
        pass


def main():
    # ── Optimize rendering on Windows ──
    # Allow Qt to repaint immediately without waiting for idle
    os.environ["QT_QPA_UPDATE_IDLE_TIME"] = "0"
    # HiDPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Disable VSync (swap interval = 0), so rendering is not capped by display refresh rate
    fmt = QSurfaceFormat()
    fmt.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(fmt)

    # Reduce Windows timer resolution for smoother animation
    _optimize_windows_timer()

    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
