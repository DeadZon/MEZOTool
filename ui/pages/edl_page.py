"""EDL Flash page - flash Qualcomm devices through 9008 mode (Emergency Download)."""
import os
import glob
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFileDialog,
                             QTextEdit, QFrame, QLabel, QGridLayout, QScrollArea,
                             QSizePolicy, QStackedWidget)
from qfluentwidgets import (TitleLabel, BodyLabel, SubtitleLabel, SimpleCardWidget,
                            PrimaryPushButton, PushButton, InfoBar, InfoBarPosition,
                            ComboBox, ProgressBar, MessageBox, LineEdit)
from core.edl_detect import EdlDeviceMonitor, detect_edl_tool
from core.edl_worker import EdlWorker, EdlResetWorker
from ui.styles import Styles


class PartitionEntry(QWidget):
    """Widget for one partition row: partition name + image file + remove button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(8)

        self.partition_input = LineEdit()
        self.partition_input.setPlaceholderText("Partition name (e.g. boot)")
        self.partition_input.setMinimumWidth(140)
        layout.addWidget(self.partition_input, 1)

        self.file_label = BodyLabel("No file selected")
        self.file_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.file_label, 2)

        self.btn_browse = PushButton("📁")
        self.btn_browse.setFixedWidth(40)
        self.btn_browse.clicked.connect(self._pick_file)
        layout.addWidget(self.btn_browse)

        self.btn_remove = PushButton("✖")
        self.btn_remove.setFixedWidth(40)
        self.btn_remove.setStyleSheet("color: #ef5350;")
        layout.addWidget(self.btn_remove)

        self._file_path = None

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select image file",
            "", "Image Files (*.img *.bin *.mbn *.elf);;All Files (*)")
        if path:
            self._file_path = path
            self.file_label.setText(os.path.basename(path))
            # Automatically fill the partition name from the filename if empty.
            if not self.partition_input.text():
                name = os.path.splitext(os.path.basename(path))[0]
                self.partition_input.setText(name)

    def get_data(self):
        """Return (partition_name, file_path) or None."""
        part = self.partition_input.text().strip()
        if part and self._file_path and os.path.isfile(self._file_path):
            return part, self._file_path
        return None


class EdlPage(QWidget):
    """EDL Flash page - flash devices through Qualcomm EDL mode (9008)."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.edl_monitor = None
        self.worker = None
        self._reset_worker = None
        self._edl_devices = []
        self._edl_path = None
        self._edl_source = None
        self._loader_path = None
        self._rom_dir = None
        self._xml_files = []  # [(rawprogram, patch), ...]
        self._partition_entries = []
        # Batch log
        self._log_buffer = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(100)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        # Checklist
        self.step_labels = []

        self.setObjectName("edl_page")
        self._detect_tool()
        self._build()
        self._start_monitor()

    def _detect_tool(self):
        self._edl_path, self._edl_source = detect_edl_tool()

    def _start_monitor(self):
        self.edl_monitor = EdlDeviceMonitor()
        self.edl_monitor.devices_signal.connect(self._on_devices)
        self.edl_monitor.start()

    # ══════════════════════════════════════════════════════════
    #  BUILD UI
    # ══════════════════════════════════════════════════════════
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left side: control panel (scroll) ──
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        left_widget = QWidget()
        left_widget.setObjectName("edlLeft")
        left_widget.setStyleSheet("#edlLeft { background: transparent; }")
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(24, 20, 12, 20)
        left_layout.setSpacing(14)

        left_layout.addWidget(TitleLabel("EDL Flash (9008)"))

        # ─── 1. EDL device status ───
        self.status_frame = QFrame()
        self.status_frame.setObjectName("edlStatusFrame")
        self.status_frame.setMinimumHeight(90)
        sl = QVBoxLayout(self.status_frame)
        sl.setContentsMargins(20, 16, 20, 16)

        self.status_icon = QLabel("📵")
        self.status_icon.setStyleSheet("font-size: 28px; background: transparent;")
        sl.addWidget(self.status_icon)

        self.status_title = QLabel("No device detected EDL")
        self.status_title.setStyleSheet("font-size: 15px; font-weight: bold; background: transparent;")
        sl.addWidget(self.status_title)

        self.status_desc = QLabel("Connect a Qualcomm device in EDL mode (9008).")
        self.status_desc.setWordWrap(True)
        self.status_desc.setStyleSheet("font-size: 12px; background: transparent;")
        sl.addWidget(self.status_desc)


        left_layout.addWidget(self.status_frame)

        # ─── 2. EDL Tool status ───
        tool_card = SimpleCardWidget()
        tl = QVBoxLayout(tool_card)
        tl.setContentsMargins(16, 14, 16, 14)
        tl.addWidget(SubtitleLabel("EDL Tool"))

        self.tool_label = BodyLabel("")
        self.tool_label.setWordWrap(True)
        self._update_tool_label()
        tl.addWidget(self.tool_label)

        tool_btn_row = QHBoxLayout()
        self.btn_refresh_tool = PushButton("🔄 Check Again")
        self.btn_refresh_tool.clicked.connect(self._refresh_tool)
        tool_btn_row.addWidget(self.btn_refresh_tool)
        tool_btn_row.addStretch()
        tl.addLayout(tool_btn_row)

        left_layout.addWidget(tool_card)

        # ─── 3. Select Firehose Loader ───
        loader_card = SimpleCardWidget()
        ll = QVBoxLayout(loader_card)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.addWidget(SubtitleLabel("1. Firehose Loader"))

        desc = BodyLabel("Programmer file (.mbn/.elf) specific to the device chipset.")
        desc.setWordWrap(True)
        desc.setStyleSheet("font-size: 12px;")
        ll.addWidget(desc)

        self.btn_loader = PushButton("📁 Select Loader File")
        self.btn_loader.clicked.connect(self._pick_loader)
        ll.addWidget(self.btn_loader)

        left_layout.addWidget(loader_card)

        # ─── 4. Flash Mode ───
        mode_card = SimpleCardWidget()
        ml = QVBoxLayout(mode_card)
        ml.setContentsMargins(16, 14, 16, 14)
        ml.addWidget(SubtitleLabel("2. Flash Mode"))

        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel("Mode:"))
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["Flash by XML", "Flash Individual Partitions"])
        self.mode_combo.setMinimumWidth(200)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_change)
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        ml.addLayout(mode_row)

        # Stacked widget for the 2 modes
        self.mode_stack = QStackedWidget()

        # ── Page 0: XML mode ──
        xml_page = QWidget()
        xml_layout = QVBoxLayout(xml_page)
        xml_layout.setContentsMargins(0, 8, 0, 0)
        xml_layout.setSpacing(8)

        xml_desc = BodyLabel("Select the ROM folder containing rawprogram*.xml, patch*.xml, and images.")
        xml_desc.setWordWrap(True)
        xml_desc.setStyleSheet("font-size: 12px;")
        xml_layout.addWidget(xml_desc)

        self.btn_rom_dir = PushButton("📁 Select ROM Folder")
        self.btn_rom_dir.clicked.connect(self._pick_rom_dir)
        xml_layout.addWidget(self.btn_rom_dir)

        self.xml_info_label = BodyLabel("")
        self.xml_info_label.setWordWrap(True)
        self.xml_info_label.setStyleSheet("font-size: 12px;")
        xml_layout.addWidget(self.xml_info_label)

        self.mode_stack.addWidget(xml_page)

        # ── Page 1: Partition mode ──
        part_page = QWidget()
        part_layout = QVBoxLayout(part_page)
        part_layout.setContentsMargins(0, 8, 0, 0)
        part_layout.setSpacing(8)

        part_desc = BodyLabel("Add a partition and select the matching image file.")
        part_desc.setWordWrap(True)
        part_desc.setStyleSheet("font-size: 12px;")
        part_layout.addWidget(part_desc)

        # Container for partition entries
        self.partition_container = QVBoxLayout()
        self.partition_container.setSpacing(4)
        part_layout.addLayout(self.partition_container)

        self.btn_add_part = PushButton("➕ Add Partition")
        self.btn_add_part.clicked.connect(self._add_partition_entry)
        part_layout.addWidget(self.btn_add_part)

        self.mode_stack.addWidget(part_page)

        ml.addWidget(self.mode_stack)
        left_layout.addWidget(mode_card)

        # ─── 5. Options ───
        opt_card = SimpleCardWidget()
        ol = QVBoxLayout(opt_card)
        ol.setContentsMargins(16, 14, 16, 14)
        ol.addWidget(SubtitleLabel("3. Options"))

        mem_row = QHBoxLayout()
        mem_row.addWidget(BodyLabel("Memory type:"))
        self.mem_combo = ComboBox()
        self.mem_combo.addItems(["Auto", "eMMC", "UFS"])
        self.mem_combo.setMinimumWidth(140)
        mem_row.addWidget(self.mem_combo)
        mem_row.addStretch()
        ol.addLayout(mem_row)

        left_layout.addWidget(opt_card)

        # ─── 6. Flash button + progress ───
        action_card = SimpleCardWidget()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(16, 12, 16, 12)

        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.progress.setVisible(False)
        al.addWidget(self.progress)

        br = QHBoxLayout()
        self.btn_flash = PrimaryPushButton("⚡ Start EDL Flash")
        self.btn_flash.setMinimumHeight(44)
        self.btn_flash.setEnabled(False)
        self.btn_flash.clicked.connect(self._confirm_flash)
        self.btn_cancel = PushButton("Cancel")
        self.btn_cancel.setMinimumHeight(44)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel)
        br.addWidget(self.btn_flash, 2)
        br.addWidget(self.btn_cancel, 1)
        al.addLayout(br)

        # Separate reset button
        self.btn_reset = PushButton("🔄 Reset Device")
        self.btn_reset.setMinimumHeight(36)
        self.btn_reset.setEnabled(False)
        self.btn_reset.clicked.connect(self._reset_device)
        al.addWidget(self.btn_reset)

        left_layout.addWidget(action_card)

        left_layout.addStretch()
        left_scroll.setWidget(left_widget)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        root.addWidget(left_scroll, 2)

        # ── Right side: log terminal ──
        right_widget = QWidget()
        right_widget.setObjectName("edlRight")
        right_widget.setStyleSheet("#edlRight { background: transparent; }")
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(12, 20, 24, 20)
        right_layout.setSpacing(10)

        right_layout.addWidget(SubtitleLabel("📋 EDL Log"))

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
        b1.clicked.connect(self._copy_log)
        b2 = PushButton("🗑 Clear Log")
        b2.clicked.connect(self.terminal.clear)
        tr.addWidget(b1)
        tr.addWidget(b2)
        tr.addStretch()
        right_layout.addLayout(tr)

        right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(right_widget, 3)

        # Add one default partition entry
        self._add_partition_entry()

        # Set the initial state after all widgets are created
        self._set_disconnected()

    # ══════════════════════════════════════════════════════════
    #  DEVICE STATUS
    # ══════════════════════════════════════════════════════════
    def _set_disconnected(self):
        self.status_frame.setStyleSheet(Styles.status_edl_disconnected())
        self.status_icon.setText("📵")
        self.status_title.setText("No device detected EDL")
        self.status_desc.setText("Connect a Qualcomm device in EDL mode (9008).")
        self.btn_flash.setEnabled(False)
        self.btn_reset.setEnabled(False)

    def _set_connected(self, device):
        self.status_frame.setStyleSheet(Styles.status_edl_connected())
        self.status_icon.setText("🔥")
        name = device.get("name", "Qualcomm 9008")
        port = device.get("port", "")
        self.status_title.setText("EDL device connected")
        desc = f"📱 {name}"
        if port:
            desc += f"  —  Port: {port}"
        self.status_desc.setText(desc)
        self._update_flash_button()
        self.btn_reset.setEnabled(self._edl_path is not None)

    def _on_devices(self, devices):
        self._edl_devices = devices
        if devices:
            self._set_connected(devices[0])
        else:
            self._set_disconnected()

    # ══════════════════════════════════════════════════════════
    #  TOOL DETECTION
    # ══════════════════════════════════════════════════════════
    def _update_tool_label(self):
        if self._edl_path:
            source_map = {
                "integrated": "integrated",
                "local": "local directory",
                "path": "system PATH",
            }
            src = source_map.get(self._edl_source, self._edl_source)
            self.tool_label.setText(f"✔ EDL tool is ready ({src})")
        else:
            self.tool_label.setText(
                "⚠ EDL tool was not found.\n\n"
                "Run: pip install edl\n"
                "Or download from: github.com/bkerler/edl"
            )

    def _refresh_tool(self):
        self._detect_tool()
        self._update_tool_label()
        if self._edl_path:
            InfoBar.success("Found!", f"EDL tool: {self._edl_path}",
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.warning("Not found", "Please install the edl tool.",
                          position=InfoBarPosition.TOP, parent=self.window())

    # ══════════════════════════════════════════════════════════
    #  LOADER SELECTION
    # ══════════════════════════════════════════════════════════
    def _pick_loader(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Firehose Loader",
            "", "Programmer Files (*.mbn *.elf);;All Files (*)")
        if path:
            self._loader_path = path
            self.btn_loader.setText(f"📁 {os.path.basename(path)}")
            self._update_flash_button()

    # ══════════════════════════════════════════════════════════
    #  MODE SWITCHING
    # ══════════════════════════════════════════════════════════
    def _on_mode_change(self, index):
        self.mode_stack.setCurrentIndex(index)
        self._update_flash_button()

    # ══════════════════════════════════════════════════════════
    #  XML MODE
    # ══════════════════════════════════════════════════════════
    def _pick_rom_dir(self):
        p = QFileDialog.getExistingDirectory(self, "Select ROM Folder (EDL)")
        if p:
            self._rom_dir = p
            self.btn_rom_dir.setText(f"📁 {os.path.basename(p)}")
            self._scan_xml(p)

    def _scan_xml(self, directory):
        """Scan the folder for rawprogram*.xml and patch*.xml."""
        self._xml_files = []

        rawprograms = sorted(glob.glob(os.path.join(directory, "rawprogram*.xml")))
        patches = sorted(glob.glob(os.path.join(directory, "patch*.xml")))

        if not rawprograms:
            self.xml_info_label.setText("⚠ No rawprogram*.xml found in this folder.")
            self._update_flash_button()
            return

        # Pair rawprogram + patch by index number
        for rp in rawprograms:
            rp_name = os.path.basename(rp)
            # Find the matching patch (rawprogram0.xml -> patch0.xml)
            idx = rp_name.replace("rawprogram", "").replace(".xml", "")
            matching_patch = None
            for pt in patches:
                pt_name = os.path.basename(pt)
                pt_idx = pt_name.replace("patch", "").replace(".xml", "")
                if pt_idx == idx:
                    matching_patch = pt
                    break
            self._xml_files.append((rp, matching_patch))

        # Display results
        info_lines = [f"✔ Found {len(rawprograms)} rawprogram XML:"]
        for rp, pt in self._xml_files:
            rp_name = os.path.basename(rp)
            pt_name = os.path.basename(pt) if pt else "(no patch)"
            info_lines.append(f"  • {rp_name} + {pt_name}")

        # Count images
        images = glob.glob(os.path.join(directory, "*.img")) + \
                 glob.glob(os.path.join(directory, "*.bin"))
        if images:
            info_lines.append(f"\n📦 {len(images)} image files in the folder.")

        self.xml_info_label.setText("\n".join(info_lines))
        self._update_flash_button()

    # ══════════════════════════════════════════════════════════
    #  PARTITION MODE
    # ══════════════════════════════════════════════════════════
    def _add_partition_entry(self):
        entry = PartitionEntry()
        entry.btn_remove.clicked.connect(lambda: self._remove_partition_entry(entry))
        self.partition_container.addWidget(entry)
        self._partition_entries.append(entry)

    def _remove_partition_entry(self, entry):
        if len(self._partition_entries) <= 1:
            return  # Keep at least one entry
        self.partition_container.removeWidget(entry)
        self._partition_entries.remove(entry)
        entry.deleteLater()

    # ══════════════════════════════════════════════════════════
    #  FLASH LOGIC
    # ══════════════════════════════════════════════════════════
    def _update_flash_button(self):
        """Update the Flash button state based on requirements."""
        can_flash = (
            self._edl_devices and
            self._edl_path is not None and
            self._loader_path is not None
        )
        if can_flash and self.mode_combo.currentIndex() == 0:
            # XML mode: XML files are required
            can_flash = len(self._xml_files) > 0
        self.btn_flash.setEnabled(can_flash)

    def _get_memory_type(self):
        idx = self.mem_combo.currentIndex()
        return ["auto", "emmc", "ufs"][idx]

    def _confirm_flash(self):
        if not self._edl_devices:
            InfoBar.error("Error", "No EDL device is connected.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return
        if not self._edl_path:
            InfoBar.error("Error", "EDL tool is not installed.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return
        if not self._loader_path:
            InfoBar.error("Error", "No Firehose Loader selected.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        # Build steps
        steps = self._build_steps()
        if not steps:
            InfoBar.error("Error", "There are no flash steps. Check the configuration.",
                        position=InfoBarPosition.TOP, parent=self.window())
            return

        msg = (f"⚠ EDL Flash is a low-level and very risky operation!\n\n"
               f"Loader: {os.path.basename(self._loader_path)}\n"
               f"Steps: {len(steps)}\n\n"
               f"Continue?")

        box = MessageBox("Confirm EDL Flash", msg, self.window())
        box.yesButton.setText("⚡ Flash")
        box.cancelButton.setText("Cancel")
        if box.exec():
            self._run_flash(steps)

    def _build_steps(self):
        steps = []
        if self.mode_combo.currentIndex() == 0:
            # XML mode
            for rp, pt in self._xml_files:
                name = f"Flash {os.path.basename(rp)}"
                step = {
                    "mode": "xml",
                    "name": name,
                    "rawprogram": rp,
                    "patch": pt or "",
                    "directory": self._rom_dir
                }
                steps.append(step)
        else:
            # Partition mode
            for entry in self._partition_entries:
                data = entry.get_data()
                if data:
                    part, path = data
                    steps.append({
                        "mode": "partition",
                        "name": f"Flash {part}",
                        "partition": part,
                        "image": path
                    })
        return steps

    def _run_flash(self, steps):
        self._clear_checklist()
        self.terminal.clear()

        # Add checklist entries
        for step in steps:
            self._add_step(step["name"])

        self._set_busy(True)
        self.progress.setValue(0)

        # Start the batch log timer
        self._log_buffer.clear()
        self._log_timer.start()

        memory = self._get_memory_type()
        self.worker = EdlWorker(self._edl_path, self._loader_path, steps, memory)
        self.worker.log_signal.connect(self._on_log_line)
        self.worker.step_signal.connect(self._update_step)
        self.worker.progress_signal.connect(self.progress.setValue)
        self.worker.finished_signal.connect(self._on_flash_done)
        self.worker.start()

    def _set_busy(self, busy):
        for w in (self.btn_flash, self.btn_loader, self.btn_rom_dir,
                  self.mode_combo, self.btn_add_part, self.btn_reset,
                  self.btn_refresh_tool):
            w.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.progress.setVisible(busy)

    def _cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()

    # ══════════════════════════════════════════════════════════
    #  LOG BATCH
    # ══════════════════════════════════════════════════════════
    def _on_log_line(self, text):
        self._log_buffer.append(text)

    def _flush_log_buffer(self):
        if not self._log_buffer:
            return
        batch = self._log_buffer.copy()
        self._log_buffer.clear()
        self.terminal.setUpdatesEnabled(False)
        for line in batch:
            self.terminal.append(line)
        self.terminal.setUpdatesEnabled(True)

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
            if status == EdlWorker.RUNNING:
                lbl.setText(f"🔄 {txt}")
                lbl.setStyleSheet(Styles.checklist_running())
            elif status == EdlWorker.SUCCESS:
                lbl.setText(f"✔ {txt}")
                lbl.setStyleSheet(Styles.checklist_success())
            elif status == EdlWorker.FAILED:
                lbl.setText(f"✖ {txt}")
                lbl.setStyleSheet(Styles.checklist_failed())
            self.checklist_area.ensureWidgetVisible(lbl)

    # ══════════════════════════════════════════════════════════
    #  FLASH DONE
    # ══════════════════════════════════════════════════════════
    def _on_flash_done(self, ok, msg):
        self._log_timer.stop()
        self._flush_log_buffer()
        self._set_busy(False)
        # Re-enable the flash button if a device is still present
        self._update_flash_button()

        if ok:
            for i in range(len(self.step_labels)):
                self._update_step(i, EdlWorker.SUCCESS)
            InfoBar.success("EDL Flash Successful! 🏆", msg,
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Error EDL Flash", msg,
                        position=InfoBarPosition.TOP, parent=self.window())

    # ══════════════════════════════════════════════════════════
    #  RESET
    # ══════════════════════════════════════════════════════════
    def _reset_device(self):
        if not self._edl_path:
            return
        self._reset_worker = EdlResetWorker(self._edl_path, self._loader_path)
        self._reset_worker.log_signal.connect(self.terminal.append)
        self._reset_worker.finished_signal.connect(self._on_reset_done)
        self.btn_reset.setEnabled(False)
        self._reset_worker.start()

    def _on_reset_done(self, ok, msg):
        self.btn_reset.setEnabled(True)
        if ok:
            InfoBar.success("Reset Complete", msg,
                          position=InfoBarPosition.TOP, parent=self.window())
        else:
            InfoBar.error("Error reset", msg,
                        position=InfoBarPosition.TOP, parent=self.window())

    # ══════════════════════════════════════════════════════════
    #  UTILITIES
    # ══════════════════════════════════════════════════════════
    def _copy_log(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.terminal.toPlainText())
        InfoBar.success("Copied", "", duration=1500,
                      position=InfoBarPosition.TOP, parent=self.window())

    def cleanup(self):
        """Called when the app closes."""
        if self.edl_monitor:
            self.edl_monitor.stop()
            self.edl_monitor.wait(3000)
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        if self._reset_worker and self._reset_worker.isRunning():
            self._reset_worker.wait(2000)
