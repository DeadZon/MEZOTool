import subprocess
import threading
import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import ADBFastbootManager, get_startup_info


class FlashWorker(QThread):
    log_signal = pyqtSignal(str)
    step_signal = pyqtSignal(int, int)   # (index, status)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)
    ask_continue_signal = pyqtSignal(str)  # Hỏi user khi flash lỗi

    PENDING, RUNNING, SUCCESS, FAILED = 0, 1, 2, 3

    # Chu kỳ flush log (giây) — gộp nhiều dòng rồi emit 1 lần
    LOG_FLUSH_INTERVAL = 0.15

    def __init__(self, manager: ADBFastbootManager, steps, serial=None):
        super().__init__()
        self.manager = manager
        self.steps = steps
        self.serial = serial
        self._proc = None
        self._cancelled = False
        self._continue_event = threading.Event()
        self._continue_answer = False
        # Buffer cho batch log
        self._log_buffer = []
        self._log_lock = threading.Lock()

    def reply_continue(self, yes: bool):
        """UI gọi method này để trả lời có tiếp tục flash hay không."""
        self._continue_answer = yes
        self._continue_event.set()

    def cancel(self):
        self._cancelled = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _flush_log(self):
        """Gửi toàn bộ log buffer ra UI, gộp thành 1 lần emit."""
        with self._log_lock:
            if not self._log_buffer:
                return
            batch = "\n".join(self._log_buffer)
            self._log_buffer.clear()
        self.log_signal.emit(batch)

    def _buffered_log(self, text):
        """Thêm dòng log vào buffer. Sẽ được flush theo chu kỳ."""
        with self._log_lock:
            self._log_buffer.append(text)

    def _stream_output(self, proc):
        """Đọc stdout và stderr real-time từ process.
        Fastboot ghi output chính ra stderr, nên cần đọc cả hai.
        Sử dụng batch logging để giảm tải cho UI thread."""

        output_lines = []
        errors = []

        def read_stream(stream, target_list):
            try:
                for line in iter(stream.readline, ''):
                    if self._cancelled:
                        break
                    if line:
                        stripped = line.rstrip()
                        if stripped:
                            target_list.append(stripped)
                            self._buffered_log("   " + stripped)
            except Exception:
                pass

        stdout_thread = threading.Thread(
            target=read_stream, args=(proc.stdout, output_lines), daemon=True)
        stderr_thread = threading.Thread(
            target=read_stream, args=(proc.stderr, errors), daemon=True)

        stdout_thread.start()
        stderr_thread.start()

        # Chờ process kết thúc, flush log theo chu kỳ
        while proc.poll() is None:
            self._flush_log()
            time.sleep(self.LOG_FLUSH_INTERVAL)
            if self._cancelled:
                try:
                    proc.terminate()
                except Exception:
                    pass
                break

        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        # Flush log còn lại sau khi process kết thúc
        self._flush_log()

        return proc.returncode if proc.returncode is not None else -1

    def run(self):
        if not self.steps:
            self.finished_signal.emit(False, "Không có bước flash nào.")
            return
        if not self.manager.is_available():
            self.finished_signal.emit(False, "ADB/Fastboot không khả dụng.")
            return

        total = len(self.steps)
        self.log_signal.emit("═══ BẮT ĐẦU FLASH ═══\n")

        for i, step in enumerate(self.steps):
            if self._cancelled:
                self.log_signal.emit("\n✖ Đã hủy bởi người dùng.")
                self.finished_signal.emit(False, "Đã hủy.")
                return

            name = step.get("name", f"Bước {i+1}")
            exe = self.manager.adb_path if step.get("type") == "ADB" else self.manager.fastboot_path
            args = list(step.get("args", []))

            cmd = [exe]
            if self.serial:
                cmd += ["-s", self.serial]
            cmd += args

            self.log_signal.emit(f"── [{i+1}/{total}] {name}")
            self.step_signal.emit(i, self.RUNNING)

            try:
                # Tạo process với stdout và stderr riêng để stream real-time
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='ignore',
                    startupinfo=get_startup_info(), bufsize=1
                )

                rc = self._stream_output(self._proc)

                if rc == 0:
                    self.log_signal.emit(f"   ✔ Thành công\n")
                    self.step_signal.emit(i, self.SUCCESS)
                else:
                    self.log_signal.emit(f"   ✖ Thất bại (mã {rc})\n")
                    self.step_signal.emit(i, self.FAILED)
                    # Hỏi user có muốn tiếp tục không (timeout 5 phút)
                    self._continue_event.clear()
                    self.ask_continue_signal.emit(name)
                    if not self._continue_event.wait(timeout=300):
                        # Timeout → dừng flash
                        self.log_signal.emit("   ⏱ Timeout: không có phản hồi, dừng flash.\n")
                        self.finished_signal.emit(False, f"Timeout tại bước '{name}'")
                        return
                    if not self._continue_answer:
                        self.finished_signal.emit(False, f"Dừng tại bước '{name}' (mã {rc})")
                        return
                    self.log_signal.emit(f"   ▶ Tiếp tục flash...\n")
            except Exception as e:
                self.log_signal.emit(f"   ✖ Lỗi: {e}\n")
                self.step_signal.emit(i, self.FAILED)
                # Hỏi user có muốn tiếp tục không (timeout 5 phút)
                self._continue_event.clear()
                self.ask_continue_signal.emit(name)
                if not self._continue_event.wait(timeout=300):
                    self.log_signal.emit("   ⏱ Timeout: không có phản hồi, dừng flash.\n")
                    self.finished_signal.emit(False, f"Timeout tại bước '{name}'")
                    return
                if not self._continue_answer:
                    self.finished_signal.emit(False, str(e))
                    return
                self.log_signal.emit(f"   ▶ Tiếp tục flash...\n")

            self.progress_signal.emit(int((i + 1) * 100 / total))

        self.log_signal.emit("═══ HOÀN TẤT ═══")
        self.finished_signal.emit(True, "Flash hoàn tất thành công!")
