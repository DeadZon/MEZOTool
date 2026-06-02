import sys
import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

# Ẩn cửa sổ CMD trên Windows
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass


def _optimize_windows_timer():
    """Giảm timer resolution của Windows từ ~15.6ms xuống 1ms.
    Mặc định Windows dùng tick 15.625ms → QTimer, animation, repaint
    chỉ chạy tối đa ~64Hz và bị giật. timeBeginPeriod(1) khiến hệ thống
    dùng timer 1ms → animation mượt hơn rất nhiều."""
    if sys.platform != 'win32':
        return
    try:
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)
    except Exception:
        pass


def main():
    # ── Tối ưu rendering trên Windows ──
    # Cho phép Qt repaint ngay lập tức, không đợi idle
    os.environ["QT_QPA_UPDATE_IDLE_TIME"] = "0"
    # HiDPI scaling
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    if hasattr(Qt, 'HighDpiScaleFactorRoundingPolicy'):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    # Tắt VSync (swap interval = 0) → không bị cap ở refresh rate màn hình
    fmt = QSurfaceFormat()
    fmt.setSwapInterval(0)
    QSurfaceFormat.setDefaultFormat(fmt)

    # Giảm timer resolution Windows → animation mượt
    _optimize_windows_timer()

    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
