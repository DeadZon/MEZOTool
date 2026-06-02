import os
import threading
import subprocess
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTextEdit,
                             QLineEdit, QSizePolicy)
from qfluentwidgets import (TitleLabel, BodyLabel, SubtitleLabel, SimpleCardWidget,
                            PushButton, InfoBar, InfoBarPosition)
from core.adb_fastboot import ADBFastbootManager, get_startup_info
from ui.styles import Styles


class CommandRunner(QThread):
    """Thread chạy lệnh ADB/Fastboot và stream output real-time."""
    output_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int)  # return code

    def __init__(self, cmd, cwd=None):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
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
            self._proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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

            stdout_t = threading.Thread(target=read_stream, args=(self._proc.stdout,), daemon=True)
            stderr_t = threading.Thread(target=read_stream, args=(self._proc.stderr,), daemon=True)
            stdout_t.start()
            stderr_t.start()

            self._proc.wait()
            stdout_t.join(timeout=5)
            stderr_t.join(timeout=5)

            self.finished_signal.emit(self._proc.returncode)
        except Exception as e:
            self.output_signal.emit(f"✖ Lỗi: {e}")
            self.finished_signal.emit(-1)


class HistoryLineEdit(QLineEdit):
    """QLineEdit với lịch sử lệnh (phím ↑↓)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history = []
        self._history_idx = -1
        self._current_text = ""

    def add_history(self, text):
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._history_idx = -1
        self._current_text = ""

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Up:
            if self._history:
                if self._history_idx == -1:
                    self._current_text = self.text()
                    self._history_idx = len(self._history) - 1
                elif self._history_idx > 0:
                    self._history_idx -= 1
                self.setText(self._history[self._history_idx])
            return
        elif event.key() == Qt.Key.Key_Down:
            if self._history_idx >= 0:
                self._history_idx += 1
                if self._history_idx >= len(self._history):
                    self._history_idx = -1
                    self.setText(self._current_text)
                else:
                    self.setText(self._history[self._history_idx])
            return
        super().keyPressEvent(event)


class TerminalPage(QWidget):
    """Trang terminal cho phép nhập lệnh ADB/Fastboot tùy ý."""

    def __init__(self, manager: ADBFastbootManager, parent=None):
        super().__init__(parent=parent)
        self.manager = manager
        self._runner = None
        self.setObjectName("terminal_page")
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("Terminal"))

        # ── Thông tin ──
        info_card = SimpleCardWidget()
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(16, 12, 16, 12)
        desc = BodyLabel("Nhập lệnh ADB hoặc Fastboot. Ví dụ: adb devices, fastboot getvar all, adb shell getprop")
        desc.setWordWrap(True)
        info_layout.addWidget(desc)
        layout.addWidget(info_card)

        # ── Terminal output ──
        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(Styles.terminal())
        self.terminal.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.terminal, 1)

        # ── Input row ──
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        prompt = BodyLabel("❯")
        prompt.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        input_row.addWidget(prompt)

        self.input_field = HistoryLineEdit()
        self.input_field.setPlaceholderText("Nhập lệnh (ví dụ: adb devices)...")
        self.input_field.setStyleSheet(Styles.console_input())
        self.input_field.setMinimumHeight(40)
        self.input_field.returnPressed.connect(self._execute)
        input_row.addWidget(self.input_field, 1)

        self.btn_run = PushButton("▶ Chạy")
        self.btn_run.setMinimumHeight(40)
        self.btn_run.clicked.connect(self._execute)
        input_row.addWidget(self.btn_run)

        self.btn_stop = PushButton("⏹ Dừng")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._cancel)
        input_row.addWidget(self.btn_stop)

        layout.addLayout(input_row)

        # ── Action buttons ──
        btn_row = QHBoxLayout()
        b1 = PushButton("📋 Copy Log")
        b1.clicked.connect(self._copy)
        b2 = PushButton("🗑 Xóa Log")
        b2.clicked.connect(self.terminal.clear)
        btn_row.addWidget(b1)
        btn_row.addWidget(b2)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _resolve_cmd(self, text):
        """Phân tích lệnh user nhập, thay thế adb/fastboot bằng path đúng."""
        parts = text.strip().split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1:]

        if cmd_name == "adb":
            if self.manager.adb_path:
                return [self.manager.adb_path] + args
            else:
                self.terminal.append("✖ ADB chưa được cấu hình. Vào Cài đặt để thiết lập.")
                return None
        elif cmd_name == "fastboot":
            if self.manager.fastboot_path:
                return [self.manager.fastboot_path] + args
            else:
                self.terminal.append("✖ Fastboot chưa được cấu hình. Vào Cài đặt để thiết lập.")
                return None
        else:
            self.terminal.append(f"✖ Lệnh không hỗ trợ: '{cmd_name}'. Chỉ hỗ trợ 'adb' và 'fastboot'.")
            return None

    def _execute(self):
        text = self.input_field.text().strip()
        if not text:
            return

        if self._runner and self._runner.isRunning():
            InfoBar.warning("Đang chạy", "Vui lòng đợi lệnh hiện tại hoàn tất.",
                          position=InfoBarPosition.TOP, parent=self.window())
            return

        cmd = self._resolve_cmd(text)
        if not cmd:
            return

        self.input_field.add_history(text)
        self.input_field.clear()

        # Hiện lệnh trong terminal
        self.terminal.append(f"\n❯ {text}")
        self.terminal.append("─" * 40)

        self._set_busy(True)
        self._runner = CommandRunner(cmd)
        self._runner.output_signal.connect(self._on_output)
        self._runner.finished_signal.connect(self._on_finished)
        self._runner.start()

    def _on_output(self, line):
        self.terminal.append(line)

    def _on_finished(self, rc):
        self._set_busy(False)
        if rc == 0:
            self.terminal.append("✔ Hoàn tất")
        else:
            self.terminal.append(f"✖ Kết thúc (mã {rc})")

    def _cancel(self):
        if self._runner and self._runner.isRunning():
            self._runner.cancel()
            self.terminal.append("⏹ Đã hủy lệnh.")
            self._set_busy(False)

    def _set_busy(self, busy):
        self.btn_run.setEnabled(not busy)
        self.input_field.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.terminal.toPlainText())
        InfoBar.success("Đã copy", "", duration=1500,
                      position=InfoBarPosition.TOP, parent=self.window())

    def cleanup(self):
        """Gọi khi đóng app."""
        if self._runner and self._runner.isRunning():
            self._runner.cancel()
            self._runner.wait(2000)
