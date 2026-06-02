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
    ask_continue_signal = pyqtSignal(str)  # Ask the user when flashing fails

    PENDING, RUNNING, SUCCESS, FAILED = 0, 1, 2, 3

    # Log flush interval in seconds - batch multiple lines into one emit
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
        # Batch log buffer
        self._log_buffer = []
        self._log_lock = threading.Lock()

    def reply_continue(self, yes: bool):
        """The UI calls this method to answer whether flashing should continue."""
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
        """Send the full log buffer to the UI as one emit."""
        with self._log_lock:
            if not self._log_buffer:
                return
            batch = "\n".join(self._log_buffer)
            self._log_buffer.clear()
        self.log_signal.emit(batch)

    def _buffered_log(self, text):
        """Add a log line to the buffer. It will be flushed on an interval."""
        with self._log_lock:
            self._log_buffer.append(text)

    def _stream_output(self, proc):
        """Read process stdout and stderr in real time.
        Fastboot writes primary output to stderr, so both streams must be read.
        Batch logging reduces UI thread load."""

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

        # Wait for the process to finish and flush logs on an interval
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
        # Flush remaining logs after the process exits
        self._flush_log()

        return proc.returncode if proc.returncode is not None else -1

    def run(self):
        if not self.steps:
            self.finished_signal.emit(False, "No flash steps.")
            return
        if not self.manager.is_available():
            self.finished_signal.emit(False, "ADB/Fastboot is not available.")
            return

        total = len(self.steps)
        self.log_signal.emit("═══ STARTING FLASH ═══\n")

        for i, step in enumerate(self.steps):
            if self._cancelled:
                self.log_signal.emit("\n✖ Cancelled by the user.")
                self.finished_signal.emit(False, "Cancelled.")
                return

            name = step.get("name", f"Step {i+1}")
            exe = self.manager.adb_path if step.get("type") == "ADB" else self.manager.fastboot_path
            args = list(step.get("args", []))

            cmd = [exe]
            if self.serial:
                cmd += ["-s", self.serial]
            cmd += args

            self.log_signal.emit(f"── [{i+1}/{total}] {name}")
            self.step_signal.emit(i, self.RUNNING)

            try:
                # Create a process with separate stdout and stderr streams for real-time output.
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
                    # Ask whether the user wants to continue (5 minute timeout)
                    self._continue_event.clear()
                    self.ask_continue_signal.emit(name)
                    if not self._continue_event.wait(timeout=300):
                        # Timeout -> stop flashing.
                        self.log_signal.emit("   ⏱ Timeout: no response, stopping flash.\n")
                        self.finished_signal.emit(False, f"Timeout at step '{name}'")
                        return
                    if not self._continue_answer:
                        self.finished_signal.emit(False, f"Stopped at step '{name}' (code {rc})")
                        return
                    self.log_signal.emit(f"   ▶ Continuing flash...\n")
            except Exception as e:
                self.log_signal.emit(f"   ✖ Error: {e}\n")
                self.step_signal.emit(i, self.FAILED)
                # Ask whether the user wants to continue (5 minute timeout)
                self._continue_event.clear()
                self.ask_continue_signal.emit(name)
                if not self._continue_event.wait(timeout=300):
                    self.log_signal.emit("   ⏱ Timeout: no response, stopping flash.\n")
                    self.finished_signal.emit(False, f"Timeout at step '{name}'")
                    return
                if not self._continue_answer:
                    self.finished_signal.emit(False, str(e))
                    return
                self.log_signal.emit(f"   ▶ Continuing flash...\n")

            self.progress_signal.emit(int((i + 1) * 100 / total))

        self.log_signal.emit("═══ COMPLETE ═══")
        self.finished_signal.emit(True, "Flash completed successfully!")
