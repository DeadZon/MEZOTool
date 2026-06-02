import os
import time
from datetime import datetime
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
                             QTextEdit, QFrame, QLabel, QGridLayout, QScrollArea,
                             QSizePolicy)
from qfluentwidgets import (TitleLabel, BodyLabel, SubtitleLabel, SimpleCardWidget,
                            PrimaryPushButton, PushButton, InfoBar, InfoBarPosition,
                            CheckBox, ProgressBar, MessageBox)
from core.adb_fastboot import ADBFastbootManager
from core.flash_worker import FlashWorker
from ui.styles import Styles
from core.device_monitor import DeviceMonitor


# Các phân vùng KHÔNG thêm hậu tố _a/_b
NO_SLOT_PARTITIONS = {"cust", "super", "userdata", "metadata", "frp"}


class DeviceInfoWorker(QThread):
    """Thread lấy thông tin thiết bị để không block main thread.
    get_device_info() gọi nhiều subprocess (getprop/getvar), có thể
    treo khi thiết bị bận → không nên chạy trên main thread."""
    info_signal = pyqtSignal(dict, dict)  # (device_dict, info_dict)

    def __init__(self, manager: ADBFastbootManager, device: dict):
        super().__init__()
        self.manager = manager
        self.device = device

    def run(self):
        try:
            info = self.manager.get_device_info(self.device["serial"], self.device["mode"])
            self.info_signal.emit(self.device, info)
        except Exception:
            # Trả về info mặc định nếu lỗi
            self.info_signal.emit(self.device, {
                "serial": self.device["serial"], "mode": self.device["mode"],
                "name": "N/A", "codename": "N/A", "bootloader": "N/A", "slot": "N/A"
            })


class RebootWaiter(QThread):
    """Thread đợi thiết bị chuyển sang FASTBOOT sau khi reboot."""
    done_signal = pyqtSignal(bool, str)  # (success, serial_or_error)
    log_signal = pyqtSignal(str)

    def __init__(self, manager: ADBFastbootManager, serial: str, timeout=30):
        super().__init__()
        self.manager = manager
        self.serial = serial
        self.timeout = timeout

    def run(self):
        self.log_signal.emit("⏳ Đang đợi thiết bị chuyển sang FASTBOOT...")
        start = time.time()
        while time.time() - start < self.timeout:
            devs = self.manager.get_devices()
            for d in devs:
                if d["mode"] == "FASTBOOT":
                    self.log_signal.emit(f"✔ Thiết bị đã vào FASTBOOT: {d['serial']}")
                    self.done_signal.emit(True, d["serial"])
                    return
            self.msleep(2000)
        self.done_signal.emit(False, "Timeout: Thiết bị không chuyển sang FASTBOOT trong 30 giây.")


class DashboardPage(QWidget):
    """Bảng điều khiển chính: panel điều khiển bên trái, log bên phải."""

    def __init__(self, manager: ADBFastbootManager, parent=None):
        super().__init__(parent=parent)
        self.manager = manager
        self.monitor = None  # Sẽ được set từ MainWindow
        self.worker = None
        self.connected_devices = []
        self.active_device = None
        self.selected_rom_dir = None
        self.detected_images = []
        self.step_labels = []
        self._reboot_btns = []
        self._device_product = ""  # Lưu product name cho log
        self._reboot_waiter = None
        self._info_worker = None  # Background thread cho get_device_info
        # Batch log buffer
        self._log_buffer = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(100)  # Flush mỗi 100ms
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self.setObjectName("dashboard_page")
        self._build()

    # ══════════════════════════════════════════════════════════
    #  BUILD UI
    # ══════════════════════════════════════════════════════════
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Bên trái: Bảng điều khiển (scroll) ──
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        left_widget = QWidget()
        left_widget.setObjectName("dashboardLeft")
        left_widget.setStyleSheet("#dashboardLeft { background: transparent; }")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(24, 20, 12, 20)
        left_layout.setSpacing(14)

        left_layout.addWidget(TitleLabel("Bảng điều khiển"))

        # ─── 1. Trạng thái thiết bị ───
        self.status_frame = QFrame()
        self.status_frame.setObjectName("statusFrame")
        self.status_frame.setMinimumHeight(120)
        sl = QVBoxLayout(self.status_frame)
        sl.setContentsMargins(20, 16, 20, 16)

        self.status_icon = QLabel("📵")
        self.status_icon.setStyleSheet("font-size: 28px; background: transparent;")
        sl.addWidget(self.status_icon)

        self.status_title = QLabel("Chưa phát hiện thiết bị")
        self.status_title.setStyleSheet("font-size: 15px; font-weight: bold; background: transparent;")
        sl.addWidget(self.status_title)

        self.status_desc = QLabel("Kết nối điện thoại Android qua cáp USB.")
        self.status_desc.setWordWrap(True)
        self.status_desc.setStyleSheet("font-size: 12px; background: transparent;")
        sl.addWidget(self.status_desc)

        # Device info grid
        grid = QGridLayout()
        grid.setSpacing(6)
        labels = ["Serial:", "Name:", "Codename:", "Bootloader:", "Slot:"]
        self._info_vals = []
        for i, lbl in enumerate(labels):
            l = QLabel(lbl)
            l.setStyleSheet("font-weight: bold; font-size: 11px; background: transparent;")
            v = QLabel("—")
            v.setStyleSheet("font-size: 11px; background: transparent;")
            grid.addWidget(l, i, 0)
            grid.addWidget(v, i, 1)
            self._info_vals.append(v)
        sl.addLayout(grid)

        left_layout.addWidget(self.status_frame)

        # ─── 2. Chọn thư mục ROM ───
        rom_card = SimpleCardWidget()
        rcl = QVBoxLayout(rom_card)
        rcl.setContentsMargins(16, 14, 16, 14)
        rcl.addWidget(SubtitleLabel("1. Chọn thư mục ROM"))
        self.btn_dir = PushButton("📁 Chọn thư mục")
        self.btn_dir.clicked.connect(self._pick_dir)
        rcl.addWidget(self.btn_dir)
        left_layout.addWidget(rom_card)

        # ─── 3. Tùy chọn flash ───
        opt_card = SimpleCardWidget()
        ol = QVBoxLayout(opt_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.addWidget(SubtitleLabel("2. Tùy chọn Flash"))

        self.chk_wipe = CheckBox("🗑 Xóa sạch dữ liệu (Clean Flash)")
        self.chk_wipe.setStyleSheet(Styles.wipe_checkbox())
        ol.addWidget(self.chk_wipe)
        left_layout.addWidget(opt_card)

        # ─── 4. Khởi động nhanh ───
        reboot_card = SimpleCardWidget()
        rl = QVBoxLayout(reboot_card)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.addWidget(SubtitleLabel("Khởi động nhanh"))
        bl = QGridLayout()
        bl.setSpacing(8)
        btns = [
            ("🔄  Hệ thống", "system"),
            ("⚡  Bootloader", "bootloader"),
            ("🛠  Recovery", "recovery"),
            ("🔧  Fastbootd", "fastboot"),
        ]
        for idx, (text, target) in enumerate(btns):
            b = PushButton(text)
            b.setMinimumHeight(36)
            b.setEnabled(False)
            b.clicked.connect(lambda _, t=target: self._reboot(t))
            bl.addWidget(b, idx // 2, idx % 2)
        rl.addLayout(bl)
        self._reboot_btns = [bl.itemAt(i).widget() for i in range(bl.count())]
        left_layout.addWidget(reboot_card)

        # ─── 5. Nút Flash + Progress ───
        action_card = SimpleCardWidget()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(16, 12, 16, 12)

        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        al.addWidget(self.progress)

        br = QHBoxLayout()
        self.btn_flash = PrimaryPushButton("⚡ Bắt đầu Flash ROM")
        self.btn_flash.setMinimumHeight(44)
        self.btn_flash.setEnabled(False)
        self.btn_flash.clicked.connect(self._confirm)
        self.btn_cancel = PushButton("Hủy")
        self.btn_cancel.setMinimumHeight(44)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        br.addWidget(self.btn_flash, 2)
        br.addWidget(self.btn_cancel, 1)
        al.addLayout(br)
        left_layout.addWidget(action_card)
        self._set_disconnected()

        left_layout.addStretch()
        left_scroll.setWidget(left_widget)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root.addWidget(left_scroll, 2)

        # ── Bên phải: Log terminal ──
        right_widget = QWidget()
        right_widget.setObjectName("dashboardRight")
        right_widget.setStyleSheet("#dashboardRight { background: transparent; }")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(12, 20, 24, 20)
        right_layout.setSpacing(10)

        right_layout.addWidget(SubtitleLabel("📋 Nhật ký (Log)"))

        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(Styles.terminal())
        right_layout.addWidget(self.terminal, 1)

        # Checklist tiến trình
        cl_card = SimpleCardWidget()
        cl_layout = QVBoxLayout(cl_card)
        cl_layout.setContentsMargins(12, 10, 12, 10)
        cl_layout.addWidget(BodyLabel("Tiến trình:"))
        self.checklist_area = QScrollArea()
        self.checklist_area.setWidgetResizable(True)
        self.checklist_area.setMaximumHeight(140)
        self.checklist_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.checklist_widget = QWidget()
        self.checklist_widget.setStyleSheet("background: transparent;")
        self.checklist_layout = QVBoxLayout(self.checklist_widget)
        self.checklist_layout.setContentsMargins(4, 4, 4, 4)
        self.checklist_layout.setSpacing(4)
        self.checklist_area.setWidget(self.checklist_widget)
        cl_layout.addWidget(self.checklist_area)
        right_layout.addWidget(cl_card)

        # Buttons dưới terminal
        tr = QHBoxLayout()
        b1 = PushButton("📋 Copy Log")
        b1.clicked.connect(self._copy)
        b2 = PushButton("🗑 Xóa Log")
        b2.clicked.connect(self.terminal.clear)
        b3 = PushButton("💾 Xuất Log")
        b3.clicked.connect(self._save_log)
        tr.addWidget(b1)
        tr.addWidget(b2)
        tr.addWidget(b3)
        tr.addStretch()
        right_layout.addLayout(tr)

        right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(right_widget, 3)

    # ══════════════════════════════════════════════════════════
    #  DEVICE STATUS
    # ══════════════════════════════════════════════════════════
    def _set_disconnected(self):
        self.status_frame.setStyleSheet(Styles.status_disconnected())
        self.status_icon.setText("📵")
        self.status_title.setText("Chưa phát hiện thiết bị")
        self.status_desc.setText("Kết nối điện thoại Android qua cáp USB.")
        for v in self._info_vals:
            v.setText("—")
        for b in self._reboot_btns:
            b.setEnabled(False)
        self.btn_flash.setEnabled(False)

    def set_monitor(self, monitor: DeviceMonitor):
        """Gắn reference đến DeviceMonitor để có thể pause/resume."""
        self.monitor = monitor

    def update_devices(self, devices):
        self.connected_devices = devices
        if not devices:
            self.active_device = None
            self._set_disconnected()
            return

        d = devices[0]
        self.active_device = d
        self.status_frame.setStyleSheet(Styles.status_connected())
        self.status_icon.setText("📱")
        self.status_title.setText(f"Đã kết nối ({d['mode']})")
        self.status_desc.setText(f"Serial: {d['serial']}  —  Trạng thái: {d['state'].upper()}")

        for b in self._reboot_btns:
            b.setEnabled(True)
        self.btn_flash.setEnabled(True)

        # Lấy device info trong background thread thay vì main thread
        # Tránh block UI khi subprocess bị treo
        self._info_worker = DeviceInfoWorker(self.manager, d)
        self._info_worker.info_signal.connect(self._on_device_info)
        self._info_worker.start()

    def _on_device_info(self, device, info):
        """Callback khi lấy thông tin thiết bị xong (chạy trên main thread)."""
        # Chỉ cập nhật nếu thiết bị vẫn là active device
        if self.active_device and self.active_device.get("serial") == device.get("serial"):
            self._info_vals[0].setText(info["serial"])
            self._info_vals[1].setText(info["name"])
            self._info_vals[2].setText(info["codename"])
            self._info_vals[3].setText(info["bootloader"])
            self._info_vals[4].setText(info["slot"])

            # Lưu codename để dùng khi lưu log
            codename = info.get("codename", "")
            if codename and codename != "N/A":
                self._device_product = codename

    def _reboot(self, target):
        if not self.active_device:
            return
        s = self.active_device["serial"]
        m = self.active_device["mode"]

        if m == "ADB":
            args = ["-s", s, "reboot"] + ([target] if target != "system" else [])
            _, err, rc = self.manager.run_adb(args)
        else:
            args = ["-s", s, "reboot"] + ([target] if target != "system" else [])
            _, err, rc = self.manager.run_fastboot(args)

        if rc == 0:
            InfoBar.success("Đã gửi lệnh", f"Đang reboot vào {target}.",
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Thất bại", err or "Lỗi không xác định.",
                        position=InfoBarPosition.TOP, parent=self.window())

    # ══════════════════════════════════════════════════════════
    #  ROM SELECTION & SCANNING
    # ══════════════════════════════════════════════════════════
    def _pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Chọn thư mục ROM")
        if p:
            self.selected_rom_dir = p
            self.btn_dir.setText(f"📁 {os.path.basename(p)}")
            self._scan(p)

    def _scan(self, d):
        self.detected_images = []
        try:
            files = [f for f in os.listdir(d) if f.lower().endswith(('.img', '.bin'))]
            # Cũng quét thư mục images/ (giống bat file)
            images_dir = os.path.join(d, "images")
            if os.path.isdir(images_dir):
                img_files = [f for f in os.listdir(images_dir) if f.lower().endswith(('.img', '.bin'))]
                for f in img_files:
                    if f not in files:
                        files.append(f)
                        self.detected_images.append({
                            "partition": os.path.splitext(f)[0],
                            "path": os.path.join(images_dir, f),
                            "name": f})
        except Exception:
            return

        prio = {"vbmeta": 0, "boot": 10, "init_boot": 11, "dtbo": 12,
                "recovery": 20, "super": 30, "system": 40, "vendor": 42, "userdata": 100}

        root_files = [f for f in os.listdir(d) if f.lower().endswith(('.img', '.bin'))]
        root_files.sort(key=lambda f: prio.get(os.path.splitext(f)[0].lower(), 50))
        for f in root_files:
            if not any(img["name"] == f for img in self.detected_images):
                self.detected_images.append({
                    "partition": os.path.splitext(f)[0],
                    "path": os.path.join(d, f),
                    "name": f})

    # ══════════════════════════════════════════════════════════
    #  CHECKLIST
    # ══════════════════════════════════════════════════════════
    def _clear_checklist(self):
        for lbl in self.step_labels:
            self.checklist_layout.removeWidget(lbl)
            lbl.deleteLater()
        self.step_labels.clear()

    def _add_step(self, text):
        lbl = BodyLabel(f"⏳ {text}")
        lbl.setStyleSheet(Styles.checklist_pending())
        self.checklist_layout.addWidget(lbl)
        self.step_labels.append(lbl)

    def _update_step(self, idx, status):
        if 0 <= idx < len(self.step_labels):
            lbl = self.step_labels[idx]
            txt = lbl.text()[2:]
            if status == FlashWorker.RUNNING:
                lbl.setText(f"🔄 {txt}")
                lbl.setStyleSheet(Styles.checklist_running())
            elif status == FlashWorker.SUCCESS:
                lbl.setText(f"✔ {txt}")
                lbl.setStyleSheet(Styles.checklist_success())
            elif status == FlashWorker.FAILED:
                lbl.setText(f"✖ {txt}")
                lbl.setStyleSheet(Styles.checklist_failed())
            # Tự động cuộn checklist đến bước hiện tại
            self.checklist_area.ensureWidgetVisible(lbl)

    # ══════════════════════════════════════════════════════════
    #  FLASH LOGIC (dựa trên file bat)
    # ══════════════════════════════════════════════════════════
    def _confirm(self):
        if not self.selected_rom_dir or not self.detected_images:
            InfoBar.error("Lỗi", "Chưa chọn thư mục ROM hợp lệ.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return
        if not self.manager.is_available():
            InfoBar.error("Lỗi", "ADB/Fastboot chưa cấu hình. Vào Cài đặt để thiết lập.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        if not self.connected_devices:
            InfoBar.error("Lỗi", "Không có thiết bị nào được kết nối.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        d = self.connected_devices[0]

        # Nếu thiết bị đang ở ADB (system/recovery) → tự động reboot bootloader
        if d["mode"] == "ADB":
            msg = (f"Thiết bị đang ở chế độ {d['state'].upper()} (ADB).\n"
                   "Sẽ tự động reboot vào Bootloader để flash.\n\n")
        else:
            msg = ""

        msg += "Flash ROM sẽ ghi đè hệ điều hành hiện tại."
        if self.chk_wipe.isChecked():
            msg += "\n\n⚠ CLEAN FLASH: Toàn bộ dữ liệu sẽ bị xóa sạch!"
        msg += f"\n\nSẽ flash {len(self.detected_images)} phân vùng.\nTiếp tục?"

        box = MessageBox("Xác nhận Flash ROM", msg, self.window())
        box.yesButton.setText("⚡ Flash")
        box.cancelButton.setText("Hủy")
        if box.exec():
            if d["mode"] == "ADB":
                # Reboot vào bootloader rồi đợi
                self._set_busy(True)
                self.terminal.clear()
                self.terminal.append("🔄 Đang reboot thiết bị vào Bootloader...")
                ok, err = self.manager.reboot_to_bootloader(d["serial"], d["mode"])
                if not ok:
                    self.terminal.append(f"✖ Không thể reboot: {err}")
                    self._set_busy(False)
                    InfoBar.error("Lỗi", f"Không thể reboot vào bootloader: {err}",
                                position=InfoBarPosition.TOP, parent=self.window())
                    return
                # Đợi thiết bị chuyển sang FASTBOOT
                self._reboot_waiter = RebootWaiter(self.manager, d["serial"])
                self._reboot_waiter.log_signal.connect(self.terminal.append)
                self._reboot_waiter.done_signal.connect(self._on_reboot_done)
                self._reboot_waiter.start()
            else:
                # Đã ở FASTBOOT, flash luôn
                self._run(d["serial"])

    def _on_reboot_done(self, success, serial_or_error):
        """Callback khi thiết bị đã reboot xong vào bootloader."""
        self._reboot_waiter = None
        if success:
            self.terminal.append("")
            self._run(serial_or_error)
        else:
            self._set_busy(False)
            self.terminal.append(f"✖ {serial_or_error}")
            InfoBar.error("Lỗi", serial_or_error,
                        position=InfoBarPosition.TOP, parent=self.window())

    def _build_flash_args(self, partition, path):
        """Tạo args cho lệnh flash. 
        - super: dùng 'flash super' + --skip-secondary để tránh treo
        - cust: flash không có hậu tố _a/_b
        - Các phân vùng khác: flash cả _a và _b
        """
        part_lower = partition.lower()
        steps = []

        if part_lower == "super":
            # Flash super không có slot, thêm --skip-secondary để tránh treo
            steps.append({
                "name": f"Flash {partition}",
                "type": "FASTBOOT",
                "args": ["flash", partition, path]
            })
        elif part_lower in NO_SLOT_PARTITIONS:
            # cust, userdata, metadata, frp: không thêm hậu tố slot
            steps.append({
                "name": f"Flash {partition}",
                "type": "FASTBOOT",
                "args": ["flash", partition, path]
            })
        else:
            # Các phân vùng khác: flash cả slot _a và _b
            steps.append({
                "name": f"Flash {partition}_a",
                "type": "FASTBOOT",
                "args": ["flash", f"{partition}_a", path]
            })
            steps.append({
                "name": f"Flash {partition}_b",
                "type": "FASTBOOT",
                "args": ["flash", f"{partition}_b", path]
            })

        return steps

    def _run(self, serial):
        steps = []
        self._clear_checklist()

        # Lấy thông tin thiết bị để hiển thị trong log
        info = self.manager.get_device_info(serial, "FASTBOOT")
        codename = info.get("codename", "N/A")
        if codename and codename != "N/A":
            self._device_product = codename
        self.terminal.append(f"📱 Thiết bị: {info.get('name', 'N/A')}")
        self.terminal.append(f"📦 Codename: {info.get('codename', 'N/A')}")
        self.terminal.append(f"🔐 Bootloader: {info.get('bootloader', 'N/A')}")
        self.terminal.append(f"💾 Slot: {info.get('slot', 'N/A')}")
        self.terminal.append("")

        # Flash từng image với hậu tố _a/_b (trừ cust và super)
        for img in self.detected_images:
            part = img["partition"]
            path = img["path"]
            flash_steps = self._build_flash_args(part, path)
            for fs in flash_steps:
                steps.append(fs)
                self._add_step(fs["name"])

        # Wipe nếu chọn Clean Flash (giống bat: erase frp, userdata, metadata)
        if self.chk_wipe.isChecked():
            for part in ["frp", "userdata", "metadata"]:
                steps.append({"name": f"Erase {part}", "type": "FASTBOOT",
                              "args": ["erase", part]})
                self._add_step(f"Erase {part}")

        # Reboot
        steps.append({"name": "Reboot", "type": "FASTBOOT", "args": ["reboot"]})
        self._add_step("Reboot hệ thống")

        self._set_busy(True)
        self.progress.setValue(0)

        # Pause monitor khi đang flash — tránh poll thiết bị đang bận
        if self.monitor:
            self.monitor.pause()

        # Bật batch log timer
        self._log_buffer.clear()
        self._log_timer.start()

        self.worker = FlashWorker(self.manager, steps, serial=serial)
        self.worker.log_signal.connect(self._on_log_line)
        self.worker.step_signal.connect(self._update_step)
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.finished_signal.connect(self._done)
        self.worker.ask_continue_signal.connect(self._ask_continue)
        self.worker.start()

    def _set_busy(self, busy):
        for w in (self.btn_flash, self.btn_dir, self.chk_wipe):
            w.setEnabled(not busy)
        for b in self._reboot_btns:
            b.setEnabled(not busy and self.active_device is not None)
        self.btn_cancel.setEnabled(busy)
        self.progress.setVisible(busy)

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()

    def _ask_continue(self, step_name):
        """Hiện hộp thoại hỏi user có muốn tiếp tục flash khi gặp lỗi."""
        box = MessageBox(
            "Flash phân vùng thất bại",
            f"Bước '{step_name}' bị lỗi.\n\nBạn có muốn bỏ qua và tiếp tục flash các phân vùng còn lại không?",
            self.window()
        )
        box.yesButton.setText("Tiếp tục")
        box.cancelButton.setText("Dừng lại")
        result = box.exec()
        self.worker.reply_continue(result)

    def _on_log_line(self, text):
        """Buffer log line, sẽ flush bởi QTimer mỗi 100ms."""
        self._log_buffer.append(text)

    def _flush_log_buffer(self):
        """Flush toàn bộ log buffer vào terminal 1 lần — giảm re-render."""
        if not self._log_buffer:
            return
        batch = self._log_buffer.copy()
        self._log_buffer.clear()
        # Tắt cập nhật UI trong khi batch insert
        self.terminal.setUpdatesEnabled(False)
        for line in batch:
            self.terminal.append(line)
        self.terminal.setUpdatesEnabled(True)

    def _done(self, ok, msg):
        # Dừng batch log timer và flush log còn lại
        self._log_timer.stop()
        self._flush_log_buffer()

        # Resume monitor sau khi flash xong
        if self.monitor:
            self.monitor.resume()

        self._set_busy(False)
        if ok:
            for i in range(len(self.step_labels)):
                self._update_step(i, FlashWorker.SUCCESS)
            InfoBar.success("Flash ROM thành công! 🏆", msg,
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Lỗi Flash", msg,
                        position=InfoBarPosition.TOP, parent=self.window())

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.terminal.toPlainText())
        InfoBar.success("Đã copy", "", duration=1500,
                      position=InfoBarPosition.TOP, parent=self.window())

    def _save_log(self):
        """Xuất log ra file .txt với tên: date_time_product.txt
        Sử dụng product name đã lưu từ getvar/getprop thay vì query lại."""
        text = self.terminal.toPlainText().strip()
        if not text:
            InfoBar.info("Trống", "Chưa có nội dung log.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        # Tạo tên file: date_time_product
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Dùng codename đã lưu sẵn (từ getvar product / getprop ro.product.device)
        model = self._device_product if self._device_product else ""
        if not model:
            # Fallback: thử lấy từ thiết bị hiện tại
            if self.active_device:
                info = self.manager.get_device_info(
                    self.active_device["serial"], self.active_device["mode"])
                model = info.get("codename", "")
                if model and model != "N/A":
                    self._device_product = model
        if not model or model == "N/A":
            model = "unknown"
        # Loại bỏ ký tự không hợp lệ trong tên file
        model = model.replace(" ", "_").replace("/", "-").replace("\\", "-")
        default_name = f"{now}_{model}.txt"

        path, _ = QFileDialog.getSaveFileName(
            self, "Xuất Log", default_name,
            "Text Files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                InfoBar.success("Đã lưu", os.path.basename(path),
                              position=InfoBarPosition.TOP, parent=self.window())
            except Exception as e:
                InfoBar.error("Lỗi", str(e),
                            position=InfoBarPosition.TOP, parent=self.window())
