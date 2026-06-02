"""QThread worker for EDL Flash that runs edl tool commands in the background."""
import os
import subprocess
import threading
import time
from PyQt6.QtCore import QThread, pyqtSignal
from core.adb_fastboot import get_startup_info
from core.edl_detect import build_edl_cmd


class EdlWorker(QThread):
    """EDL flash worker. Supports 2 modes:
    - XML mode: flash by rawprogram*.xml + patch*.xml
    - Partition mode: flash each partition separately
    """
    log_signal = pyqtSignal(str)
    step_signal = pyqtSignal(int, int)   # (index, status)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal(bool, str)

    PENDING, RUNNING, SUCCESS, FAILED = 0, 1, 2, 3
    LOG_FLUSH_INTERVAL = 0.15

    def __init__(self, edl_path, loader_path, steps, memory_type="auto"):
        """
        edl_path: path to the edl tool
        loader_path: path to the firehose programmer (.mbn/.elf)
        steps: list of dict, each dict has:
            - mode: "xml" or "partition"
            - name: display name
            Cho xml mode:
                - rawprogram: path to rawprogram*.xml
                - patch: path to patch*.xml (optional)
                - directory: directory containing images
            Cho partition mode:
                - partition: partition name
                - image: path to the image file
        memory_type: "emmc", "ufs", or "auto"
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
        """Build base arguments for the edl command."""
        args = []
        if self.loader_path:
            args += ["--loader", self.loader_path]
        if self.memory_type and self.memory_type != "auto":
            args += ["--memory", self.memory_type]
        return args

    def _build_xml_cmd(self, step):
        """Build the command for XML mode flashing."""
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
        """Build the command for partition mode flashing."""
        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            return None

        partition = step.get("partition", "")
        image = step.get("image", "")

        cmd = base_cmd + ["w", partition, image]
        cmd += self._build_base_args()
        return cmd

    def _stream_output(self, proc):
        """Read stdout/stderr in real time with batch logging."""
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
            self.finished_signal.emit(False, "No flash steps.")
            return

        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            self.finished_signal.emit(False, "EDL tool is not available.")
            return

        if not self.loader_path or not os.path.isfile(self.loader_path):
            self.finished_signal.emit(False, "No Firehose Loader (.mbn/.elf) selected.")
            return

        total = len(self.steps)
        self.log_signal.emit("═══ STARTING EDL FLASH ═══\n")
        self.log_signal.emit(f"🔧 Loader: {os.path.basename(self.loader_path)}")
        self.log_signal.emit(f"💾 Memory: {self.memory_type}")
        self.log_signal.emit(f"📦 Steps: {total}\n")

        for i, step in enumerate(self.steps):
            if self._cancelled:
                self.log_signal.emit("\n✖ Cancelled by the user.")
                self.finished_signal.emit(False, "Cancelled.")
                return

            name = step.get("name", f"Step {i+1}")
            mode = step.get("mode", "partition")

            if mode == "xml":
                cmd = self._build_xml_cmd(step)
            else:
                cmd = self._build_partition_cmd(step)

            if not cmd:
                self.log_signal.emit(f"   ✖ Could not build command for step '{name}'")
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
                    self.log_signal.emit(f"   ✔ Success\n")
                    self.step_signal.emit(i, self.SUCCESS)
                else:
                    self.log_signal.emit(f"   ✖ Failed (code {rc})\n")
                    self.step_signal.emit(i, self.FAILED)
                    # Continue with the next step (EDL flashing usually does not stop midway)

            except Exception as e:
                self.log_signal.emit(f"   ✖ Error: {e}\n")
                self.step_signal.emit(i, self.FAILED)

            self.progress_signal.emit(int((i + 1) * 100 / total))

        # Check whether any step failed
        self.log_signal.emit("═══ EDL FLASH COMPLETE ═══")
        self.finished_signal.emit(True, "EDL Flash completed!")


class EdlResetWorker(QThread):
    """Worker that resets the device after EDL flashing."""
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, edl_path, loader_path):
        super().__init__()
        self.edl_path = edl_path
        self.loader_path = loader_path

    def run(self):
        base_cmd = build_edl_cmd(self.edl_path)
        if not base_cmd:
            self.finished_signal.emit(False, "EDL tool is not available.")
            return

        self.log_signal.emit("🔄 Resetting device...")
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
                self.log_signal.emit("✔ Reset command sent.")
                self.finished_signal.emit(True, "Device is rebooting.")
            else:
                err = result.stderr.strip() or result.stdout.strip() or "Unknown error"
                self.log_signal.emit(f"⚠ {err}")
                self.finished_signal.emit(False, err)
        except Exception as e:
            self.log_signal.emit(f"✖ Error: {e}")
            self.finished_signal.emit(False, str(e))
