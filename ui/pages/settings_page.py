import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QFileDialog
from qfluentwidgets import (ScrollArea, SettingCardGroup, PushSettingCard,
                            SettingCard, ComboBox, InfoBar, InfoBarPosition,
                            ProgressBar, TitleLabel, BodyLabel, Theme, setTheme,
                            FluentIcon as FIF)
from core.adb_fastboot import ADBFastbootManager, load_config, save_config
from core.downloader import PlatformToolsDownloader


class SettingsPage(ScrollArea):
    settings_changed = pyqtSignal()

    def __init__(self, manager: ADBFastbootManager, parent=None):
        super().__init__(parent=parent)
        self.manager = manager
        self.downloader = None
        self.setObjectName("settings_page")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── Important: transparent background for dark mode ──
        self.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self._build()

    def _build(self):
        # The content widget must also be transparent
        w = QWidget()
        w.setObjectName("settingsContent")
        w.setStyleSheet("#settingsContent { background: transparent; }")

        layout = QVBoxLayout(w)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(24)

        layout.addWidget(TitleLabel("Settings"))

        config = load_config()

        # ── ADB/Fastboot ──
        tools = SettingCardGroup("ADB & Fastboot Tools", w)

        self.status_card = PushSettingCard(
            "Check", FIF.FINGERPRINT,
            "ADB & Fastboot Version",
            "Checking...", tools)
        self.status_card.button.clicked.connect(self._check_version)
        tools.addSettingCard(self.status_card)

        path_text = config.get("platform_tools_path", "") or "Not configured..."
        self.path_card = PushSettingCard(
            "Select Folder", FIF.FOLDER,
            "Platform Tools Path",
            path_text, tools)
        self.path_card.button.clicked.connect(self._pick_folder)
        tools.addSettingCard(self.path_card)

        self.dl_card = PushSettingCard(
            "Download", FIF.DOWNLOAD,
            "Download Platform Tools Automatically",
            "Download the latest version from Google.", tools)
        self.dl_card.button.clicked.connect(self._download)
        tools.addSettingCard(self.dl_card)

        # Progress (hidden)
        self.dl_container = QWidget(tools)
        self.dl_container.setStyleSheet("background: transparent;")
        dl_lay = QVBoxLayout(self.dl_container)
        dl_lay.setContentsMargins(16, 8, 16, 8)
        self.dl_label = BodyLabel("Ready.")
        self.dl_progress = ProgressBar()
        self.dl_progress.setValue(0)
        dl_lay.addWidget(self.dl_label)
        dl_lay.addWidget(self.dl_progress)
        self.dl_container.setVisible(False)
        tools.layout().addWidget(self.dl_container)

        layout.addWidget(tools)

        # ── Theme ──
        theme_group = SettingCardGroup("Application Appearance", w)

        self.theme_card = SettingCard(
            FIF.BRUSH, "Display Theme",
            "Choose light or dark appearance.", theme_group)
        self.theme_combo = ComboBox()
        self.theme_combo.addItems(["Light", "Dark", "System"])
        self.theme_combo.setMinimumWidth(150)
        idx = {"Light": 0, "Dark": 1, "System": 2}
        self.theme_combo.setCurrentIndex(idx.get(config.get("theme", "System"), 2))
        self.theme_combo.currentIndexChanged.connect(self._change_theme)
        self.theme_card.hBoxLayout.addWidget(self.theme_combo, 0, Qt.AlignmentFlag.AlignRight)
        self.theme_card.hBoxLayout.addSpacing(16)
        theme_group.addSettingCard(self.theme_card)

        layout.addWidget(theme_group)
        layout.addStretch()
        self.setWidget(w)
        self._check_version()

    def _check_version(self):
        self.manager.detect_paths()
        if self.manager.is_available():
            a, f = self.manager.get_version()
            self.status_card.setContent(f"ADB: {a}  |  Fastboot: {f}")
        else:
            self.status_card.setContent("Not found. Select a path or download it.")

    def _pick_folder(self):
        p = QFileDialog.getExistingDirectory(self, "Select Folder Platform Tools")
        if p:
            config = load_config()
            config["platform_tools_path"] = p
            save_config(config)
            self.path_card.setContent(p)
            self.manager.detect_paths()
            self._check_version()
            self.settings_changed.emit()
            InfoBar.success("Saved", f"Path: {p}",
                          position=InfoBarPosition.TOP, parent=self.window())

    def _change_theme(self, index):
        mapping = [("Light", Theme.LIGHT), ("Dark", Theme.DARK), ("System", Theme.AUTO)]
        name, theme = mapping[index]
        config = load_config()
        config["theme"] = name
        save_config(config)
        setTheme(theme)

    def _download(self):
        if self.downloader and self.downloader.isRunning():
            InfoBar.info("Downloading", "Please wait.", position=InfoBarPosition.TOP,
                       parent=self.window())
            return
        self.dl_container.setVisible(True)
        self.dl_card.setEnabled(False)
        self.dl_progress.setValue(0)
        self.dl_label.setText("Connecting...")
        self.downloader = PlatformToolsDownloader(dest_dir=self.manager._app_dir)
        self.downloader.progress_signal.connect(self.dl_progress.setValue)
        self.downloader.status_signal.connect(self.dl_label.setText)
        self.downloader.finished_signal.connect(self._dl_done)
        self.downloader.start()

    def _dl_done(self, ok, msg):
        self.dl_card.setEnabled(True)
        if ok:
            config = load_config()
            config["platform_tools_path"] = msg
            save_config(config)
            self.path_card.setContent(msg)
            self.manager.detect_paths()
            self._check_version()
            self.settings_changed.emit()
            InfoBar.success("Complete!", "Platform Tools has been installed.",
                          position=InfoBarPosition.TOP, parent=self.window())
            self.dl_container.setVisible(False)
        else:
            InfoBar.error("Error", msg, position=InfoBarPosition.TOP,
                        parent=self.window())
