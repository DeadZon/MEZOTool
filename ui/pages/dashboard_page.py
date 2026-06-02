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


# Partitions that must NOT receive the _a/_b suffix
NO_SLOT_PARTITIONS = {"cust", "super", "userdata", "metadata", "frp"}


class DeviceInfoWorker(QThread):
    """Thread that fetches device information without blocking the main thread.
    get_device_info() calls multiple subprocesses (getprop/getvar) and can hang
    when the device is busy, so it should not run on the main thread."""
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
            # Return default info on error
            self.info_signal.emit(self.device, {
                "serial": self.device["serial"], "mode": self.device["mode"],
                "name": "N/A", "codename": "N/A", "bootloader": "N/A", "slot": "N/A"
            })


class RebootWaiter(QThread):
    """Thread that waits for the device to switch to FASTBOOT after reboot."""
    done_signal = pyqtSignal(bool, str)  # (success, serial_or_error)
    log_signal = pyqtSignal(str)

    def __init__(self, manager: ADBFastbootManager, serial: str, timeout=30):
        super().__init__()
        self.manager = manager
        self.serial = serial
        self.timeout = timeout

    def run(self):
        self.log_signal.emit("⏳ Waiting for the device to switch to FASTBOOT...")
        start = time.time()
        while time.time() - start < self.timeout:
            devs = self.manager.get_devices()
            for d in devs:
                if d["mode"] == "FASTBOOT":
                    self.log_signal.emit(f"✔ Device entered FASTBOOT: {d['serial']}")
                    self.done_signal.emit(True, d["serial"])
                    return
            self.msleep(2000)
        self.done_signal.emit(False, "Timeout: device did not switch to FASTBOOT within 30 seconds.")


class DashboardPage(QWidget):
    """Main dashboard: control panel on the left, log on the right."""

    def __init__(self, manager: ADBFastbootManager, parent=None):
        super().__init__(parent=parent)
        self.manager = manager
        self.monitor = None  # Set from MainWindow
        self.worker = None
        self.connected_devices = []
        self.active_device = None
        self.selected_rom_dir = None
        self.detected_images = []
        self.step_labels = []
        self._reboot_btns = []
        self._device_product = ""  # Store product name for logs
        self._reboot_waiter = None
        self._info_worker = None  # Background thread for get_device_info
        # Batch log buffer
        self._log_buffer = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(100)  # Flush every 100ms
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

        # ── Left side: dashboard controls (scroll) ──
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

        left_layout.addWidget(TitleLabel("Dashboard"))

        # ─── 1. Device status ───
        self.status_frame = QFrame()
        self.status_frame.setObjectName("statusFrame")
        self.status_frame.setMinimumHeight(120)
        sl = QVBoxLayout(self.status_frame)
        sl.setContentsMargins(20, 16, 20, 16)

        self.status_icon = QLabel("📵")
        self.status_icon.setStyleSheet("font-size: 28px; background: transparent;")
        sl.addWidget(self.status_icon)

        self.status_title = QLabel("No device detected")
        self.status_title.setStyleSheet("font-size: 15px; font-weight: bold; background: transparent;")
        sl.addWidget(self.status_title)

        self.status_desc = QLabel("Connect an Android phone with a USB cable.")
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

        # ─── 2. Select ROM Folder ───
        rom_card = SimpleCardWidget()
        rcl = QVBoxLayout(rom_card)
        rcl.setContentsMargins(16, 14, 16, 14)
        rcl.addWidget(SubtitleLabel("1. Select ROM Folder"))
        self.btn_dir = PushButton("📁 Select Folder")
        self.btn_dir.clicked.connect(self._pick_dir)
        rcl.addWidget(self.btn_dir)
        left_layout.addWidget(rom_card)

        # ─── 3. Flash Options ───
        opt_card = SimpleCardWidget()
        ol = QVBoxLayout(opt_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.addWidget(SubtitleLabel("2. Flash Options"))

        self.chk_wipe = CheckBox("🗑 Wipe data (Clean Flash)")
        self.chk_wipe.setStyleSheet(Styles.wipe_checkbox())
        ol.addWidget(self.chk_wipe)
        left_layout.addWidget(opt_card)

        # ─── 4. Quick Reboot ───
        reboot_card = SimpleCardWidget()
        rl = QVBoxLayout(reboot_card)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.addWidget(SubtitleLabel("Quick Reboot"))
        bl = QGridLayout()
        bl.setSpacing(8)
        btns = [
            ("🔄  System", "system"),
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

        # ─── 5. Flash button + progress ───
        action_card = SimpleCardWidget()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(16, 12, 16, 12)

        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        al.addWidget(self.progress)

        br = QHBoxLayout()
        self.btn_flash = PrimaryPushButton("⚡ Start ROM Flash")
        self.btn_flash.setMinimumHeight(44)
        self.btn_flash.setEnabled(False)
        self.btn_flash.clicked.connect(self._confirm)
        self.btn_cancel = PushButton("Cancel")
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

        # ── Right side: log terminal ──
        right_widget = QWidget()
        right_widget.setObjectName("dashboardRight")
        right_widget.setStyleSheet("#dashboardRight { background: transparent; }")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(12, 20, 24, 20)
        right_layout.setSpacing(10)

        right_layout.addWidget(SubtitleLabel("📋 Log"))

        self.terminal = QTextEdit()
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(Styles.terminal())
        right_layout.addWidget(self.terminal, 1)

        # Progress checklist
        cl_card = SimpleCardWidget()
        cl_layout = QVBoxLayout(cl_card)
        cl_layout.setContentsMargins(12, 10, 12, 10)
        cl_layout.addWidget(BodyLabel("Progress:"))
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

        # Buttons below terminal
        tr = QHBoxLayout()
        b1 = PushButton("📋 Copy Log")
        b1.clicked.connect(self._copy)
        b2 = PushButton("🗑 Clear Log")
        b2.clicked.connect(self.terminal.clear)
        b3 = PushButton("💾 Export Log")
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
        self.status_title.setText("No device detected")
        self.status_desc.setText("Connect an Android phone with a USB cable.")
        for v in self._info_vals:
            v.setText("—")
        for b in self._reboot_btns:
            b.setEnabled(False)
        self.btn_flash.setEnabled(False)

    def set_monitor(self, monitor: DeviceMonitor):
        """Attach a DeviceMonitor reference so flashing can pause/resume it."""
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
        self.status_title.setText(f"Connected ({d['mode']})")
        self.status_desc.setText(f"Serial: {d['serial']}  —  Status: {d['state'].upper()}")

        for b in self._reboot_btns:
            b.setEnabled(True)
        self.btn_flash.setEnabled(True)

        # Fetch device info in a background thread instead of the main thread
        # Avoid blocking the UI if a subprocess hangs
        self._info_worker = DeviceInfoWorker(self.manager, d)
        self._info_worker.info_signal.connect(self._on_device_info)
        self._info_worker.start()

    def _on_device_info(self, device, info):
        """Callback after device information is fetched (runs on the main thread)."""
        # Only update if the device is still the active device
        if self.active_device and self.active_device.get("serial") == device.get("serial"):
            self._info_vals[0].setText(info["serial"])
            self._info_vals[1].setText(info["name"])
            self._info_vals[2].setText(info["codename"])
            self._info_vals[3].setText(info["bootloader"])
            self._info_vals[4].setText(info["slot"])

            # Store codename for saving logs
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
            InfoBar.success("Command sent", f"Rebooting to {target}.",
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Failed", err or "Unknown error.",
                        position=InfoBarPosition.TOP, parent=self.window())

    # ══════════════════════════════════════════════════════════
    #  ROM SELECTION & SCANNING
    # ══════════════════════════════════════════════════════════
    def _pick_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Select ROM Folder")
        if p:
            self.selected_rom_dir = p
            self.btn_dir.setText(f"📁 {os.path.basename(p)}")
            self._scan(p)

    def _scan(self, d):
        self.detected_images = []
        try:
            files = [f for f in os.listdir(d) if f.lower().endswith(('.img', '.bin'))]
            # Also scan the images/ directory, matching the batch file behavior.
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
            # Automatically scroll the checklist to the current step.
            self.checklist_area.ensureWidgetVisible(lbl)

    # ══════════════════════════════════════════════════════════
    #  FLASH LOGIC (based on the batch file)
    # ══════════════════════════════════════════════════════════
    def _confirm(self):
        if not self.selected_rom_dir or not self.detected_images:
            InfoBar.error("Error", "No valid ROM folder selected.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return
        if not self.manager.is_available():
            InfoBar.error("Error", "ADB/Fastboot is not configured. Open Settings to set it up.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        if not self.connected_devices:
            InfoBar.error("Error", "No device is connected.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        d = self.connected_devices[0]

        # If the device is in ADB mode (system/recovery), automatically reboot to bootloader
        if d["mode"] == "ADB":
            msg = (f"Device is in {d['state'].upper()} (ADB).\n"
                   "It will automatically reboot to Bootloader for flashing.\n\n")
        else:
            msg = ""

        msg += "Flashing the ROM will overwrite the current operating system."
        if self.chk_wipe.isChecked():
            msg += "\n\n⚠ CLEAN FLASH: all data will be erased!"
        msg += f"\n\nWill flash {len(self.detected_images)} partitions.\nContinue?"

        box = MessageBox("Confirm ROM Flash", msg, self.window())
        box.yesButton.setText("⚡ Flash")
        box.cancelButton.setText("Cancel")
        if box.exec():
            if d["mode"] == "ADB":
                # Reboot into bootloader and wait
                self._set_busy(True)
                self.terminal.clear()
                self.terminal.append("🔄 Rebooting device into Bootloader...")
                ok, err = self.manager.reboot_to_bootloader(d["serial"], d["mode"])
                if not ok:
                    self.terminal.append(f"✖ Could not reboot: {err}")
                    self._set_busy(False)
                    InfoBar.error("Error", f"Could not reboot into bootloader: {err}",
                                position=InfoBarPosition.TOP, parent=self.window())
                    return
                # Wait for the device to switch to FASTBOOT
                self._reboot_waiter = RebootWaiter(self.manager, d["serial"])
                self._reboot_waiter.log_signal.connect(self.terminal.append)
                self._reboot_waiter.done_signal.connect(self._on_reboot_done)
                self._reboot_waiter.start()
            else:
                # Already in FASTBOOT, start flashing
                self._run(d["serial"])

    def _on_reboot_done(self, success, serial_or_error):
        """Callback after the device has rebooted into bootloader."""
        self._reboot_waiter = None
        if success:
            self.terminal.append("")
            self._run(serial_or_error)
        else:
            self._set_busy(False)
            self.terminal.append(f"✖ {serial_or_error}")
            InfoBar.error("Error", serial_or_error,
                        position=InfoBarPosition.TOP, parent=self.window())

    def _build_flash_args(self, partition, path):
        """Build arguments for the flash command. 
        - super: use 'flash super' + --skip-secondary to avoid hangs
        - cust: flash without the _a/_b suffix
        - Other partitions: flash both _a and _b
        """
        part_lower = partition.lower()
        steps = []

        if part_lower == "super":
            # Flash super without slot suffixes; add --skip-secondary to avoid hangs.
            steps.append({
                "name": f"Flash {partition}",
                "type": "FASTBOOT",
                "args": ["flash", partition, path]
            })
        elif part_lower in NO_SLOT_PARTITIONS:
            # cust, userdata, metadata, frp: do not add slot suffixes
            steps.append({
                "name": f"Flash {partition}",
                "type": "FASTBOOT",
                "args": ["flash", partition, path]
            })
        else:
            # Other partitions: flash both _a and _b slots
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

        # Fetch device information for display in the log
        info = self.manager.get_device_info(serial, "FASTBOOT")
        codename = info.get("codename", "N/A")
        if codename and codename != "N/A":
            self._device_product = codename
        self.terminal.append(f"📱 Device: {info.get('name', 'N/A')}")
        self.terminal.append(f"📦 Codename: {info.get('codename', 'N/A')}")
        self.terminal.append(f"🔐 Bootloader: {info.get('bootloader', 'N/A')}")
        self.terminal.append(f"💾 Slot: {info.get('slot', 'N/A')}")
        self.terminal.append("")

        # Flash each image with _a/_b suffixes except cust and super
        for img in self.detected_images:
            part = img["partition"]
            path = img["path"]
            flash_steps = self._build_flash_args(part, path)
            for fs in flash_steps:
                steps.append(fs)
                self._add_step(fs["name"])

        # Wipe when Clean Flash is selected (like the bat file: erase frp, userdata, metadata)
        if self.chk_wipe.isChecked():
            for part in ["frp", "userdata", "metadata"]:
                steps.append({"name": f"Erase {part}", "type": "FASTBOOT",
                              "args": ["erase", part]})
                self._add_step(f"Erase {part}")

        # Reboot
        steps.append({"name": "Reboot", "type": "FASTBOOT", "args": ["reboot"]})
        self._add_step("Reboot system")

        self._set_busy(True)
        self.progress.setValue(0)

        # Pause monitoring during flashing to avoid polling a busy device
        if self.monitor:
            self.monitor.pause()

        # Start the batch log timer
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
        """Show a dialog asking whether to continue flashing after an error."""
        box = MessageBox(
            "Partition Flash Failed",
            f"Step '{step_name}' failed.\n\nDo you want to skip it and continue flashing the remaining partitions?",
            self.window()
        )
        box.yesButton.setText("Continue")
        box.cancelButton.setText("Stop")
        result = box.exec()
        self.worker.reply_continue(result)

    def _on_log_line(self, text):
        """Buffer log lines; QTimer flushes them every 100ms."""
        self._log_buffer.append(text)

    def _flush_log_buffer(self):
        """Flush the full log buffer into the terminal in one pass to reduce re-rendering."""
        if not self._log_buffer:
            return
        batch = self._log_buffer.copy()
        self._log_buffer.clear()
        # Disable UI updates during batch insert
        self.terminal.setUpdatesEnabled(False)
        for line in batch:
            self.terminal.append(line)
        self.terminal.setUpdatesEnabled(True)

    def _done(self, ok, msg):
        # Stop the batch log timer and flush remaining logs
        self._log_timer.stop()
        self._flush_log_buffer()

        # Resume monitoring after flashing completes
        if self.monitor:
            self.monitor.resume()

        self._set_busy(False)
        if ok:
            for i in range(len(self.step_labels)):
                self._update_step(i, FlashWorker.SUCCESS)
            InfoBar.success("ROM Flash Successful! 🏆", msg,
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Error Flash", msg,
                        position=InfoBarPosition.TOP, parent=self.window())

    def _copy(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.terminal.toPlainText())
        InfoBar.success("Copied", "", duration=1500,
                      position=InfoBarPosition.TOP, parent=self.window())

    def _save_log(self):
        """Export the log to a .txt file named date_time_product.txt.
        Uses the stored product name from getvar/getprop instead of querying again."""
        text = self.terminal.toPlainText().strip()
        if not text:
            InfoBar.info("Empty", "No log content yet.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        # Create filename: date_time_product
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Use the stored codename (from getvar product / getprop ro.product.device)
        model = self._device_product if self._device_product else ""
        if not model:
            # Fallback: try reading from the current device
            if self.active_device:
                info = self.manager.get_device_info(
                    self.active_device["serial"], self.active_device["mode"])
                model = info.get("codename", "")
                if model and model != "N/A":
                    self._device_product = model
        if not model or model == "N/A":
            model = "unknown"
        # Remove invalid filename characters
        model = model.replace(" ", "_").replace("/", "-").replace("\\", "-")
        default_name = f"{now}_{model}.txt"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Log", default_name,
            "Text Files (*.txt);;All Files (*)")
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                InfoBar.success("Saved", os.path.basename(path),
                              position=InfoBarPosition.TOP, parent=self.window())
            except Exception as e:
                InfoBar.error("Error", str(e),
                            position=InfoBarPosition.TOP, parent=self.window())
