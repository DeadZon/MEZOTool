import os
import sys
import shutil
import subprocess
import threading
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QSizePolicy, QLabel)
from qfluentwidgets import (TitleLabel, BodyLabel, SubtitleLabel, SimpleCardWidget,
                            PushButton, PrimaryPushButton, ComboBox, SpinBox,
                            InfoBar, InfoBarPosition)
from core.adb_fastboot import ADBFastbootManager, get_startup_info
from ui.styles import Styles


class ScrcpyProcess(QThread):
    """Thread quản lý process scrcpy."""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)

    def __init__(self, scrcpy_path, args):
        super().__init__()
        self.scrcpy_path = scrcpy_path
        self.args = args
        self._proc = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def run(self):
        try:
            cmd = [self.scrcpy_path] + self.args
            self.output_signal.emit(f"▶ Chạy: scrcpy {' '.join(self.args)}")

            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='ignore',
                startupinfo=get_startup_info(), bufsize=1
            )

            def read_stream(stream):
                try:
                    for line in iter(stream.readline, ''):
                        if self._cancelled:
                            break
                        stripped = line.rstrip()
                        if stripped:
                            self.output_signal.emit(stripped)
                except Exception:
                    pass

            t1 = threading.Thread(target=read_stream, args=(self._proc.stdout,), daemon=True)
            t2 = threading.Thread(target=read_stream, args=(self._proc.stderr,), daemon=True)
            t1.start()
            t2.start()

            self._proc.wait()
            t1.join(timeout=3)
            t2.join(timeout=3)

            self.finished_signal.emit(self._proc.returncode)
        except Exception as e:
            self.output_signal.emit(f"✖ Lỗi: {e}")
            self.finished_signal.emit(-1)


class ScrcpyPage(QWidget):
    """Trang Scrcpy - Hiển thị màn hình điện thoại."""

    def __init__(self, manager: ADBFastbootManager, parent=None):
        super().__init__(parent=parent)
        self.manager = manager
        self._scrcpy_proc = None
        self._scrcpy_path = None
        self.setObjectName("scrcpy_page")
        self._detect_scrcpy()
        self._build()

    def _detect_scrcpy(self):
        """Tìm scrcpy.exe theo thứ tự: thư mục scrcpy/ trong project → PATH."""
        if getattr(sys, 'frozen', False):
            app_dir = os.path.dirname(sys.executable)
        else:
            app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        exe_name = "scrcpy.exe" if sys.platform == 'win32' else "scrcpy"

        # 1. Thư mục scrcpy/ trong project
        local = os.path.join(app_dir, "scrcpy", exe_name)
        if os.path.isfile(local):
            self._scrcpy_path = local
            return

        # 2. PATH hệ thống
        found = shutil.which("scrcpy")
        if found:
            self._scrcpy_path = found

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(TitleLabel("Scrcpy - Hiển thị màn hình"))

        # ── Status card ──
        status_card = SimpleCardWidget()
        sl = QVBoxLayout(status_card)
        sl.setContentsMargins(16, 14, 16, 14)

        self.status_label = BodyLabel("")
        self.status_label.setWordWrap(True)

        if self._scrcpy_path:
            self.status_label.setText(f"✔ Đã tìm thấy Scrcpy: {self._scrcpy_path}")
        else:
            self.status_label.setText(
                "⚠ Không tìm thấy Scrcpy.\n\n"
                "Hãy tải scrcpy và giải nén vào thư mục 'scrcpy/' trong thư mục ứng dụng,\n"
                "hoặc cài scrcpy vào PATH hệ thống.\n\n"
                "📥 Tải tại: https://github.com/Genymobile/scrcpy/releases"
            )
        sl.addWidget(self.status_label)

        if not self._scrcpy_path:
            btn_open = PushButton("🌐 Mở trang tải Scrcpy")
            btn_open.clicked.connect(self._open_scrcpy_page)
            sl.addWidget(btn_open)

            btn_refresh = PushButton("🔄 Kiểm tra lại")
            btn_refresh.clicked.connect(self._refresh_scrcpy)
            sl.addWidget(btn_refresh)

        layout.addWidget(status_card)

        # ── Tùy chọn ──
        opt_card = SimpleCardWidget()
        ol = QVBoxLayout(opt_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.addWidget(SubtitleLabel("Tùy chọn"))

        # Row 1: Max size
        r1 = QHBoxLayout()
        r1.addWidget(BodyLabel("Độ phân giải tối đa:"))
        self.size_combo = ComboBox()
        self.size_combo.addItems(["Mặc định", "640", "800", "1024", "1280", "1920"])
        self.size_combo.setMinimumWidth(140)
        r1.addWidget(self.size_combo)
        r1.addStretch()
        ol.addLayout(r1)

        # Row 2: Bitrate
        r2 = QHBoxLayout()
        r2.addWidget(BodyLabel("Bitrate (Mbps):"))
        self.bitrate_spin = SpinBox()
        self.bitrate_spin.setRange(1, 32)
        self.bitrate_spin.setValue(8)
        self.bitrate_spin.setMinimumWidth(100)
        r2.addWidget(self.bitrate_spin)
        r2.addStretch()
        ol.addLayout(r2)

        # Row 3: Extra options
        r3 = QHBoxLayout()
        self.chk_stay_awake = PushButton("☕ Giữ màn hình sáng")
        self.chk_stay_awake.setCheckable(True)
        r3.addWidget(self.chk_stay_awake)

        self.chk_borderless = PushButton("🖼 Không viền")
        self.chk_borderless.setCheckable(True)
        r3.addWidget(self.chk_borderless)

        self.chk_always_top = PushButton("📌 Luôn trên cùng")
        self.chk_always_top.setCheckable(True)
        r3.addWidget(self.chk_always_top)

        r3.addStretch()
        ol.addLayout(r3)

        layout.addWidget(opt_card)

        # ── Action buttons ──
        action_card = SimpleCardWidget()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(16, 12, 16, 12)

        btn_row = QHBoxLayout()
        self.btn_start = PrimaryPushButton("▶ Bắt đầu Scrcpy")
        self.btn_start.setMinimumHeight(44)
        self.btn_start.setEnabled(self._scrcpy_path is not None)
        self.btn_start.clicked.connect(self._start)
        btn_row.addWidget(self.btn_start)

        self.btn_stop = PushButton("⏹ Dừng Scrcpy")
        self.btn_stop.setMinimumHeight(44)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btn_row.addWidget(self.btn_stop)

        al.addLayout(btn_row)
        layout.addWidget(action_card)

        # ── Log output ──
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet(Styles.terminal())
        self.log_output.setMaximumHeight(200)
        layout.addWidget(self.log_output)

        layout.addStretch()

    def _build_scrcpy_args(self):
        """Tạo danh sách arguments cho scrcpy."""
        args = []

        # Serial thiết bị
        # Lấy serial từ thiết bị đang kết nối ADB
        devices = self.manager.get_devices()
        adb_devices = [d for d in devices if d["mode"] == "ADB"]
        if adb_devices:
            args += ["-s", adb_devices[0]["serial"]]

        # Max size
        size = self.size_combo.currentText()
        if size != "Mặc định":
            args += ["--max-size", size]

        # Bitrate
        bitrate = self.bitrate_spin.value()
        args += ["--video-bit-rate", f"{bitrate}M"]

        # Tùy chọn
        if self.chk_stay_awake.isChecked():
            args.append("--stay-awake")
        if self.chk_borderless.isChecked():
            args.append("--window-borderless")
        if self.chk_always_top.isChecked():
            args.append("--always-on-top")

        return args

    def _start(self):
        if not self._scrcpy_path:
            InfoBar.error("Lỗi", "Scrcpy chưa được cài đặt.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        if self._scrcpy_proc and self._scrcpy_proc.isRunning():
            InfoBar.warning("Đang chạy", "Scrcpy đang chạy. Hãy dừng trước khi bắt đầu lại.",
                          position=InfoBarPosition.TOP, parent=self.window())
            return

        # Kiểm tra thiết bị ADB
        devices = self.manager.get_devices()
        adb_devices = [d for d in devices if d["mode"] == "ADB"]
        if not adb_devices:
            InfoBar.error("Lỗi", "Không có thiết bị ADB nào được kết nối. Scrcpy cần kết nối ADB.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        self.log_output.clear()
        args = self._build_scrcpy_args()

        self._scrcpy_proc = ScrcpyProcess(self._scrcpy_path, args)
        self._scrcpy_proc.output_signal.connect(self.log_output.append)
        self._scrcpy_proc.finished_signal.connect(self._on_scrcpy_done)
        self._scrcpy_proc.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.log_output.append("📱 Đang khởi động Scrcpy...")

    def _stop(self):
        if self._scrcpy_proc and self._scrcpy_proc.isRunning():
            self._scrcpy_proc.cancel()
            self.log_output.append("⏹ Đã dừng Scrcpy.")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _on_scrcpy_done(self, rc):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if rc == 0:
            self.log_output.append("✔ Scrcpy đã kết thúc.")
        else:
            self.log_output.append(f"✖ Scrcpy kết thúc với mã {rc}")

    def _open_scrcpy_page(self):
        import webbrowser
        webbrowser.open("https://github.com/Genymobile/scrcpy/releases")

    def _refresh_scrcpy(self):
        self._detect_scrcpy()
        if self._scrcpy_path:
            self.status_label.setText(f"✔ Đã tìm thấy Scrcpy: {self._scrcpy_path}")
            self.btn_start.setEnabled(True)
            InfoBar.success("Tìm thấy!", f"Scrcpy: {self._scrcpy_path}",
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.warning("Không tìm thấy", "Vui lòng tải scrcpy và thử lại.",
                          position=InfoBarPosition.TOP, parent=self.window())

    def cleanup(self):
        """Gọi khi đóng app."""
        if self._scrcpy_proc and self._scrcpy_proc.isRunning():
            self._scrcpy_proc.cancel()
            self._scrcpy_proc.wait(3000)
