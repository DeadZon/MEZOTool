import os
import sys
import platform
import urllib.request
import zipfile
import logging
from PyQt6.QtCore import QThread, pyqtSignal


class PlatformToolsDownloader(QThread):
    """Tải Platform Tools từ Google, tự phát hiện hệ điều hành."""
    progress_signal = pyqtSignal(int)
    status_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    # URL theo hệ điều hành
    URLS = {
        "windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
        "linux":   "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
        "darwin":  "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    }

    def __init__(self, dest_dir="."):
        super().__init__()
        self.dest_dir = os.path.abspath(dest_dir)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    @staticmethod
    def _get_os_key():
        """Trả về key OS cho dict URLS."""
        if sys.platform == 'win32':
            return "windows"
        elif sys.platform == 'darwin':
            return "darwin"
        else:
            return "linux"

    @staticmethod
    def _get_os_label():
        """Tên OS hiển thị cho người dùng."""
        if sys.platform == 'win32':
            return "Windows"
        elif sys.platform == 'darwin':
            return "macOS"
        else:
            return "Linux"

    def run(self):
        os_key = self._get_os_key()
        url = self.URLS.get(os_key)
        if not url:
            self.finished_signal.emit(False, f"Không hỗ trợ hệ điều hành: {sys.platform}")
            return

        zip_path = os.path.join(self.dest_dir, f"platform-tools-latest-{os_key}.zip")
        os_label = self._get_os_label()

        try:
            if not os.path.exists(self.dest_dir):
                os.makedirs(self.dest_dir)

            self.status_signal.emit("Đang kết nối tới máy chủ Google...")

            # User-Agent phù hợp theo OS
            ua = {
                "windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "darwin":  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "linux":   "Mozilla/5.0 (X11; Linux x86_64)",
            }
            req = urllib.request.Request(url, headers={'User-Agent': ua.get(os_key, ua["linux"])})

            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                block_size = 8192

                self.status_signal.emit(
                    f"Đang tải Platform Tools cho {os_label} (~8MB)...")

                with open(zip_path, 'wb') as out_file:
                    while True:
                        if self._is_cancelled:
                            out_file.close()
                            if os.path.exists(zip_path):
                                os.remove(zip_path)
                            self.status_signal.emit("Đã hủy tải xuống.")
                            self.finished_signal.emit(False, "Đã hủy tải xuống bởi người dùng.")
                            return

                        buffer = response.read(block_size)
                        if not buffer:
                            break

                        downloaded += len(buffer)
                        out_file.write(buffer)

                        if total_size > 0:
                            percent = int(downloaded * 100 / total_size)
                            self.progress_signal.emit(percent)

            # Giải nén
            self.status_signal.emit("Tải thành công! Đang giải nén...")
            self.progress_signal.emit(95)

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.dest_dir)

            # Xóa file zip tạm
            if os.path.exists(zip_path):
                os.remove(zip_path)

            local_pt_path = os.path.join(self.dest_dir, "platform-tools")

            # Trên Linux/macOS: chmod +x cho adb và fastboot
            if sys.platform != 'win32':
                import stat
                for tool in ("adb", "fastboot"):
                    tool_path = os.path.join(local_pt_path, tool)
                    if os.path.isfile(tool_path):
                        st = os.stat(tool_path)
                        os.chmod(tool_path,
                                 st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            self.progress_signal.emit(100)
            self.status_signal.emit(f"Đã hoàn tất thiết lập ADB & Fastboot ({os_label})!")
            self.finished_signal.emit(True, local_pt_path)

        except Exception as e:
            logging.error(f"Lỗi tải Platform Tools: {e}")
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except Exception:
                    pass
            self.status_signal.emit(f"Lỗi: {str(e)}")
            self.finished_signal.emit(False, f"Lỗi trong quá trình tải xuống: {str(e)}")
