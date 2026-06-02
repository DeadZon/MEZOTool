"""Worker QThread cho EDL Flash — chạy lệnh edl tool trong background."""
import os
import subprocess
import threading
import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import get_startup_info
from core.edl_detect import build_edl_cmd


class EdlWorker(QThread):
    """Worker flash EDL. Hỗ trợ 2 chế độ:
    - XML mode: flash theo rawprogram*.xml + patch*.xml
    - Partition mode: flash từng partition riêng lẻ
    """
    log_signal = pyqtSignal(str)
    step_signal = pyqtSignal(int, int)   # (index, status)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    PENDING, RUNNING, SUCCESS, FAILED = 0, 1, 2, 3
    LOG_FLUSH_INTERVAL = 0.15

    def __init__(self, edl_path, loader_path, steps, memory_type="auto"):
        """
        edl_path: đường dẫn tới edl tool
        loader_path: đường dẫn tới firehose programmer (.mbn/.elf)
        steps: list of dict, mỗi dict có:
            - mode: "xml" hoặc "partition"
            - name: tên hiển thị
            Cho xml mode:
                - rawprogram: path tới rawprogram*.xml
                - patch: path tới patch*.xml (optional)
                - directory: thư mục chứa images
            Cho partition mode:
                - partition: tên partition
                - image: path tới file image
        memory_type: "emmc", "ufs", hoặc "auto"
        """
        super().__init__()
        self.edl_path = edl_path
        self.loader_path = loader_path
        self.steps = steps
        self.memory_type = memory_type
        self._proc = None
        self._cancelled = False
        self._log_buffer = []
        self._log_lock = threading.Lock()

    def cancel(self):
        self._cancelled = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _flush_log(self):
        with self._log_lock:
            if not self._log_buffer:
                return
            batch = "\n".join(self._log_buffer)
            self._log_buffer.clear()
        self.log_signal.emit(batch)

    def _buffered_log(self, text):
        with self._log_lock:
            self._log_buffer.append(text)

    def _build_base_args(self):
        """Tạo base arguments cho edl command."""
        args = []
        if self.loader_path:
            args += ["--loader", self.loader_path]
        if self.memory_type and self.memory_type != "auto":
            args += ["--memory", self.memory_type]
        return args

    def _build_xml_cmd(self, step):
        """Tạo lệnh cho XML mode flash."""
        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            return None

        rawprogram = step.get("rawprogram", "")
        patch = step.get("patch", "")
        directory = step.get("directory", "")

        cmd = base_cmd + ["qfil"]
        if rawprogram:
            cmd.append(rawprogram)
        if patch:
            cmd.append(patch)
        if directory:
            cmd.append(directory)
        cmd += self._build_base_args()
        return cmd

    def _build_partition_cmd(self, step):
        """Tạo lệnh cho partition mode flash."""
        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            return None

        partition = step.get("partition", "")
        image = step.get("image", "")

        cmd = base_cmd + ["w", partition, image]
        cmd += self._build_base_args()
        return cmd

    def _stream_output(self, proc):
        """Đọc stdout/stderr real-time với batch logging."""
        def read_stream(stream):
            try:
                for line in iter(stream.readline, ''):
                    if self._cancelled:
                        break
                    if line:
                        stripped = line.rstrip()
                        if stripped:
                            self._buffered_log("   " + stripped)
            except Exception:
                pass

        stdout_thread = threading.Thread(
            target=read_stream, args=(proc.stdout,), daemon=True)
        stderr_thread = threading.Thread(
            target=read_stream, args=(proc.stderr,), daemon=True)

        stdout_thread.start()
        stderr_thread.start()

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
        self._flush_log()

        return proc.returncode if proc.returncode is not None else -1

    def run(self):
        if not self.steps:
            self.finished_signal.emit(False, "Không có bước flash nào.")
            return

        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            self.finished_signal.emit(False, "EDL tool không khả dụng.")
            return

        if not self.loader_path or not os.path.isfile(self.loader_path):
            self.finished_signal.emit(False, "Chưa chọn Firehose Loader (.mbn/.elf).")
            return

        total = len(self.steps)
        self.log_signal.emit("═══ BẮT ĐẦU EDL FLASH ═══\n")
        self.log_signal.emit(f"🔧 Loader: {os.path.basename(self.loader_path)}")
        self.log_signal.emit(f"💾 Memory: {self.memory_type}")
        self.log_signal.emit(f"📦 Số bước: {total}\n")

        for i, step in enumerate(self.steps):
            if self._cancelled:
                self.log_signal.emit("\n✖ Đã hủy bởi người dùng.")
                self.finished_signal.emit(False, "Đã hủy.")
                return

            name = step.get("name", f"Bước {i+1}")
            mode = step.get("mode", "partition")

            if mode == "xml":
                cmd = self._build_xml_cmd(step)
            else:
                cmd = self._build_partition_cmd(step)

            if not cmd:
                self.log_signal.emit(f"   ✖ Không thể tạo lệnh cho bước '{name}'")
                self.step_signal.emit(i, self.FAILED)
                continue

            self.log_signal.emit(f"── [{i+1}/{total}] {name}")
            self.step_signal.emit(i, self.RUNNING)

            try:
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
                    # Tiếp tục bước tiếp theo (EDL flash thường không dừng giữa chừng)

            except Exception as e:
                self.log_signal.emit(f"   ✖ Lỗi: {e}\n")
                self.step_signal.emit(i, self.FAILED)

            self.progress_signal.emit(int((i + 1) * 100 / total))

        # Kiểm tra có bước nào failed không
        self.log_signal.emit("═══ HOÀN TẤT EDL FLASH ═══")
        self.finished_signal.emit(True, "EDL Flash hoàn tất!")


class EdlResetWorker(QThread):
    """Worker reset thiết bị sau khi flash EDL."""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, edl_path, loader_path):
        super().__init__()
        self.edl_path = edl_path
        self.loader_path = loader_path

    def run(self):
        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            self.finished_signal.emit(False, "EDL tool không khả dụng.")
            return

        self.log_signal.emit("🔄 Đang reset thiết bị...")
        cmd = base_cmd + ["reset"]
        if self.loader_path:
            cmd += ["--loader", self.loader_path]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='ignore',
                startupinfo=get_startup_info(), timeout=30
            )
            if result.returncode == 0:
                self.log_signal.emit("✔ Đã gửi lệnh reset.")
                self.finished_signal.emit(True, "Thiết bị đang khởi động lại.")
            else:
                err = result.stderr.strip() or result.stdout.strip() or "Lỗi không xác định"
                self.log_signal.emit(f"⚠ {err}")
                self.finished_signal.emit(False, err)
        except Exception as e:
            self.log_signal.emit(f"✖ Lỗi: {e}")
            self.finished_signal.emit(False, str(e))
