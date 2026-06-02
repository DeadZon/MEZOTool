import os
import sys
import subprocess
import shutil
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy)
from qfluentwidgets import (TitleLabel, BodyLabel, SubtitleLabel, SimpleCardWidget,
                            PushButton, PrimaryPushButton, InfoBar, InfoBarPosition,
                            ProgressBar)
from core.adb_fastboot import get_startup_info


# URL tải Google USB Driver
DRIVER_INF_URL = "https://dl.google.com/android/repository/usb_driver_r13-windows.zip"


class DriverInstallThread(QThread):
    """Thread tải và cài đặt Google USB Driver."""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, app_dir):
        super().__init__()
        self.app_dir = app_dir

    def run(self):
        import zipfile
        import urllib.request

        driver_dir = os.path.join(self.app_dir, "usb_driver")
        zip_path = os.path.join(self.app_dir, "usb_driver.zip")

        try:
            # Bước 1: Tải driver
            self.log_signal.emit("📥 Đang tải Google USB Driver...")
            self.progress_signal.emit(10)

            req = urllib.request.Request(DRIVER_INF_URL, headers={
                "User-Agent": "NTFlashTools/1.0"
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(zip_path, "wb") as f:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = int(10 + (downloaded / total) * 40)
                            self.progress_signal.emit(min(pct, 50))

            self.log_signal.emit("✔ Tải xong.")
            self.progress_signal.emit(50)

            # Bước 2: Giải nén
            self.log_signal.emit("📦 Đang giải nén...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(self.app_dir)
            self.progress_signal.emit(70)

            # Tìm file .inf
            inf_path = None
            extracted_dir = os.path.join(self.app_dir, "usb_driver")
            if os.path.isdir(extracted_dir):
                for f in os.listdir(extracted_dir):
                    if f.lower().endswith(".inf"):
                        inf_path = os.path.join(extracted_dir, f)
                        break

            if not inf_path:
                self.finished_signal.emit(False, "Không tìm thấy file .inf trong driver đã tải.")
                return

            self.log_signal.emit(f"📄 Tìm thấy driver: {os.path.basename(inf_path)}")
            self.progress_signal.emit(80)

            # Bước 3: Cài đặt bằng pnputil (yêu cầu Admin)
            self.log_signal.emit("🔧 Đang cài đặt driver (yêu cầu quyền Admin)...")

            # Dùng runas để yêu cầu quyền Admin
            result = subprocess.run(
                ["powershell", "-Command",
                 f"Start-Process pnputil -ArgumentList '/add-driver \"{inf_path}\" /install' -Verb RunAs -Wait -PassThru"],
                capture_output=True, text=True, encoding='utf-8', errors='ignore',
                startupinfo=get_startup_info(), timeout=120
            )

            self.progress_signal.emit(100)

            if result.returncode == 0:
                self.log_signal.emit("✔ Cài đặt driver thành công!")
                self.finished_signal.emit(True, "Google USB Driver đã được cài đặt thành công.")
            else:
                err = result.stderr.strip() or result.stdout.strip() or "Lỗi không xác định"
                self.log_signal.emit(f"⚠ {err}")
                self.finished_signal.emit(False, f"Cài đặt có thể thất bại: {err}")

        except Exception as e:
            self.log_signal.emit(f"✖ Lỗi: {e}")
            self.finished_signal.emit(False, str(e))
        finally:
            # Dọn file zip
            try:
                if os.path.exists(zip_path):
                    os.remove(zip_path)
            except Exception:
                pass


class DriverCheckThread(QThread):
    """Thread kiểm tra driver Android đã cài đặt."""
    result_signal = pyqtSignal(str)

    def run(self):
        try:
            result = subprocess.run(
                ["pnputil", "/enum-drivers"],
                capture_output=True, text=True, encoding='utf-8', errors='ignore',
                startupinfo=get_startup_info(), timeout=15
            )
            output = result.stdout or ""
            # Tìm driver Android/Google
            drivers = []
            lines = output.splitlines()
            current_block = []
            for line in lines:
                if line.strip() == "":
                    if current_block:
                        block_text = "\n".join(current_block)
                        lower = block_text.lower()
                        if any(kw in lower for kw in ["android", "google", "adb", "bootloader", "composite"]):
                            drivers.append(block_text)
                    current_block = []
                else:
                    current_block.append(line)
            # Xử lý block cuối
            if current_block:
                block_text = "\n".join(current_block)
                lower = block_text.lower()
                if any(kw in lower for kw in ["android", "google", "adb", "bootloader", "composite"]):
                    drivers.append(block_text)

            if drivers:
                self.result_signal.emit(
                    f"✔ Tìm thấy {len(drivers)} driver Android:\n\n" + "\n\n".join(drivers))
            else:
                self.result_signal.emit("⚠ Không tìm thấy driver Android nào. Hãy cài đặt Google USB Driver.")
        except Exception as e:
            self.result_signal.emit(f"✖ Lỗi kiểm tra: {e}")


class DriverPage(QWidget):
    """Trang cài đặt USB Driver cho Windows."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self._installer = None
        self._checker = None
        # Khi chạy từ PyInstaller (frozen), dùng thư mục chứa exe
        if getattr(sys, 'frozen', False):
            self._app_dir = os.path.dirname(sys.executable)
        else:
            self._app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.setObjectName("driver_page")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(TitleLabel("Cài đặt USB Driver"))

        # ── Thông tin ──
        info_card = SimpleCardWidget()
        il = QVBoxLayout(info_card)
        il.setContentsMargins(16, 14, 16, 14)

        il.addWidget(SubtitleLabel("Google USB Driver"))
        desc = BodyLabel(
            "Google USB Driver cho phép Windows nhận diện thiết bị Android qua ADB và Fastboot.\n"
            "Driver này cần thiết để flash ROM, debug, và sử dụng các công cụ ADB/Fastboot.\n\n"
            "⚠ Cài đặt driver yêu cầu quyền Quản trị viên (Administrator)."
        )
        desc.setWordWrap(True)
        il.addWidget(desc)
        layout.addWidget(info_card)

        # ── Nút hành động ──
        action_card = SimpleCardWidget()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(16, 14, 16, 14)

        btn_row = QHBoxLayout()
        self.btn_install = PrimaryPushButton("📥 Tải & Cài đặt Driver")
        self.btn_install.setMinimumHeight(44)
        self.btn_install.clicked.connect(self._install)
        btn_row.addWidget(self.btn_install)

        self.btn_check = PushButton("🔍 Kiểm tra Driver")
        self.btn_check.setMinimumHeight(44)
        self.btn_check.clicked.connect(self._check)
        btn_row.addWidget(self.btn_check)
        al.addLayout(btn_row)

        # Progress
        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        al.addWidget(self.progress)

        layout.addWidget(action_card)

        # ── Log output ──
        from PyQt6.QtWidgets import QTextEdit
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        from ui.styles import Styles
        self.log_output.setStyleSheet(Styles.terminal())
        self.log_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.log_output, 1)

        # ── Chỉ hỗ trợ Windows ──
        if sys.platform != 'win32':
            self.btn_install.setEnabled(False)
            self.btn_check.setEnabled(False)
            self.log_output.append("ℹ Tính năng này chỉ hỗ trợ trên Windows.")

    def _install(self):
        if self._installer and self._installer.isRunning():
            InfoBar.warning("Đang cài đặt", "Vui lòng đợi.",
                          position=InfoBarPosition.TOP, parent=self.window())
            return

        self.log_output.clear()
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.btn_install.setEnabled(False)

        self._installer = DriverInstallThread(self._app_dir)
        self._installer.log_signal.connect(self.log_output.append)
        self._installer.progress_signal.connect(self.progress.setValue)
        self._installer.finished_signal.connect(self._on_install_done)
        self._installer.start()

    def _on_install_done(self, ok, msg):
        self.btn_install.setEnabled(True)
        self.progress.setVisible(False)
        if ok:
            InfoBar.success("Thành công", msg,
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Lỗi", msg,
                        position=InfoBarPosition.TOP, parent=self.window())

    def _check(self):
        if self._checker and self._checker.isRunning():
            return

        self.log_output.clear()
        self.log_output.append("🔍 Đang kiểm tra driver Android...")
        self.btn_check.setEnabled(False)

        self._checker = DriverCheckThread()
        self._checker.result_signal.connect(self._on_check_done)
        self._checker.start()

    def _on_check_done(self, result):
        self.btn_check.setEnabled(True)
        self.log_output.clear()
        self.log_output.append(result)

    def cleanup(self):
        if self._installer and self._installer.isRunning():
            self._installer.wait(3000)
