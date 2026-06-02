import os
import sys
import platform
import shutil
import time
import urllib.error
import urllib.request
import zipfile
import logging
from PyQt6.QtCore import QThread, pyqtSignal


class PlatformToolsDownloader(QThread):
    """Download Platform Tools from Google with automatic OS detection."""
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    # OS-specific URLs
    URLS = {
        "windows": "https://dl.google.com/android/repository/platform-tools_r35.0.2-windows.zip",
        "linux":   "https://dl.google.com/android/repository/platform-tools_r35.0.2-linux.zip",
        "darwin":  "https://dl.google.com/android/repository/platform-tools_r35.0.2-darwin.zip",
    }

    def __init__(self, dest_dir="."):
        super().__init__()
        self.dest_dir = os.path.abspath(dest_dir)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    @staticmethod
    def _get_os_key():
        """Return the OS key for the URLS dictionary."""
        if sys.platform == 'win32':
            return "windows"
        elif sys.platform == 'darwin':
            return "darwin"
        else:
            return "linux"

    @staticmethod
    def _get_os_label():
        """Display OS name for the user."""
        if sys.platform == 'win32':
            return "Windows"
        elif sys.platform == 'darwin':
            return "macOS"
        else:
            return "Linux"

    def _download_with_retries(self, req, zip_path, os_label, attempts=3):
        last_error = None
        for attempt in range(1, attempts + 1):
            if self._is_cancelled:
                return False
            if os.path.exists(zip_path):
                os.remove(zip_path)
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    total_size = int(response.info().get('Content-Length', 0))
                    downloaded = 0
                    block_size = 8192

                    self.status_signal.emit(
                        f"Downloading Platform Tools for {os_label} (~8MB)..."
                    )

                    with open(zip_path, 'wb') as out_file:
                        while True:
                            if self._is_cancelled:
                                out_file.close()
                                if os.path.exists(zip_path):
                                    os.remove(zip_path)
                                return False

                            buffer = response.read(block_size)
                            if not buffer:
                                break

                            downloaded += len(buffer)
                            out_file.write(buffer)

                            if total_size > 0:
                                percent = int(downloaded * 100 / total_size)
                                self.progress_signal.emit(percent)

                if not os.path.isfile(zip_path) or os.path.getsize(zip_path) <= 0:
                    raise RuntimeError("Downloaded Platform Tools archive is missing or empty.")
                return True
            except urllib.error.HTTPError as e:
                last_error = e
                logging.warning(
                    "Platform Tools download HTTP %s on attempt %s/%s: %s",
                    e.code, attempt, attempts, e.reason
                )
                if e.code == 403:
                    self.status_signal.emit(
                        "Download was blocked by the server. Retrying..."
                    )
            except Exception as e:
                last_error = e
                logging.warning(
                    "Platform Tools download failed on attempt %s/%s: %s",
                    attempt, attempts, e
                )
            if os.path.exists(zip_path):
                os.remove(zip_path)
            if attempt < attempts:
                self.status_signal.emit(
                    f"Download failed. Retrying ({attempt + 1}/{attempts})..."
                )
                time.sleep(2 * attempt)
        if last_error:
            raise last_error
        return None

    def run(self):
        os_key = self._get_os_key()
        url = self.URLS.get(os_key)
        if not url:
            self.finished_signal.emit(False, f"Unsupported operating system: {sys.platform}")
            return

        zip_path = os.path.join(self.dest_dir, f"platform-tools-r35.0.2-{os_key}.zip")
        os_label = self._get_os_label()
        local_pt_path = os.path.join(self.dest_dir, "platform-tools")
        adb_name = "adb.exe" if sys.platform == 'win32' else "adb"
        fastboot_name = "fastboot.exe" if sys.platform == 'win32' else "fastboot"
        required_files = [
            os.path.join(local_pt_path, adb_name),
            os.path.join(local_pt_path, fastboot_name),
        ]

        try:
            if not os.path.exists(self.dest_dir):
                os.makedirs(self.dest_dir)

            self.status_signal.emit("Connecting to Google servers...")

            # OS-specific User-Agent
            ua = {
                "windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "darwin":  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "linux":   "Mozilla/5.0 (X11; Linux x86_64)",
            }
            req = urllib.request.Request(url, headers={'User-Agent': ua.get(os_key, ua["linux"])})

            if not self._download_with_retries(req, zip_path, os_label):
                self.status_signal.emit("Download cancelled.")
                self.finished_signal.emit(False, "Download cancelled by the user.")
                return

            # Extract
            self.status_signal.emit("Download complete. Extracting...")
            self.progress_signal.emit(95)

            if os.path.isdir(local_pt_path):
                shutil.rmtree(local_pt_path)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.dest_dir)

            missing = [path for path in required_files if not os.path.isfile(path)]
            if missing:
                if os.path.isdir(local_pt_path):
                    shutil.rmtree(local_pt_path)
                raise RuntimeError(
                    "Platform Tools extraction is incomplete. Missing: "
                    + ", ".join(os.path.basename(path) for path in missing)
                )

            # Remove temporary zip file
            if os.path.exists(zip_path):
                os.remove(zip_path)

            # On Linux/macOS: chmod +x for adb and fastboot
            if sys.platform != 'win32':
                import stat
                for tool in ("adb", "fastboot"):
                    tool_path = os.path.join(local_pt_path, tool)
                    if os.path.isfile(tool_path):
                        st = os.stat(tool_path)
                        os.chmod(tool_path,
                                 st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            self.progress_signal.emit(100)
            self.status_signal.emit(f"ADB & Fastboot setup completed ({os_label})!")
            self.finished_signal.emit(True, local_pt_path)

        except Exception as e:
            logging.error(f"Failed to download Platform Tools: {e}")
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            self.status_signal.emit(f"Error: {str(e)}")
            self.finished_signal.emit(False, f"Download failed: {str(e)}")
