from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QFont, QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    MSFluentWindow,
    NavigationItemPosition,
    PasswordLineEdit,
    Pivot,
    PrimaryPushButton,
    PrimaryToolButton,
    PushButton,
    Theme,
    ToolButton,
    isDarkTheme,
    qconfig,
    setTheme,
)

from app import (
    APP_NAME,
    CONFIG_PATH,
    DEFAULT_HOTKEY,
    ERROR_ALREADY_EXISTS,
    HOTKEY_LABELS,
    ICON_PATH,
    INSTANCE_MUTEX_NAME,
    THEME_LABELS,
    TM_PATH,
    VERSION,
    Config,
    HotkeyDetector,
    hotkey_label,
    load_translation_memory,
    normalize_pdf_layout,
    normalize_translation_output,
    parse_glossary,
    relevant_memories,
    run_self_test,
    send_ctrl_c,
    translate_glm_stream,
    translate_qwen_stream,
)


def apply_qt_theme(value: str) -> None:
    theme = {
        "system": Theme.AUTO,
        "light": Theme.LIGHT,
        "dark": Theme.DARK,
    }.get(value, Theme.AUTO)
    setTheme(theme, save=False, lazy=False)


class TranslationInterface(QFrame):
    """ZenNotes-style writing surface repurposed as the translation output."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("translationInterface")
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(0)

        self.editor = QTextEdit(self)
        self.editor.setAcceptRichText(False)
        self.editor.setReadOnly(False)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setFont(QFont("Segoe UI Variable Text", 11))
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.document().setDocumentMargin(12)
        layout.addWidget(self.editor, 1)

        footer = QWidget(self)
        footer.setFixedHeight(46)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 6, 4, 0)
        footer_layout.setSpacing(8)
        self.status = CaptionLabel("", footer)
        footer_layout.addWidget(self.status, 1)

        self.confirm_button = PrimaryToolButton(FIF.ACCEPT, footer)
        self.confirm_button.setFixedSize(40, 36)
        self.confirm_button.setToolTip("确认译文")
        footer_layout.addWidget(self.confirm_button)

        self.copy_button = ToolButton(FIF.COPY, footer)
        self.copy_button.setFixedSize(40, 36)
        self.copy_button.setToolTip("复制译文")
        footer_layout.addWidget(self.copy_button)
        layout.addWidget(footer)

        qconfig.themeChanged.connect(self.update_theme)
        self.update_theme()

    def update_theme(self) -> None:
        if isDarkTheme():
            background, foreground = "#272727", "#F5F5F5"
        else:
            background, foreground = "#FAF9F8", "#111111"
        self.setStyleSheet(f"QFrame#translationInterface {{ background: {background}; border: 0; }}")
        self.editor.setStyleSheet(
            f"QTextEdit {{ background: {background}; color: {foreground}; border: 0; }}"
        )

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str) -> None:
        current_bar = self.editor.verticalScrollBar()
        at_bottom = current_bar.value() >= current_bar.maximum() - 2
        self.editor.setPlainText(text)
        if at_bottom:
            current_bar.setValue(current_bar.maximum())


class SettingsDialog(QDialog):
    """Fluent settings editor that keeps every existing translation option."""

    def __init__(self, cfg: Config, parent: QWidget | None = None, initial_page: str = "qwen") -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.entries: dict[str, LineEdit] = {}
        self.setWindowTitle("QuickTranslator 设置")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(700, 560)
        self.setMinimumSize(620, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        title = BodyLabel("设置", self)
        title.setFont(QFont("Segoe UI Variable Display", 18, QFont.Weight.DemiBold))
        root.addWidget(title)

        self.pivot = Pivot(self)
        root.addWidget(self.pivot)
        self.stack = QStackedWidget(self)
        root.addWidget(self.stack, 1)

        self.pages: dict[str, QWidget] = {}
        self._add_form_page("appearance", "外观", [
            ("主题", "theme", False),
        ])
        self._add_form_page("qwen", "百炼 Qwen-MT", [
            ("API 地址", "qwen_api_url", False),
            ("API Key", "qwen_api_key", True),
            ("精准模型", "accurate_model", False),
            ("极速模型", "fast_model", False),
        ])
        self._add_form_page("research", "科研翻译", [
            ("科研领域", "research_domain", False),
            ("英文领域提示", "domain_prompt", False),
            ("术语表", "glossary", False),
            ("GLM 目标语言", "target_language", False),
        ])
        self._add_form_page("glm", "备用 GLM", [
            ("API 地址", "api_url", False),
            ("API Key", "api_key", True),
            ("首选模型", "model", False),
            ("备用模型", "fallback_model", False),
        ])
        self._add_hotkey_page()

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = PushButton("取消", self)
        cancel.clicked.connect(self.reject)
        save = PrimaryPushButton("保存设置", self)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self.select_page(initial_page if initial_page in self.pages else "qwen")

    def _new_page(self, key: str, label: str) -> tuple[QWidget, QFormLayout]:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget(scroll)
        form = QFormLayout(page)
        form.setContentsMargins(10, 14, 10, 14)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(page)
        self.pages[key] = scroll
        self.stack.addWidget(scroll)
        self.pivot.addItem(key, label, lambda page_key=key: self.select_page(page_key))
        return page, form

    def _add_form_page(self, key: str, label: str, fields: list[tuple[str, str, bool]]) -> None:
        _, form = self._new_page(key, label)
        for field_label, attr, secret in fields:
            if attr == "theme":
                self.theme_box = ComboBox(self)
                self.theme_box.addItems(list(THEME_LABELS.values()))
                self.theme_box.setCurrentText(THEME_LABELS.get(self.cfg.theme, THEME_LABELS["system"]))
                form.addRow(field_label, self.theme_box)
                continue
            entry = PasswordLineEdit(self) if secret else LineEdit(self)
            entry.setText(str(getattr(self.cfg, attr)))
            entry.setClearButtonEnabled(True)
            self.entries[attr] = entry
            form.addRow(field_label, entry)

    def _add_hotkey_page(self) -> None:
        _, form = self._new_page("hotkey", "快捷键")
        self.hotkey_box = ComboBox(self)
        self.hotkey_box.addItems(list(HOTKEY_LABELS.values()))
        self.hotkey_box.setCurrentText(hotkey_label(self.cfg.hotkey))
        form.addRow("翻译触发方式", self.hotkey_box)
        note = CaptionLabel(
            "推荐“按住 Ctrl，双击 C”；Fn 通常由键盘硬件处理，无法被 Windows 程序稳定监听。",
            self,
        )
        note.setWordWrap(True)
        form.addRow("", note)

    def select_page(self, key: str) -> None:
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.pivot.setCurrentItem(key)

    def _save(self) -> None:
        for attr, entry in self.entries.items():
            setattr(self.cfg, attr, entry.text().strip())
        label_to_hotkey = {label: key for key, label in HOTKEY_LABELS.items()}
        label_to_theme = {label: key for key, label in THEME_LABELS.items()}
        self.cfg.hotkey = label_to_hotkey.get(self.hotkey_box.currentText(), DEFAULT_HOTKEY)
        self.cfg.theme = label_to_theme.get(self.theme_box.currentText(), "system")
        if not self.cfg.api_url or not self.cfg.model or not self.cfg.qwen_api_url:
            self.select_page("qwen")
            self.setWindowTitle("QuickTranslator 设置 · API 地址和模型不能为空")
            return
        self.cfg.save()
        self.accept()


class QuickTranslatorWindow(MSFluentWindow):
    """QuickTranslator UI built with the same window and navigation system as ZenNotes."""

    def __init__(self, cfg: Config) -> None:
        apply_qt_theme(cfg.theme)
        super().__init__()
        self.cfg = cfg
        self.controller: QuickTranslatorQt | None = None
        self._quitting = False
        self._auto_resizing = False
        self._settings_dialog: SettingsDialog | None = None

        self.translation_interface = TranslationInterface(self)
        self.stackedWidget.addWidget(self.translation_interface)
        self._init_navigation()
        self.navigationInterface.setCurrentItem("Translate")
        self.stackedWidget.setCurrentWidget(self.translation_interface)

        self.setWindowTitle("QuickTranslator")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(700, 360)
        self.setMinimumSize(500, 240)

        self.translation_interface.copy_button.clicked.connect(self._copy_clicked)
        self.translation_interface.confirm_button.clicked.connect(self._confirm_clicked)

    def _init_navigation(self) -> None:
        self.navigationInterface.addItem(
            routeKey="Translate",
            icon=FIF.EDIT,
            text="翻译",
            onClick=lambda: self.stackedWidget.setCurrentWidget(self.translation_interface),
            position=NavigationItemPosition.TOP,
        )
        self.mode_item = self.navigationInterface.addItem(
            routeKey="Mode",
            icon=FIF.SPEED_HIGH,
            text=self.mode_label(),
            onClick=self.toggle_mode,
            selectable=False,
            position=NavigationItemPosition.TOP,
        )
        self.mode_item.setToolTip(self.mode_label())
        self.navigationInterface.addItem(
            routeKey="Settings",
            icon=FIF.SETTING,
            text="设置",
            onClick=self.open_settings,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )

    @property
    def status(self) -> CaptionLabel:
        return self.translation_interface.status

    def mode_label(self) -> str:
        return "精准 Plus" if self.cfg.translation_mode == "accurate" else "极速 Turbo"

    def toggle_mode(self) -> None:
        self.cfg.translation_mode = "fast" if self.cfg.translation_mode == "accurate" else "accurate"
        self.cfg.save()
        self.mode_item.setText(self.mode_label())
        self.mode_item.setToolTip(self.mode_label())
        self.status.setText(f"已切换为 {self.mode_label()}")

    def _copy_clicked(self) -> None:
        if self.controller:
            self.controller.copy_result()

    def _confirm_clicked(self) -> None:
        if self.controller:
            self.controller.confirm_translation()

    def open_settings(self, initial_page: str = "qwen") -> None:
        if self._settings_dialog and self._settings_dialog.isVisible():
            self._settings_dialog.select_page(initial_page)
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(self.cfg, self, initial_page)
        self._settings_dialog = dialog
        if dialog.exec() == QDialog.DialogCode.Accepted:
            apply_qt_theme(self.cfg.theme)
            self.translation_interface.update_theme()
            self.status.setText(f"设置已保存 · {hotkey_label(self.cfg.hotkey)}")
        self._settings_dialog = None

    def set_output(self, text: str, auto_size: bool = True) -> None:
        self.translation_interface.set_text(text)
        if auto_size:
            QTimer.singleShot(0, self.auto_size_to_content)

    def output_text(self) -> str:
        return self.translation_interface.text()

    def auto_size_to_content(self) -> None:
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        document_height = self.translation_interface.editor.document().documentLayout().documentSize().height()
        chrome_height = self.titleBar.height() + 98
        target_height = max(self.minimumHeight(), min(int(available.height() * 0.72), int(document_height + chrome_height)))
        if abs(target_height - self.height()) <= 2:
            return
        self._auto_resizing = True
        self.resize(max(620, self.width()), target_height)
        self._auto_resizing = False

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting:
            event.accept()
        else:
            event.ignore()
            self.hide()


class QuickTranslatorQt:
    def __init__(self, application: QApplication) -> None:
        self.application = application
        self.cfg = Config.load()
        self.events: queue.Queue[tuple] = queue.Queue()
        self._clipboard_sequence = 0
        self._capture_deadline = 0.0
        self._key_release_deadline = 0.0
        self._capture_in_progress = False
        self._last_capture_trigger = 0.0
        self._request_id = 0
        self._stream_text = ""
        self._current_source = ""
        self._memories = load_translation_memory()
        self._quitting = False

        self.window = QuickTranslatorWindow(self.cfg)
        self.window.controller = self
        self._start_tray()

        self.poll_timer = QTimer(self.window)
        self.poll_timer.timeout.connect(self._poll_events)
        self.poll_timer.start(33)
        threading.Thread(target=self._translation_hotkey_loop, daemon=True).start()
        threading.Thread(target=self._double_shift_loop, daemon=True).start()

        executable_name = Path(sys.executable).stem.lower()
        preview = "preview" in executable_name or "--ui-preview" in sys.argv
        if preview:
            self.window.show()
            if "contentpreview" in executable_name:
                sample = (
                    "多焦视网膜电图（mfERG）是一种电生理检查方法，可同时评估视网膜多个独立区域的功能。"
                    "本文提供经更新与修订的临床标准，并界定记录与报告的最低规范。"
                )
                self.show_message(sample)
                self.window.status.setText("翻译完成")
        else:
            QTimer.singleShot(450, self.window.hide)

    def _start_tray(self) -> None:
        self.tray_icon = QSystemTrayIcon(QIcon(str(ICON_PATH)), self.application)
        self.tray_icon.setToolTip(APP_NAME)
        menu = QMenu()
        show_action = QAction("显示翻译窗口", menu)
        show_action.triggered.connect(self.show_window)
        settings_action = QAction("设置", menu)
        settings_action.triggered.connect(lambda: (self.show_window(), self.window.open_settings()))
        exit_action = QAction("退出", menu)
        exit_action.triggered.connect(self.exit_app)
        menu.addAction(show_action)
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_window()

    def _translation_hotkey_loop(self) -> None:
        user32 = ctypes.windll.user32
        detector = HotkeyDetector()
        while not self._quitting:
            if self._capture_in_progress:
                detector.reset()
                time.sleep(0.015)
                continue
            triggered = detector.update(
                self.cfg.hotkey,
                time.monotonic(),
                ctrl=bool(user32.GetAsyncKeyState(0x11) & 0x8000),
                alt=bool(user32.GetAsyncKeyState(0x12) & 0x8000),
                c=bool(user32.GetAsyncKeyState(0x43) & 0x8000),
                t=bool(user32.GetAsyncKeyState(0x54) & 0x8000),
            )
            if triggered:
                self.events.put(("translate",))
            time.sleep(0.015)

    def _double_shift_loop(self) -> None:
        user32 = ctypes.windll.user32
        was_down = False
        last_press = 0.0
        while not self._quitting:
            down = bool(user32.GetAsyncKeyState(0x10) & 0x8000)
            if down and not was_down:
                now = time.monotonic()
                if 0.08 < now - last_press < 0.42:
                    self.events.put(("show",))
                    last_press = 0.0
                else:
                    last_press = now
            was_down = down
            time.sleep(0.015)

    def _poll_events(self) -> None:
        render_text: str | None = None
        translation_done = False
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "translate":
                    self._start_safe_capture()
                elif kind == "show":
                    self.show_window()
                elif kind == "settings":
                    self.show_window()
                    self.window.open_settings()
                elif kind == "exit":
                    self.exit_app()
                    return
                elif kind == "chunk" and event[1] == self._request_id:
                    self._stream_text += event[2]
                    render_text = self._stream_text
                elif kind == "replace" and event[1] == self._request_id:
                    self._stream_text = event[2]
                    render_text = self._stream_text
                elif kind == "done" and event[1] == self._request_id:
                    translation_done = True
                    self.window.status.setText("翻译完成")
                elif kind == "status" and event[1] == self._request_id:
                    self.window.status.setText(event[2])
                elif kind == "error" and event[1] == self._request_id:
                    self._show_error(event[2])
        except queue.Empty:
            pass
        if render_text is not None:
            self.show_translation(render_text, final=translation_done)
        elif translation_done:
            self.show_translation(self._stream_text, final=True)

    def _start_safe_capture(self) -> None:
        now = time.monotonic()
        if self._capture_in_progress or now - self._last_capture_trigger < 0.9:
            return
        self._capture_in_progress = True
        self._last_capture_trigger = now
        self._key_release_deadline = now + 0.8
        self._capture_after_hotkey_release()

    def _capture_after_hotkey_release(self) -> None:
        ctrl_is_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
        alt_is_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)
        if (ctrl_is_down or alt_is_down) and time.monotonic() < self._key_release_deadline:
            QTimer.singleShot(20, self._capture_after_hotkey_release)
            return
        if ctrl_is_down or alt_is_down:
            self._capture_in_progress = False
            self._show_error("快捷键没有及时松开，已安全取消；未操作剪贴板。")
            return
        QTimer.singleShot(35, self.capture_and_translate)

    def capture_and_translate(self) -> None:
        self._clipboard_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if not send_ctrl_c():
            self._capture_in_progress = False
            self._show_error("Windows 未能发送复制指令；程序没有修改剪贴板。")
            return
        self._capture_deadline = time.monotonic() + 1.2
        QTimer.singleShot(60, self._read_selection)

    def _read_selection(self) -> None:
        sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if sequence == self._clipboard_sequence:
            if time.monotonic() < self._capture_deadline:
                QTimer.singleShot(50, self._read_selection)
                return
            self._capture_in_progress = False
            self._show_error("没有读取到选中文字；程序未改写你的剪贴板。")
            return
        text = QApplication.clipboard().text().strip()
        if not text and time.monotonic() < self._capture_deadline:
            QTimer.singleShot(50, self._read_selection)
            return
        self._capture_in_progress = False
        text = normalize_pdf_layout(text)
        if not text:
            self.show_message("选中的内容为空，请重新选择后再按快捷键。")
            return
        self._request_id += 1
        request_id = self._request_id
        self._current_source = text
        self._stream_text = ""
        self.show_message("正在连接…")
        self.window.status.setText(f"正在翻译…（{hotkey_label(self.cfg.hotkey)} 可开始新任务）")
        self._position_near_cursor()
        threading.Thread(target=self._translate_worker, args=(text, request_id), daemon=True).start()

    def _translate_worker(self, text: str, request_id: int) -> None:
        if self.cfg.qwen_api_key:
            qwen_model = (
                self.cfg.accurate_model
                if self.cfg.translation_mode == "accurate"
                else self.cfg.fast_model
            )
            try:
                self.events.put(("status", request_id, f"正在使用 {qwen_model} 翻译…"))
                result = translate_qwen_stream(
                    text,
                    self.cfg,
                    qwen_model,
                    self._memories,
                    lambda content: self.events.put(("replace", request_id, content)),
                )
                if not result:
                    raise RuntimeError("百炼接口没有返回译文。")
                self.events.put(("done", request_id))
                return
            except Exception as exc:
                self.events.put(("status", request_id, f"百炼暂不可用，正在切换 GLM：{exc}"))

        models = [self.cfg.model]
        if self.cfg.fallback_model and self.cfg.fallback_model not in models:
            models.append(self.cfg.fallback_model)
        for index, model in enumerate(models):
            try:
                self.events.put(("status", request_id, f"正在使用 {model} 翻译…"))
                result = translate_glm_stream(
                    text,
                    self.cfg,
                    lambda chunk: self.events.put(("chunk", request_id, chunk)),
                    model=model,
                )
                if not result:
                    raise RuntimeError("接口没有返回译文。")
                self.events.put(("done", request_id))
                return
            except Exception as exc:
                message = str(exc)
                if "429" in message and index + 1 < len(models) and request_id == self._request_id:
                    next_model = models[index + 1]
                    self.events.put((
                        "status",
                        request_id,
                        f"{model} 繁忙，正在切换备用模型 {next_model}…",
                    ))
                    continue
                if "429" in message:
                    message = "免费模型当前拥堵，请稍后再试；这不是电脑卡死。"
                self.events.put(("error", request_id, message))
                return

    def _position_near_cursor(self) -> None:
        self.window.auto_size_to_content()
        cursor = QCursor.pos()
        screen = QApplication.screenAt(cursor) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QApplication.primaryScreen().availableGeometry()
        x = min(cursor.x() + 18, available.right() - self.window.width() - 12)
        y = min(cursor.y() + 18, available.bottom() - self.window.height() - 12)
        self.window.move(max(available.left(), x), max(available.top(), y))
        self.show_window()

    def _show_error(self, message: str) -> None:
        self.show_message(f"翻译失败\n\n{message}")
        self.window.status.setText("翻译失败")
        self._position_near_cursor()

    def show_message(self, message: str, resize: bool = True) -> None:
        self.window.set_output(message, auto_size=resize)

    def show_translation(self, text: str, final: bool = False) -> None:
        rendered = normalize_translation_output(text, self._current_source)
        self.show_message(rendered, resize=True)

    def copy_result(self) -> None:
        QApplication.clipboard().setText(self.window.output_text())
        self.window.status.setText("已复制译文")

    def confirm_translation(self) -> None:
        source = self._current_source.strip()
        target = self.window.output_text().strip()
        if not source or not target or target.startswith(("正在", "翻译失败")):
            self.window.status.setText("当前没有可确认的译文")
            return
        self._memories = [item for item in self._memories if item.get("source") != source]
        self._memories.append({
            "source": source,
            "target": target,
            "domain": self.cfg.research_domain,
            "saved_at": str(int(time.time())),
        })
        self._memories = self._memories[-2000:]
        TM_PATH.parent.mkdir(parents=True, exist_ok=True)
        import json
        TM_PATH.write_text(json.dumps(self._memories, ensure_ascii=False, indent=2), encoding="utf-8")
        self.window.status.setText(f"已加入本地翻译记忆（{len(self._memories)} 条）")

    def show_window(self) -> None:
        if self.window.isMinimized():
            self.window.showNormal()
        else:
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def exit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.window._quitting = True
        self.poll_timer.stop()
        self.tray_icon.hide()
        self.window.close()
        self.application.quit()


def main() -> int:
    if "--self-test" in sys.argv:
        return 0 if run_self_test() else 1

    executable_stem = Path(sys.executable).stem.lower()
    mutex_name = (
        f"{INSTANCE_MUTEX_NAME}-{executable_stem}"
        if "preview" in executable_stem
        else INSTANCE_MUTEX_NAME
    )
    instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(None, "划词翻译已经在运行。", APP_NAME, 0x40)
        if instance_mutex:
            ctypes.windll.kernel32.CloseHandle(instance_mutex)
        return 0

    application = QApplication(sys.argv)
    application.setApplicationName("QuickTranslator")
    application.setApplicationVersion(VERSION)
    application.setQuitOnLastWindowClosed(False)
    controller = QuickTranslatorQt(application)
    exit_code = application.exec()
    if instance_mutex:
        ctypes.windll.kernel32.CloseHandle(instance_mutex)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
