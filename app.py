from __future__ import annotations

import ctypes
import ctypes.wintypes
import difflib
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import winreg
from dataclasses import asdict, dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
import pystray


APP_NAME = "划词翻译"
VERSION = "0.6.3"
FONT_TEXT = "Segoe UI Variable Text"
FONT_DISPLAY = "Segoe UI Variable Text"
FONT_ICON = "Segoe Fluent Icons"
CONFIG_PATH = Path(os.getenv("APPDATA", Path.home())) / "QuickTranslator" / "config.json"
TM_PATH = CONFIG_PATH.with_name("translation_memory.json")
ICON_PATH = Path(__file__).resolve().parent / "assets" / "app_icon.png"
ERROR_ALREADY_EXISTS = 183
INSTANCE_MUTEX_NAME = "Local\\QuickTranslatorSingleInstance"
DEFAULT_HOTKEY = "ctrl_double_c"
TITLEBAR_HEIGHT = 32
TOOLBAR_HEIGHT = 42
MIN_WINDOW_HEIGHT = 190
HOTKEY_LABELS = {
    "ctrl_double_c": "按住 Ctrl，双击 C",
    "ctrl_alt_t": "Ctrl + Alt + T",
    "double_alt": "双击 Alt",
    "double_ctrl": "双击 Ctrl",
}
THEME_LABELS = {
    "system": "跟随 Windows",
    "light": "浅色",
    "dark": "深色",
}
LIGHT_PALETTE = {
    "ACCENT": "#008C99", "ACCENT_HOVER": "#179DA8", "ACCENT_PRESSED": "#00737E",
    "ACCENT_LIGHT": "#DDF4F6", "ACCENT_TEXT": "#FFFFFF", "WINDOW_BG": "#EFF5FA",
    "SURFACE": "#FFFFFF", "SURFACE_ALT": "#F8F8F8", "CONTROL_FILL": "#FBFBFB",
    "CONTROL_HOVER": "#F0F0F0", "CONTROL_PRESSED": "#E7E7E7", "BORDER": "#E1E1E1",
    "BORDER_STRONG": "#C8C8C8", "TEXT": "#1A1A1A", "MUTED": "#616161",
    "SUBTLE": "#7A7A7A", "SELECTION": "#BFE8EC", "SCROLL_THUMB": "#A8A8A8",
}
DARK_PALETTE = {
    "ACCENT": "#21D7E5", "ACCENT_HOVER": "#43E0EB", "ACCENT_PRESSED": "#00AFBC",
    "ACCENT_LIGHT": "#243E41", "ACCENT_TEXT": "#061719", "WINDOW_BG": "#202020",
    "SURFACE": "#232323", "SURFACE_ALT": "#292929", "CONTROL_FILL": "#303030",
    "CONTROL_HOVER": "#383838", "CONTROL_PRESSED": "#414141", "BORDER": "#303030",
    "BORDER_STRONG": "#4A4A4A", "TEXT": "#F5F5F5", "MUTED": "#B0B0B0",
    "SUBTLE": "#8D8D8D", "SELECTION": "#17535A", "SCROLL_THUMB": "#676767",
}
IS_DARK = False


def apply_palette(theme: str) -> None:
    global IS_DARK
    IS_DARK = theme == "dark"
    globals().update(DARK_PALETTE if IS_DARK else LIGHT_PALETTE)


apply_palette("light")


def normalize_hotkey(value: object) -> str:
    legacy_values = {
        "双击 Ctrl": DEFAULT_HOTKEY,
        "Ctrl+双击 C": DEFAULT_HOTKEY,
        "Ctrl + 双击 C": DEFAULT_HOTKEY,
        "Ctrl+Alt+T": "ctrl_alt_t",
    }
    normalized = legacy_values.get(str(value), str(value))
    return normalized if normalized in HOTKEY_LABELS else DEFAULT_HOTKEY


def hotkey_label(value: object) -> str:
    return HOTKEY_LABELS[normalize_hotkey(value)]


def normalize_theme(value: object) -> str:
    normalized = str(value).lower()
    return normalized if normalized in THEME_LABELS else "system"


def theme_label(value: object) -> str:
    return THEME_LABELS[normalize_theme(value)]


def system_theme() -> str:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            uses_light = int(winreg.QueryValueEx(key, "AppsUseLightTheme")[0])
            return "light" if uses_light else "dark"
    except (FileNotFoundError, OSError, ValueError):
        return "light"


def resolve_theme(value: object) -> str:
    normalized = normalize_theme(value)
    return system_theme() if normalized == "system" else normalized


def calculate_panel_height(display_lines: int, line_height: int) -> int:
    """Fit text, action row and the rounded panel's inner padding."""
    return 84 + max(1, display_lines) * max(16, line_height)


def calculate_window_height(display_lines: int, line_height: int, screen_height: int) -> int:
    """Fit the compact toolbar and translation area without leaving blank space."""
    content_height = calculate_panel_height(display_lines, line_height) + TOOLBAR_HEIGHT
    return min(int(screen_height * 0.72), max(MIN_WINDOW_HEIGHT, content_height))


def display_line_count(raw_count: object) -> int:
    """Tk counts display-line transitions; the visible line count is one greater."""
    try:
        return max(1, int(raw_count) + 1)
    except (TypeError, ValueError):
        return 1


def _colorref(hex_color: str) -> int:
    red, green, blue = (
        int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16),
    )
    return red | (green << 8) | (blue << 16)


def apply_fluent_window(window: tk.Misc, backdrop_type: int = 0) -> None:
    """Keep the normal Windows frame while matching its light or dark caption."""
    try:
        window.update_idletasks()
        client_hwnd = window.winfo_id()
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.wintypes.HWND]
        user32.GetParent.restype = ctypes.wintypes.HWND
        hwnd = user32.GetParent(client_hwnd) or client_hwnd
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.DWORD,
            ctypes.c_void_p, ctypes.wintypes.DWORD,
        ]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long

        def set_int(attribute: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

        set_int(20, 1 if IS_DARK else 0)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_int(33, 2)  # DWMWA_WINDOW_CORNER_PREFERENCE: round
        set_int(34, -1)  # Use the normal Windows frame and separator.
        set_int(35, -1)
        set_int(36, -1)
        set_int(38, backdrop_type)  # Use the normal system backdrop.
    except (AttributeError, OSError, tk.TclError):
        # Older Windows versions keep the same Fluent-inspired fallback palette.
        pass


def apply_borderless_window(window: tk.Misc) -> None:
    """Keep a normal taskbar window while replacing only its native caption."""
    try:
        window.update_idletasks()
        client_hwnd = window.winfo_id()
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.wintypes.HWND]
        user32.GetParent.restype = ctypes.wintypes.HWND
        user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.SetWindowPos.argtypes = [
            ctypes.wintypes.HWND, ctypes.wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.wintypes.UINT,
        ]
        user32.SetWindowPos.restype = ctypes.wintypes.BOOL
        hwnd = user32.GetParent(client_hwnd) or client_hwnd
        gwl_style = -16
        ws_caption = 0x00C00000
        ws_maximizebox = 0x00010000
        ws_thickframe = 0x00040000
        ws_minimizebox = 0x00020000
        ws_sysmenu = 0x00080000
        style = user32.GetWindowLongW(hwnd, gwl_style)
        style = (style & ~ws_caption & ~ws_maximizebox) | ws_thickframe | ws_minimizebox | ws_sysmenu
        user32.SetWindowLongW(hwnd, gwl_style, style)
        swp_flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, swp_flags)
    except (AttributeError, OSError, tk.TclError):
        pass


def _draw_rounded_rectangle(
    canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float,
    *, fill: str, outline: str = "", width: int = 1, tags: str = "",
) -> int:
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(
        points, smooth=True, splinesteps=24, fill=fill, outline=outline,
        width=width, tags=tags,
    )


class FluentToolTip:
    def __init__(self, widget: tk.Widget, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self._job = None
        self._window = None

    def schedule(self) -> None:
        self.cancel()
        if self.text:
            self._job = self.widget.after(550, self.show)

    def cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None
        self.hide()

    def show(self) -> None:
        self._job = None
        if not self.text or self._window is not None:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + max(0, (self.widget.winfo_height() - 26) // 2)
        tip = tk.Toplevel(self.widget)
        self._window = tip
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(background=BORDER_STRONG)
        tk.Label(
            tip, text=self.text, foreground=TEXT, background=SURFACE,
            font=(FONT_TEXT, 9), padx=9, pady=5,
        ).pack(padx=1, pady=1)
        tip.geometry(f"+{x}+{y}")

    def hide(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


class FluentButton(tk.Canvas):
    def __init__(
        self, parent: tk.Misc, text: str, command, *, kind: str = "secondary",
        width: int | None = None, height: int = 34, icon: bool = False,
        tooltip: str = "",
    ) -> None:
        units = sum(2 if ord(char) > 127 else 1 for char in text)
        default_width = 36 if icon else max(72, 28 + units * 7)
        self._surface = parent.cget("background") if "background" in parent.keys() else WINDOW_BG
        super().__init__(
            parent, width=width or default_width, height=height, background=self._surface,
            borderwidth=0, highlightthickness=0, takefocus=1, cursor="hand2",
        )
        self._text = text
        self._command = command
        self._kind = kind
        self._icon = icon
        self._hovered = False
        self._pressed = False
        self._tooltip = FluentToolTip(self, tooltip)
        self.bind("<Configure>", lambda event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda event: self._invoke())
        self.bind("<space>", lambda event: self._invoke())
        self._draw()

    def _palette(self) -> tuple[str, str, str, str, str]:
        return {
            "primary": (ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT_TEXT, ACCENT),
            "secondary": (CONTROL_FILL, CONTROL_HOVER, CONTROL_PRESSED, TEXT, BORDER_STRONG),
            "subtle": (self._surface, CONTROL_HOVER, CONTROL_PRESSED, TEXT, self._surface),
            "selected": (ACCENT_LIGHT, CONTROL_HOVER, CONTROL_PRESSED, ACCENT, ACCENT_LIGHT),
        }[self._kind]

    def _draw(self) -> None:
        self.delete("all")
        normal, hover, pressed, foreground, outline = self._palette()
        fill = pressed if self._pressed else hover if self._hovered else normal
        current_outline = outline if self._kind in {"primary", "secondary"} else fill
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        _draw_rounded_rectangle(
            self, 1, 1, width - 1, height - 1, 7 if self._icon else 5,
            fill=fill, outline=current_outline, width=1,
        )
        self.create_text(
            width / 2, height / 2, text=self._text, fill=foreground,
            font=(FONT_ICON, 13) if self._icon else (FONT_TEXT, 9),
        )

    def _on_enter(self, event) -> None:
        self._hovered = True
        self._draw()
        self._tooltip.schedule()

    def _on_leave(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self._draw()
        self._tooltip.cancel()

    def _on_press(self, event) -> None:
        self.focus_set()
        self._pressed = True
        self._draw()
        self._tooltip.cancel()

    def _on_release(self, event) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._pressed = False
        self._draw()
        if inside:
            self._invoke()

    def _invoke(self) -> None:
        if callable(self._command):
            self._command()

    def set_tooltip(self, text: str) -> None:
        self._tooltip.text = text

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if "tooltip" in kwargs:
            self.set_tooltip(kwargs.pop("tooltip"))
        result = super().configure(cnf, **kwargs)
        if hasattr(self, "_text"):
            self._draw()
        return result

    config = configure

    def destroy(self) -> None:
        self._tooltip.cancel()
        super().destroy()


class CaptionButton(tk.Canvas):
    """Windows 11-style caption control for the integrated titlebar."""

    def __init__(
        self, parent: tk.Misc, glyph: str, command, *, role: str = "normal",
        tooltip: str = "", selected: bool = False,
    ) -> None:
        self._surface = parent.cget("background")
        super().__init__(
            parent, width=46, height=TITLEBAR_HEIGHT, background=self._surface,
            borderwidth=0, highlightthickness=0, cursor="arrow", takefocus=1,
        )
        self._glyph = glyph
        self._command = command
        self._role = role
        self._selected = selected
        self._hovered = False
        self._pressed = False
        self._tooltip = FluentToolTip(self, tooltip)
        self.bind("<Configure>", lambda event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda event: self._invoke())
        self.bind("<space>", lambda event: self._invoke())
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if self._role == "close" and (self._hovered or self._pressed):
            fill = "#A4262C" if self._pressed else "#C42B1C"
            foreground = "#FFFFFF"
        else:
            fill = CONTROL_PRESSED if self._pressed else CONTROL_HOVER if self._hovered else self._surface
            foreground = ACCENT if self._selected else TEXT
        self.create_rectangle(
            0, 0, max(2, self.winfo_width()), max(2, self.winfo_height()),
            fill=fill, outline=fill,
        )
        self.create_text(
            self.winfo_width() / 2, self.winfo_height() / 2,
            text=self._glyph, fill=foreground, font=(FONT_ICON, 10),
        )

    def _on_enter(self, event) -> None:
        self._hovered = True
        self._draw()
        self._tooltip.schedule()

    def _on_leave(self, event) -> None:
        self._hovered = False
        self._pressed = False
        self._draw()
        self._tooltip.cancel()

    def _on_press(self, event) -> None:
        self.focus_set()
        self._pressed = True
        self._draw()
        self._tooltip.cancel()

    def _on_release(self, event) -> None:
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._pressed = False
        self._draw()
        if inside:
            self._invoke()

    def _invoke(self) -> None:
        if callable(self._command):
            self._command()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._draw()

    def set_tooltip(self, text: str) -> None:
        self._tooltip.text = text

    def destroy(self) -> None:
        self._tooltip.cancel()
        super().destroy()


class FluentScrollbar(tk.Canvas):
    """Compact Fluent overlay scrollbar without legacy arrow buttons."""

    def __init__(self, parent: tk.Misc, command) -> None:
        self._surface = parent.cget("background")
        super().__init__(
            parent, width=12, background=self._surface, borderwidth=0,
            highlightthickness=0, cursor="arrow",
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hovered = False
        self._drag_y: int | None = None
        self._drag_first = 0.0
        self.bind("<Configure>", lambda event: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first: str | float, last: str | float) -> None:
        self._first = max(0.0, min(1.0, float(first)))
        self._last = max(self._first, min(1.0, float(last)))
        self._draw()

    def _thumb_geometry(self) -> tuple[float, float] | None:
        if self._last - self._first >= 0.999:
            return None
        margin = 2.0
        track = max(1.0, self.winfo_height() - margin * 2)
        visible = max(0.01, self._last - self._first)
        thumb_height = min(track, max(24.0, track * visible))
        movable = max(0.0, track - thumb_height)
        denominator = max(0.001, 1.0 - visible)
        thumb_top = margin + movable * min(1.0, self._first / denominator)
        return thumb_top, thumb_top + thumb_height

    def _draw(self) -> None:
        self.delete("all")
        geometry = self._thumb_geometry()
        if geometry is None:
            return
        top, bottom = geometry
        thumb_width = 6 if self._hovered or self._drag_y is not None else 4
        x1 = (max(2, self.winfo_width()) - thumb_width) / 2
        _draw_rounded_rectangle(
            self, x1, top, x1 + thumb_width, bottom, thumb_width / 2,
            fill=SCROLL_THUMB, outline=SCROLL_THUMB,
        )

    def _on_enter(self, event) -> None:
        self._hovered = True
        self._draw()

    def _on_leave(self, event) -> None:
        self._hovered = False
        if self._drag_y is None:
            self._draw()

    def _on_press(self, event) -> None:
        geometry = self._thumb_geometry()
        if geometry is None:
            return
        top, bottom = geometry
        if top <= event.y <= bottom:
            self._drag_y = event.y
            self._drag_first = self._first
        else:
            self._command("scroll", -1 if event.y < top else 1, "pages")
        self._draw()

    def _on_drag(self, event) -> None:
        if self._drag_y is None:
            return
        geometry = self._thumb_geometry()
        if geometry is None:
            return
        top, bottom = geometry
        movable = max(1.0, self.winfo_height() - 4.0 - (bottom - top))
        visible = max(0.01, self._last - self._first)
        new_first = self._drag_first + (event.y - self._drag_y) / movable * (1.0 - visible)
        self._command("moveto", max(0.0, min(1.0 - visible, new_first)))

    def _on_release(self, event) -> None:
        self._drag_y = None
        self._draw()


class RoundedFrame(tk.Canvas):
    def __init__(
        self, parent: tk.Misc, *, fill: str, outline: str, radius: int = 12,
        padding: int = 13,
    ) -> None:
        surface = parent.cget("background") if "background" in parent.keys() else WINDOW_BG
        super().__init__(
            parent, background=surface, borderwidth=0, highlightthickness=0,
        )
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._padding = padding
        self.container = tk.Frame(self, background=fill)
        self._window_id = self.create_window(
            padding, padding, anchor="nw", window=self.container,
        )
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event) -> None:
        self.delete("panel")
        _draw_rounded_rectangle(
            self, 1, 1, max(2, event.width - 1), max(2, event.height - 1), self._radius,
            fill=self._fill, outline=self._outline, width=1, tags="panel",
        )
        self.tag_lower("panel")
        inner_width = max(1, event.width - self._padding * 2)
        inner_height = max(1, event.height - self._padding * 2)
        self.coords(self._window_id, self._padding, self._padding)
        self.itemconfigure(self._window_id, width=inner_width, height=inner_height)


@dataclass
class Config:
    qwen_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    qwen_api_key: str = ""
    accurate_model: str = "qwen-mt-plus"
    fast_model: str = "qwen-mt-turbo"
    translation_mode: str = "accurate"
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    api_key: str = ""
    model: str = "glm-4.7-flash"
    fallback_model: str = "glm-4-flash"
    target_language: str = "简体中文"
    research_domain: str = "自动识别科研领域"
    domain_prompt: str = "Academic research paper. Use formal scholarly language and field-standard terminology."
    glossary: str = ""
    hotkey: str = DEFAULT_HOTKEY
    theme: str = "system"
    always_on_top: bool = False

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            values = {k: raw[k] for k in cls.__annotations__ if k in raw}
            values["hotkey"] = normalize_hotkey(raw.get("hotkey", DEFAULT_HOTKEY))
            values["theme"] = normalize_theme(raw.get("theme", "system"))
            values["always_on_top"] = raw.get("always_on_top", False) is True
            return cls(**values)
        except (OSError, ValueError, TypeError):
            return cls(api_key=os.getenv("TRANSLATOR_API_KEY", ""))

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


class HotkeyDetector:
    """Turn polled Windows key states into one translation trigger."""

    def __init__(self) -> None:
        self.mode = ""
        self.was_down = False
        self.last_press = 0.0

    def reset(self) -> None:
        self.was_down = False
        self.last_press = 0.0

    def update(
        self, mode: object, now: float, *, ctrl: bool, alt: bool, c: bool, t: bool,
    ) -> bool:
        normalized = normalize_hotkey(mode)
        if normalized != self.mode:
            self.mode = normalized
            self.reset()

        if normalized == "ctrl_alt_t":
            down = ctrl and alt and t
            triggered = down and not self.was_down
            self.was_down = down
            return triggered

        if normalized == "ctrl_double_c":
            down = ctrl and c
            if not ctrl:
                self.last_press = 0.0
        elif normalized == "double_alt":
            down = alt
        else:
            down = ctrl

        triggered = False
        if down and not self.was_down:
            interval = now - self.last_press
            if 0.08 < interval < 0.42:
                triggered = True
                self.last_press = 0.0
            else:
                self.last_press = now
        self.was_down = down
        return triggered


def detect_translation_direction(text: str) -> tuple[str, str]:
    letters = sum(ch.isalpha() and ord(ch) < 128 for ch in text)
    chinese = sum("\u4e00" <= ch <= "\u9fff" for ch in text)
    return ("Chinese", "English") if chinese > max(2, letters // 3) else ("English", "Chinese")


def parse_glossary(raw: str) -> list[dict[str, str]]:
    terms = []
    for item in raw.replace("\n", ";").split(";"):
        if "=" not in item:
            continue
        source, target = (part.strip() for part in item.split("=", 1))
        if source and target:
            terms.append({"source": source, "target": target})
    return terms[:100]


def normalize_pdf_layout(text: str) -> str:
    """Remove visual PDF line wraps while preserving semantic block structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    if len(lines) <= 1:
        return text.strip()

    bullet = re.compile(r"^(?:[-•*▪◦]|\(?\d+[.)]|[A-Za-z][.)])\s+")
    result = ""
    for index, line in enumerate(lines):
        if not line:
            if result and not result.endswith("\n\n"):
                result = result.rstrip() + "\n\n"
            continue
        if not result or result.endswith("\n\n"):
            result += line
            continue

        previous_line = lines[index - 1]
        previous_token = re.search(r"([A-Za-z][A-Za-z0-9_-]*)$", previous_line)
        next_token = re.match(r"([A-Za-z][A-Za-z0-9_-]*)", line)
        formula_like = (
            bullet.match(line) or bullet.match(previous_line)
            or ("=" in previous_line and len(previous_line) < 100)
            or ("=" in line and len(line) < 100)
        )
        if formula_like:
            separator = "\n"
        elif previous_line.endswith("-") and next_token:
            result = result[:-1]
            separator = ""
        elif (
            previous_token and next_token
            and previous_token.group(1).isupper() and next_token.group(1).isupper()
            and len(previous_token.group(1) + next_token.group(1)) <= 24
        ):
            separator = ""
        elif previous_line[-1:] >= "\u4e00" and previous_line[-1:] <= "\u9fff" and "\u4e00" <= line[:1] <= "\u9fff":
            separator = ""
        else:
            separator = " "
        result += separator + line
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def normalize_translation_output(text: str, source: str = "") -> str:
    """Clean model/PDF spacing without changing normal English word spacing."""
    text = normalize_pdf_layout(text)
    # Chinese layout never needs spaces inserted between two Han characters.
    text = re.sub(r"(?<=[\u3400-\u9fff])[ \t]+(?=[\u3400-\u9fff])", "", text)
    # Restore acronyms that are present unbroken in the source (NU MISHEET -> NUMISHEET).
    source_terms = set(re.findall(r"\b[A-Z][A-Z0-9_-]{3,23}\b", source))
    if source_terms:
        split_caps = re.compile(r"\b(?:[A-Z][A-Z_-]*[ \t]+)+[A-Z][A-Z_-]*\b")

        def restore_acronym(match: re.Match) -> str:
            compact = re.sub(r"[ \t]+", "", match.group(0))
            return compact if compact in source_terms else match.group(0)

        text = split_caps.sub(restore_acronym, text)
    return text.strip()


def load_translation_memory() -> list[dict[str, str]]:
    try:
        data = json.loads(TM_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def relevant_memories(text: str, memories: list[dict[str, str]]) -> list[dict[str, str]]:
    ranked = []
    for item in memories:
        source, target = item.get("source", ""), item.get("target", "")
        if not source or not target:
            continue
        score = difflib.SequenceMatcher(None, text.lower(), source.lower()).ratio()
        if score >= 0.35:
            ranked.append((score, {"source": source, "target": target}))
    return [item for _, item in sorted(ranked, key=lambda pair: pair[0], reverse=True)[:5]]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD), ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.wintypes.WPARAM),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.wintypes.LONG), ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD), ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD), ("dwExtraInfo", ctypes.wintypes.WPARAM),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD), ("wParamH", ctypes.wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", ctypes.wintypes.DWORD), ("union", INPUT_UNION)]


def send_ctrl_c() -> bool:
    key_up = 0x0002
    events = (INPUT * 4)(
        INPUT(type=1, ki=KEYBDINPUT(wVk=0x11)),
        INPUT(type=1, ki=KEYBDINPUT(wVk=0x43)),
        INPUT(type=1, ki=KEYBDINPUT(wVk=0x43, dwFlags=key_up)),
        INPUT(type=1, ki=KEYBDINPUT(wVk=0x11, dwFlags=key_up)),
    )
    sent = ctypes.windll.user32.SendInput(4, ctypes.byref(events), ctypes.sizeof(INPUT))
    return sent == 4


def translate_glm_stream(text: str, cfg: Config, on_chunk, model: str | None = None) -> str:
    if not cfg.api_key:
        raise RuntimeError("尚未设置 API Key，请先打开设置。")
    glossary_rule = f"必须优先采用以下术语映射：{cfg.glossary}。" if cfg.glossary else ""
    prompt = (
        f"你是一名严谨的科研论文翻译专家，研究领域为：{cfg.research_domain}。"
        f"把用户提供的内容准确、自然地翻译为{cfg.target_language}；如果原文已经是目标语言，则翻译为学术英语。"
        "先在内部判断段落所属学科与语境，再选择该领域通行的专业译法，避免脱离语境的逐字直译。"
        "保持术语前后一致；保留公式、变量、单位、引文编号、DOI、基因/蛋白名称和尚无公认译名的缩写。"
        "首次出现且可能有歧义的核心术语，可在译文后保留英文原词括注。"
        f"{glossary_rule}"
        "忠实保留限定词、否定、因果关系、概率和不确定性，不得补充原文没有的结论。"
        "保留语义段落、列表、公式和表格结构，但不要保留 PDF 页面中的视觉换行或断词；只输出译文，不解释翻译过程。"
    )
    payload = json.dumps({
        "model": model or cfg.model,
        "temperature": 0.1,
        "stream": True,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.api_url,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.api_key}"},
        method="POST",
    )
    try:
        parts: list[str] = []
        with urllib.request.urlopen(req, timeout=15) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                data = json.loads(body)
                chunk = data["choices"][0].get("delta", {}).get("content") or ""
                if chunk:
                    parts.append(chunk)
                    on_chunk(chunk)
        return "".join(parts).strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if exc.code == 429:
            raise RuntimeError("429：免费模型当前并发已满") from exc
        raise RuntimeError(f"接口返回错误 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"网络连接失败：{exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError("接口响应格式不兼容，请检查 API 地址和模型名称。") from exc


def translate_qwen_stream(
    text: str, cfg: Config, model: str, memories: list[dict[str, str]], on_update,
) -> str:
    if not cfg.qwen_api_key:
        raise RuntimeError("尚未配置百炼 API Key")
    source_lang, target_lang = detect_translation_direction(text)
    options: dict[str, object] = {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "domains": cfg.domain_prompt.strip(),
    }
    terms = parse_glossary(cfg.glossary)
    if terms:
        options["terms"] = terms
    matched_memories = relevant_memories(text, memories)
    if matched_memories:
        options["tm_list"] = matched_memories
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "translation_options": options,
        "temperature": 0.1,
        "stream": True,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg.qwen_api_url,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.qwen_api_key}"},
        method="POST",
    )
    latest = ""
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                data = json.loads(body)
                choices = data.get("choices") or []
                if not choices:
                    continue
                content = choices[0].get("delta", {}).get("content") or ""
                if content:
                    latest = content
                    on_update(latest)
        return latest.strip()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"百炼接口错误 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"百炼网络连接失败：{exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError("百炼响应格式异常，请检查接口区域和模型设置。") from exc


class QuickTranslator:
    def __init__(self) -> None:
        self.cfg = Config.load()
        self._executable_name = Path(sys.executable).stem.lower()
        preview_theme = (
            "dark" if "darkpreview" in self._executable_name
            else "light" if "lightpreview" in self._executable_name
            else None
        )
        self._preview_theme = preview_theme
        self._resolved_theme = preview_theme or resolve_theme(self.cfg.theme)
        apply_palette(self._resolved_theme)
        self.root = tk.Tk()
        self.root.title("QuickTranslator")
        self.root.attributes("-alpha", 1.0)
        self.root.geometry("680x300")
        self.root.minsize(520, MIN_WINDOW_HEIGHT)
        self.root.configure(background=WINDOW_BG)
        self.root.attributes("-topmost", False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind("<Escape>", lambda event: self.hide())
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
        self._user_resized = False
        self._resize_job = None
        self._resize_animation_job = None
        self._resize_target = None
        self._scrollbar_job = None
        self._edge_resize = None
        self._cursor_widget = None
        self._cursor_original = ""
        self._last_root_size: tuple[int, int] | None = None
        self._quitting = False
        self._tray_image = self._make_tray_image()
        self._build_ui()
        self.root.bind("<Configure>", self._track_native_resize, add="+")
        self._start_tray()
        self.root.after(100, self._poll_events)
        self.root.after(2500, self._poll_system_theme)
        settings_preview = "--settings-preview" in sys.argv or "settingspreview" in self._executable_name
        short_content_preview = "shortcontentpreview" in self._executable_name
        content_preview = (
            "--content-preview" in sys.argv or "contentpreview" in self._executable_name
        )
        preview_mode = (
            "--ui-preview" in sys.argv or settings_preview or self._executable_name.endswith("preview")
        )
        if preview_mode:
            self.root.after(40, lambda: apply_fluent_window(self.root, 0))
            if settings_preview:
                self.root.after(250, self.open_settings)
            if content_preview:
                preview_text = (
                    "这是一段用于检查短译文高度的两行文本，窗口应完整显示它，不需要滚动。"
                    if short_content_preview else
                    (
                        "多焦视网膜电图（mfERG）是一种电生理检查方法，可同时评估视网膜多个独立区域的功能。"
                        "本文由国际临床视觉电生理学会（ISCEV）发布，旨在提供经更新与修订的临床多焦"
                        "视网膜电图标准，并界定基本临床 mfERG 记录与报告的最低规范，以确保来自全球不同"
                        "实验室的检测结果能够被准确识别并进行比较。与先前标准相比，本次修订的主要变化包括："
                        "用于记录的 m 序列的最小长度、结果报告方式，以及文档格式的调整。"
                    ) * 8
                )
                self.root.after(180, lambda: self.show_message(preview_text, resize=True))
                self.root.after(200, lambda: self.status.config(text="翻译完成"))
        else:
            self.root.after(500, self.hide)
            self.root.after(40, lambda: apply_fluent_window(self.root, 0))
        threading.Thread(target=self._translation_hotkey_loop, daemon=True).start()
        threading.Thread(target=self._double_shift_loop, daemon=True).start()

    def _build_ui(self) -> None:
        self._configure_fluent_styles()
        self._window_icon = ImageTk.PhotoImage(
            self._tray_image.resize((64, 64), Image.Resampling.LANCZOS)
        )
        self.root.iconphoto(True, self._window_icon)

        toolbar = tk.Frame(
            self.root, background=SURFACE_ALT, height=TOOLBAR_HEIGHT,
            borderwidth=0, highlightthickness=0,
        )
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        self.mode_var = tk.StringVar(value=f"模型：{self._mode_label()}")
        self.mode_button = ttk.Menubutton(
            toolbar, textvariable=self.mode_var, style="Toolbar.TMenubutton",
        )
        mode_menu = tk.Menu(toolbar, tearoff=False)
        mode_menu.add_command(label="精准 Plus", command=lambda: self.set_mode("accurate"))
        mode_menu.add_command(label="极速 Turbo", command=lambda: self.set_mode("fast"))
        self.mode_button.configure(menu=mode_menu)
        self.mode_button.pack(side="left", padx=(8, 4), pady=7)

        theme_action = "浅色" if self._resolved_theme == "dark" else "深色"
        self.theme_button = ttk.Button(
            toolbar, text=theme_action, command=self.toggle_theme, style="Toolbar.TButton",
        )
        self.theme_button.pack(side="left", padx=4, pady=7)
        ttk.Button(
            toolbar, text="设置…", command=self.open_settings, style="Toolbar.TButton",
        ).pack(side="left", padx=4, pady=7)

        panel = tk.Frame(
            self.root, background=SURFACE, borderwidth=0,
            highlightthickness=1, highlightbackground=BORDER,
        )
        self.translation_panel = panel
        panel.pack(fill="both", expand=True)
        panel_body = panel
        panel_body.rowconfigure(0, weight=1)
        panel_body.columnconfigure(0, weight=1)
        self.output = tk.Text(
            panel_body, wrap="char", padx=16, pady=14, height=4, font=(FONT_TEXT, 11),
            relief="flat", borderwidth=0, highlightthickness=0,
            background=SURFACE, foreground=TEXT, insertbackground=TEXT,
            selectbackground=SELECTION, spacing1=3, spacing3=3, undo=True,
            insertwidth=1, takefocus=1,
            yscrollcommand=self._on_output_scroll,
        )
        self.output.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = FluentScrollbar(panel_body, command=self.output.yview)

        bottom = tk.Frame(panel_body, background=SURFACE, height=48)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 10))
        self.status = tk.Label(
            bottom, text="", foreground=MUTED, background=SURFACE,
            font=(FONT_TEXT, 9), anchor="w",
        )
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Button(
            bottom, text="复制", command=self.copy_result, style="Action.TButton",
        ).pack(side="right", padx=(6, 0))
        ttk.Button(
            bottom, text="确认", command=self.confirm_translation, style="Primary.TButton",
        ).pack(side="right")

    def _configure_fluent_styles(self) -> None:
        style = ttk.Style(self.root)
        candidates = ("clam",) if IS_DARK else ("vista", "winnative", "clam")
        for candidate in candidates:
            if candidate in style.theme_names():
                style.theme_use(candidate)
                break
        style.configure("Toolbar.TButton", padding=(10, 4))
        style.configure("Toolbar.TMenubutton", padding=(10, 4))
        style.configure("Action.TButton", padding=(12, 5))
        style.configure("Primary.TButton", padding=(12, 5))
        if IS_DARK:
            for name in ("Toolbar.TButton", "Toolbar.TMenubutton", "Action.TButton"):
                style.configure(
                    name, background=CONTROL_FILL, foreground=TEXT,
                    bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                    darkcolor=BORDER_STRONG,
                )
                style.map(
                    name, background=[("active", CONTROL_HOVER), ("pressed", CONTROL_PRESSED)],
                )
            style.configure(
                "Primary.TButton", background=ACCENT, foreground=ACCENT_TEXT,
                bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
            )
            style.map(
                "Primary.TButton",
                background=[("active", ACCENT_HOVER), ("pressed", ACCENT_PRESSED)],
            )
        style.configure(
            "Fluent.TEntry", fieldbackground=CONTROL_FILL, foreground=TEXT,
            bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
            insertcolor=TEXT, padding=(10, 8), borderwidth=1,
        )
        style.map(
            "Fluent.TEntry",
            bordercolor=[("focus", ACCENT), ("active", BORDER_STRONG)],
            lightcolor=[("focus", ACCENT)], darkcolor=[("focus", ACCENT)],
        )
        style.configure(
            "Fluent.TCombobox", fieldbackground=CONTROL_FILL, background=CONTROL_FILL,
            foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER_STRONG,
            lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
            padding=(10, 7), borderwidth=1,
        )
        style.map(
            "Fluent.TCombobox",
            fieldbackground=[("readonly", CONTROL_FILL)],
            selectbackground=[("readonly", CONTROL_FILL)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT), ("active", BORDER_STRONG)],
        )
    def _on_output_scroll(self, first: str, last: str) -> None:
        if not hasattr(self, "scrollbar"):
            return
        self.scrollbar.set(first, last)
        if self._scrollbar_job is None:
            self._scrollbar_job = self.root.after_idle(self._refresh_output_scrollbar)

    def _refresh_output_scrollbar(self) -> None:
        self._scrollbar_job = None
        try:
            first, last = self.output.yview()
            self.scrollbar.set(first, last)
            first_line = self.output.dlineinfo("1.0")
            last_line = self.output.dlineinfo("end-1c")
            widget_height = self.output.winfo_height()
            overflowing = (
                first_line is None or last_line is None
                or first_line[1] < 0
                or last_line[1] + last_line[3] > widget_height
            )
        except tk.TclError:
            return
        if overflowing and not self.scrollbar.winfo_ismapped():
            self.scrollbar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        elif not overflowing and self.scrollbar.winfo_ismapped():
            self.scrollbar.grid_remove()

    def _fluent_button(
        self, parent, text: str, command, kind: str = "secondary", *,
        icon: bool = False, width: int | None = None, tooltip: str = "",
    ) -> FluentButton:
        return FluentButton(
            parent, text, command, kind=kind, icon=icon, width=width, tooltip=tooltip,
        )

    def _track_native_resize(self, event) -> None:
        if event.widget != self.root:
            return
        size = (event.width, event.height)
        if (
            self._last_root_size is not None
            and size != self._last_root_size
            and ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000
        ):
            self._user_resized = True
            if hasattr(self, "translation_panel"):
                self.translation_panel.pack_configure(fill="both", expand=True)
        self._last_root_size = size

    def _poll_system_theme(self) -> None:
        if not self._quitting and self.cfg.theme == "system" and self._preview_theme is None:
            resolved = system_theme()
            settings = getattr(self, "_settings_window", None)
            settings_open = settings is not None and settings.winfo_exists()
            if resolved != self._resolved_theme and not settings_open:
                self._apply_resolved_theme(resolved)
        if not self._quitting:
            self.root.after(2500, self._poll_system_theme)

    def _apply_resolved_theme(self, resolved: str) -> None:
        current_text = self.output.get("1.0", "end-1c") if hasattr(self, "output") else ""
        self._resolved_theme = resolved
        apply_palette(resolved)
        for child in list(self.root.winfo_children()):
            if not isinstance(child, tk.Toplevel):
                child.destroy()
        self.root.configure(background=WINDOW_BG)
        self._build_ui()
        if current_text:
            self.output.delete("1.0", "end")
            self.output.insert("1.0", current_text)
        self.root.after(20, lambda: apply_fluent_window(self.root, 0))

    def _mode_label(self) -> str:
        return "精准 Plus" if self.cfg.translation_mode == "accurate" else "极速 Turbo"

    def set_mode(self, mode: str) -> None:
        if mode not in {"accurate", "fast"}:
            return
        self.cfg.translation_mode = mode
        if hasattr(self, "mode_var"):
            self.mode_var.set(f"模型：{self._mode_label()}")
        self.cfg.save()
        self.status.config(text=f"已切换为 {self._mode_label()}")

    def toggle_mode(self) -> None:
        mode = "fast" if self.cfg.translation_mode == "accurate" else "accurate"
        self.set_mode(mode)

    def toggle_theme(self) -> None:
        resolved = "light" if self._resolved_theme == "dark" else "dark"
        self.cfg.theme = resolved
        self.cfg.save()
        self._apply_resolved_theme(resolved)
        self.status.config(text=f"已切换为{THEME_LABELS[resolved]}模式")

    def _restore_topmost(self) -> None:
        if not self._quitting:
            self.root.attributes("-topmost", False)

    def toggle_topmost(self) -> None:
        self.cfg.always_on_top = not self.cfg.always_on_top
        self.cfg.save()
        self._restore_topmost()
        self.pin_button.set_selected(self.cfg.always_on_top)
        self.pin_button.set_tooltip("取消始终置顶" if self.cfg.always_on_top else "始终置顶")
        self.status.config(text="窗口已始终置顶" if self.cfg.always_on_top else "已取消始终置顶")

    def minimize_window(self) -> None:
        """Minimize the borderless main window through the native window handle."""
        try:
            self.root.update_idletasks()
            client_hwnd = self.root.winfo_id()
            hwnd = ctypes.windll.user32.GetParent(client_hwnd) or client_hwnd
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        except (AttributeError, OSError, tk.TclError):
            self.hide()

    def _start_drag(self, event) -> None:
        if self._edge_at(event):
            return
        self._drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_window(self, event) -> None:
        if self._edge_resize:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{x}+{y}")

    def _bind_drag_area(self, widget) -> None:
        if not isinstance(widget, tk.Button):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag_window)
        for child in widget.winfo_children():
            self._bind_drag_area(child)

    def _bind_edge_resize(self) -> None:
        self.root.bind_all("<Motion>", self._edge_motion, add="+")
        self.root.bind_all("<ButtonPress-1>", self._edge_press, add="+")
        self.root.bind_all("<B1-Motion>", self._edge_drag, add="+")
        self.root.bind_all("<ButtonRelease-1>", self._edge_release, add="+")

    def _event_on_root(self, event) -> bool:
        try:
            return event.widget.winfo_toplevel() == self.root and self.root.state() != "withdrawn"
        except tk.TclError:
            return False

    def _edge_at(self, event):
        if not self._event_on_root(event):
            return None
        x = event.x_root - self.root.winfo_rootx()
        y = event.y_root - self.root.winfo_rooty()
        width, height, margin = self.root.winfo_width(), self.root.winfo_height(), 7
        horizontal = "w" if x <= margin else "e" if x >= width - margin else ""
        vertical = "n" if y <= margin else "s" if y >= height - margin else ""
        return vertical + horizontal or None

    def _edge_motion(self, event) -> None:
        if self._edge_resize:
            return
        edge = self._edge_at(event)
        cursors = {
            "w": "size_we", "e": "size_we", "n": "size_ns", "s": "size_ns",
            "nw": "size_nw_se", "se": "size_nw_se", "ne": "size_ne_sw", "sw": "size_ne_sw",
        }
        if self._cursor_widget is not None and (not edge or self._cursor_widget != event.widget):
            try:
                self._cursor_widget.configure(cursor=self._cursor_original)
            except tk.TclError:
                pass
            self._cursor_widget = None
        if self._event_on_root(event) and edge:
            if self._cursor_widget != event.widget:
                try:
                    self._cursor_original = event.widget.cget("cursor")
                    self._cursor_widget = event.widget
                except tk.TclError:
                    self._cursor_original = ""
                    self._cursor_widget = self.root
            self._cursor_widget.configure(cursor=cursors[edge])

    def _edge_press(self, event) -> None:
        edge = self._edge_at(event)
        if not edge:
            return
        self._edge_resize = edge
        self._user_resized = True
        self._edge_origin = (
            event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(),
            self.root.winfo_width(), self.root.winfo_height(),
        )

    def _edge_drag(self, event) -> None:
        if not self._edge_resize:
            return
        start_x, start_y, win_x, win_y, width, height = self._edge_origin
        dx, dy = event.x_root - start_x, event.y_root - start_y
        edge = self._edge_resize
        new_x, new_y, new_width, new_height = win_x, win_y, width, height
        if "e" in edge:
            new_width = max(380, width + dx)
        if "s" in edge:
            new_height = max(MIN_WINDOW_HEIGHT, height + dy)
        if "w" in edge:
            new_width = max(380, width - dx)
            new_x = win_x + width - new_width
        if "n" in edge:
            new_height = max(MIN_WINDOW_HEIGHT, height - dy)
            new_y = win_y + height - new_height
        self.root.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")

    def _edge_release(self, event) -> None:
        self._edge_resize = None

    def _start_resize(self, event) -> None:
        self._user_resized = True
        self._resize_origin = (
            event.x_root, event.y_root, self.root.winfo_width(), self.root.winfo_height(),
        )

    def _resize_window(self, event) -> None:
        start_x, start_y, start_width, start_height = self._resize_origin
        width = max(520, min(self.root.winfo_screenwidth() - 20, start_width + event.x_root - start_x))
        height = max(
            MIN_WINDOW_HEIGHT,
            min(self.root.winfo_screenheight() - 80, start_height + event.y_root - start_y),
        )
        self.root.geometry(f"{width}x{height}")

    def _make_tray_image(self) -> Image.Image:
        try:
            with Image.open(ICON_PATH) as source:
                return source.convert("RGBA").resize((256, 256), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            pass
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((3, 3, 61, 61), radius=14, fill=ACCENT)
        draw.rounded_rectangle((14, 13, 39, 38), radius=6, fill="#FFFFFF")
        draw.rounded_rectangle((27, 26, 52, 51), radius=6, fill="#D8ECFC")
        draw.line((20, 23, 33, 23), fill=ACCENT, width=3)
        draw.line((26, 17, 26, 30), fill=ACCENT, width=3)
        draw.line((34, 37, 45, 37), fill=ACCENT, width=3)
        draw.line((39, 32, 39, 43), fill=ACCENT, width=3)
        return image

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("显示翻译窗口", lambda icon, item: self.events.put(("show",)), default=True),
            pystray.MenuItem("设置", lambda icon, item: self.events.put(("settings",))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda icon, item: self.events.put(("exit",))),
        )
        self.tray_icon = pystray.Icon("QuickTranslator", self._tray_image, APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

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
        render_text = None
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
                    self.open_settings()
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
                    self.status.config(text="翻译完成")
                elif kind == "status" and event[1] == self._request_id:
                    self.status.config(text=event[2])
                elif kind == "error" and event[1] == self._request_id:
                    self._show_error(event[2])
        except queue.Empty:
            pass
        # Coalesce all network chunks received in this frame into one text redraw.
        if render_text is not None:
            self.show_translation(render_text, final=translation_done)
        elif translation_done:
            self.show_translation(self._stream_text, final=True)
        self.root.after(33, self._poll_events)

    def capture_and_translate(self) -> None:
        self._clipboard_sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if not send_ctrl_c():
            self._capture_in_progress = False
            self._show_error("Windows 未能发送复制指令；程序没有修改剪贴板。")
            return
        self._capture_deadline = time.monotonic() + 1.2
        self.root.after(60, self._read_selection)

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
            self.root.after(20, self._capture_after_hotkey_release)
            return
        if ctrl_is_down or alt_is_down:
            self._capture_in_progress = False
            self._show_error("快捷键没有及时松开，已安全取消；未操作剪贴板。")
            return
        self.root.after(35, self.capture_and_translate)

    def _read_selection(self) -> None:
        sequence = ctypes.windll.user32.GetClipboardSequenceNumber()
        if sequence == self._clipboard_sequence:
            if time.monotonic() < self._capture_deadline:
                self.root.after(50, self._read_selection)
                return
            self._capture_in_progress = False
            self._show_error("没有读取到选中文字；程序未改写你的剪贴板。")
            return
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            if time.monotonic() < self._capture_deadline:
                self.root.after(50, self._read_selection)
                return
            self._capture_in_progress = False
            self._show_error("剪贴板正被其他程序占用，无法读取选中文字。")
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
        self._user_resized = False
        self.show_message("正在连接…")
        self.status.config(text=f"正在翻译…（{hotkey_label(self.cfg.hotkey)} 可开始新任务）")
        self._position_near_cursor()
        threading.Thread(target=self._translate_worker, args=(text, request_id), daemon=True).start()

    def _translate_worker(self, text: str, request_id: int) -> None:
        if self.cfg.qwen_api_key:
            qwen_model = (
                self.cfg.accurate_model if self.cfg.translation_mode == "accurate"
                else self.cfg.fast_model
            )
            try:
                self.events.put(("status", request_id, f"正在使用 {qwen_model} 翻译…"))
                result = translate_qwen_stream(
                    text, self.cfg, qwen_model, self._memories,
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
                    text, self.cfg,
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
                        "status", request_id,
                        f"{model} 繁忙，正在切换备用模型 {next_model}…",
                    ))
                    continue
                if "429" in message:
                    message = "免费模型当前拥堵，请稍后再试；这不是电脑卡死。"
                self.events.put(("error", request_id, message))
                return

    def _position_near_cursor(self) -> None:
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        width = max(520, self.root.winfo_width())
        height = max(MIN_WINDOW_HEIGHT, self.root.winfo_height())
        x = min(point.x + 18, self.root.winfo_screenwidth() - width - 12)
        y = min(point.y + 18, self.root.winfo_screenheight() - height - 60)
        self.root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(800, self._restore_topmost)

    def _show_error(self, message: str) -> None:
        self.show_message(f"翻译失败\n\n{message}")
        self.status.config(text="翻译失败")
        self._position_near_cursor()

    def show_message(self, message: str, resize: bool = True) -> None:
        current = self.output.get("1.0", "end-1c")
        common = 0
        limit = min(len(current), len(message))
        while common < limit and current[common] == message[common]:
            common += 1
        if common < len(current):
            self.output.delete(f"1.0+{common}c", "end")
        if common < len(message):
            self.output.insert("end", message[common:])
        if not resize:
            self.output.see("end")
        if not self._user_resized:
            if resize and self._resize_job is not None:
                self.root.after_cancel(self._resize_job)
                self._resize_job = None
            # Measure at most once per short frame group. Streaming therefore grows
            # continuously, without doing an expensive layout pass for every token.
            if self._resize_job is None:
                self._resize_job = self.root.after(0 if resize else 70, self._auto_size_to_content)

    def show_translation(self, text: str, final: bool = False) -> None:
        rendered = normalize_translation_output(text, self._current_source)
        # Chunk redraws are coalesced; height follows them through a separate animation.
        self.show_message(rendered, resize=final)

    def _auto_size_to_content(self) -> None:
        self._resize_job = None
        if self._user_resized or self.root.state() == "withdrawn":
            return
        self.root.update_idletasks()
        try:
            display_lines = display_line_count(
                self.output.count("1.0", "end-1c", "displaylines")[0]
            )
        except (TypeError, tk.TclError):
            display_lines = max(1, self.output.get("1.0", "end-1c").count("\n") + 1)
        try:
            font_metrics = int(self.output.tk.call("font", "metrics", self.output.cget("font"), "-linespace"))
            # spacing1/spacing3 apply to the paragraph edges, not to every wrapped line.
            line_height = font_metrics
        except (TypeError, ValueError, tk.TclError):
            line_height = 24
        screen_height = self.root.winfo_screenheight()
        target_height = calculate_window_height(display_lines, line_height, screen_height)
        panel_height = min(
            calculate_panel_height(display_lines, line_height),
            target_height - TOOLBAR_HEIGHT,
        )
        if hasattr(self, "translation_panel"):
            self.translation_panel.configure(height=max(120, panel_height))
            self.translation_panel.pack_configure(fill="x", expand=False)
        width = max(520, self.root.winfo_width())
        x, y = self.root.winfo_x(), self.root.winfo_y()
        target_y = min(y, screen_height - target_height - 55)
        self._resize_target = (target_height, max(0, target_y))
        if self._resize_animation_job is None:
            self._animate_height()

    def _animate_height(self) -> None:
        self._resize_animation_job = None
        if self._user_resized or self.root.state() == "withdrawn" or not self._resize_target:
            return
        target_height, target_y = self._resize_target
        height, y = self.root.winfo_height(), self.root.winfo_y()
        # Ease toward the latest target; a changing stream simply updates that target.
        next_height = height + max(-18, min(18, round((target_height - height) * 0.28)))
        next_y = y + max(-18, min(18, round((target_y - y) * 0.28)))
        if abs(target_height - height) <= 2:
            next_height = target_height
        if abs(target_y - y) <= 2:
            next_y = target_y
        width, x = max(520, self.root.winfo_width()), max(0, self.root.winfo_x())
        self.root.geometry(f"{width}x{next_height}+{x}+{max(0, next_y)}")
        if self._scrollbar_job is None:
            self._scrollbar_job = self.root.after_idle(self._refresh_output_scrollbar)
        if next_height != target_height or next_y != target_y:
            self._resize_animation_job = self.root.after(16, self._animate_height)

    def copy_result(self) -> None:
        text = self.output.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.config(text="已复制译文")

    def confirm_translation(self) -> None:
        source = self._current_source.strip()
        target = self.output.get("1.0", "end-1c").strip()
        if not source or not target or target.startswith(("正在", "翻译失败")):
            self.status.config(text="当前没有可确认的译文")
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
        TM_PATH.write_text(json.dumps(self._memories, ensure_ascii=False, indent=2), encoding="utf-8")
        self.status.config(text=f"已加入本地翻译记忆（{len(self._memories)} 条）")

    def hide(self) -> None:
        self.root.withdraw()

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, self._restore_topmost)

    def exit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.tray_icon.stop()
        self.root.destroy()

    def open_settings(self, initial_page: str = "qwen") -> None:
        existing = getattr(self, "_settings_window", None)
        if existing is not None and existing.winfo_exists():
            selector = getattr(self, "_settings_select_page", None)
            if callable(selector):
                selector(initial_page)
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        win = tk.Toplevel(self.root)
        self._settings_window = win
        win.title("QuickTranslator · 设置")
        win.geometry("790x570")
        win.minsize(700, 510)
        win.configure(background=WINDOW_BG)
        win.transient(self.root)
        win.iconphoto(True, self._window_icon)

        def close_settings() -> None:
            try:
                win.grab_release()
            except tk.TclError:
                pass
            self._settings_window = None
            self._settings_select_page = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", close_settings)
        win.bind("<Escape>", lambda event: close_settings())
        win.after(40, lambda: apply_fluent_window(win))

        body = tk.Frame(win, background=WINDOW_BG, padx=14, pady=14)
        body.pack(fill="both", expand=True)
        navigation = tk.Frame(body, background=WINDOW_BG, width=58)
        navigation.pack(side="left", fill="y", padx=(0, 16))
        navigation.pack_propagate(False)
        content_host = tk.Frame(body, background=WINDOW_BG)
        content_host.pack(side="left", fill="both", expand=True)

        entries: dict[str, ttk.Entry] = {}
        hotkey_var = tk.StringVar(value=hotkey_label(self.cfg.hotkey))
        theme_var = tk.StringVar(value=theme_label(self.cfg.theme))
        pages: dict[str, tk.Frame] = {}
        navigation_buttons: dict[str, FluentButton] = {}

        def make_page(key: str, title: str, subtitle: str) -> tuple[tk.Frame, tk.Frame]:
            page = tk.Frame(content_host, background=WINDOW_BG)
            pages[key] = page
            tk.Label(
                page, text=title, foreground=TEXT, background=WINDOW_BG,
                font=(FONT_DISPLAY, 15, "bold"),
            ).pack(anchor="w")
            tk.Label(
                page, text=subtitle, foreground=MUTED, background=WINDOW_BG,
                font=(FONT_TEXT, 9), wraplength=520, justify="left",
            ).pack(anchor="w", pady=(3, 14))
            card = tk.Frame(page, background=WINDOW_BG, borderwidth=0, padx=0, pady=2)
            card.pack(fill="x")
            return page, card

        def add_page(key: str, title: str, subtitle: str, fields, note: str) -> None:
            _, card = make_page(key, title, subtitle)
            for row, (label, key, secret) in enumerate(fields):
                tk.Label(
                    card, text=label, foreground=TEXT, background=WINDOW_BG,
                    font=(FONT_TEXT, 9),
                ).grid(row=row, column=0, sticky="w", pady=9)
                entry = ttk.Entry(card, show="•" if secret else "", style="Fluent.TEntry")
                entry.insert(0, getattr(self.cfg, key))
                entry.grid(row=row, column=1, sticky="ew", padx=(20, 0), pady=7)
                entries[key] = entry
            card.columnconfigure(1, weight=1)
            tk.Label(
                card, text=note, foreground=MUTED, background=WINDOW_BG,
                font=(FONT_TEXT, 9), wraplength=510, justify="left",
            ).grid(
                row=len(fields), column=0, columnspan=2, sticky="w", pady=(13, 4)
            )

        _, appearance_card = make_page(
            "appearance", "外观", "让 QuickTranslator 与 Windows 保持一致，或固定使用一种外观",
        )
        tk.Label(
            appearance_card, text="应用主题", foreground=TEXT, background=WINDOW_BG,
            font=(FONT_TEXT, 9),
        ).grid(row=0, column=0, sticky="w", pady=9)
        theme_box = ttk.Combobox(
            appearance_card, textvariable=theme_var, values=list(THEME_LABELS.values()),
            state="readonly", style="Fluent.TCombobox",
        )
        theme_box.grid(row=0, column=1, sticky="ew", padx=(20, 0), pady=9)
        appearance_card.columnconfigure(1, weight=1)
        tk.Label(
            appearance_card,
            text=(
                "“跟随 Windows”会读取系统的应用颜色设置；切换系统深浅色后，窗口会自动更新。"
                "也可以在这里固定为浅色或深色。"
            ),
            foreground=MUTED, background=WINDOW_BG, font=(FONT_TEXT, 9),
            wraplength=510, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(15, 4))

        add_page("qwen", "百炼 Qwen-MT", "优先使用的专用翻译模型", [
            ("API 地址", "qwen_api_url", False), ("API Key", "qwen_api_key", True),
            ("精准模型", "accurate_model", False), ("极速模型", "fast_model", False),
        ], "推荐北京区域 Workspace 专属地址。未填写百炼 Key 时，程序会继续使用 GLM 备用通道。")
        add_page("research", "科研翻译", "让译文遵循你的学科语境和术语偏好", [
            ("科研领域", "research_domain", False), ("英文领域提示", "domain_prompt", False),
            ("术语表", "glossary", False), ("GLM 目标语言", "target_language", False),
        ], "术语表示例：cell culture=细胞培养; power=统计功效。Qwen-MT 会自动判断中译英或英译中。")
        add_page("glm", "备用 GLM", "百炼暂时不可用时的自动回退通道", [
            ("API 地址", "api_url", False), ("API Key", "api_key", True),
            ("首选模型", "model", False), ("备用模型", "fallback_model", False),
        ], "百炼未配置或调用失败时自动使用该通道。现有智谱设置会被保留。")

        _, shortcut_card = make_page(
            "hotkey", "快捷键", "选择不与其他 Windows 功能冲突的翻译触发方式",
        )
        tk.Label(
            shortcut_card, text="翻译触发方式", foreground=TEXT, background=WINDOW_BG,
            font=(FONT_TEXT, 9),
        ).grid(row=0, column=0, sticky="w", pady=9)
        hotkey_box = ttk.Combobox(
            shortcut_card,
            textvariable=hotkey_var,
            values=list(HOTKEY_LABELS.values()),
            state="readonly",
            style="Fluent.TCombobox",
        )
        hotkey_box.grid(row=0, column=1, sticky="ew", padx=(16, 0), pady=9)
        shortcut_card.columnconfigure(1, weight=1)
        tk.Label(
            shortcut_card,
            text=(
                "推荐“按住 Ctrl，双击 C”；与 Windows 复制操作一致，且不会触发双击 Ctrl 的鼠标定位。"
                "Fn 通常由键盘硬件处理，无法被 Windows 程序稳定监听。"
            ),
            foreground=MUTED, background=WINDOW_BG, font=(FONT_TEXT, 9),
            wraplength=510, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(15, 0))

        def select_page(key: str) -> None:
            if key not in pages:
                key = "qwen"
            for page in pages.values():
                page.pack_forget()
            pages[key].pack(fill="both", expand=True)
            for item_key, button in navigation_buttons.items():
                selected = item_key == key
                button.set_kind("selected" if selected else "subtle")

        self._settings_select_page = select_page
        navigation_items = [
            ("appearance", "\uE790", "外观"),
            ("qwen", "\uE753", "百炼 Qwen-MT"),
            ("research", "\uE8D2", "科研翻译"),
            ("glm", "\uE72C", "备用 GLM"),
            ("hotkey", "\uE765", "快捷键"),
        ]
        for key, glyph, label in navigation_items:
            button = FluentButton(
                navigation, glyph, lambda item_key=key: select_page(item_key),
                kind="subtle", width=40, height=40, icon=True, tooltip=label,
            )
            button.pack(padx=9, pady=3)
            navigation_buttons[key] = button
        select_page(initial_page)

        def save() -> None:
            for key, entry in entries.items():
                setattr(self.cfg, key, entry.get().strip())
            label_to_hotkey = {label: key for key, label in HOTKEY_LABELS.items()}
            label_to_theme = {label: key for key, label in THEME_LABELS.items()}
            self.cfg.hotkey = label_to_hotkey.get(hotkey_var.get(), DEFAULT_HOTKEY)
            self.cfg.theme = label_to_theme.get(theme_var.get(), "system")
            if not self.cfg.api_url or not self.cfg.model or not self.cfg.qwen_api_url:
                messagebox.showwarning(APP_NAME, "API 地址和模型不能为空。", parent=win)
                return
            self.cfg.save()
            status_text = f"设置已保存 · 选中文字后按 {hotkey_label(self.cfg.hotkey)}"
            close_settings()
            self._apply_resolved_theme(resolve_theme(self.cfg.theme))
            self.status.config(text=status_text)

        footer = tk.Frame(win, background=WINDOW_BG, padx=20)
        footer.pack(fill="x", pady=(0, 16))
        self._fluent_button(footer, "取消", close_settings, "secondary").pack(side="right")
        self._fluent_button(footer, "保存设置", save, "primary").pack(
            side="right", padx=(0, 8),
        )
        win.grab_set()

    def run(self) -> None:
        self.root.mainloop()


def run_self_test() -> bool:
    expected_input_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    return (
        ctypes.sizeof(INPUT) == expected_input_size
        and detect_translation_direction("Scientific paper") == ("English", "Chinese")
        and normalize_pdf_layout("NUMI\nSHEET") == "NUMISHEET"
    )


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(0 if run_self_test() else 1)
    executable_stem = Path(sys.executable).stem.lower()
    mutex_name = (
        f"{INSTANCE_MUTEX_NAME}-{executable_stem}"
        if "preview" in executable_stem else INSTANCE_MUTEX_NAME
    )
    instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(None, "划词翻译已经在运行。", APP_NAME, 0x40)
    else:
        QuickTranslator().run()
    if instance_mutex:
        ctypes.windll.kernel32.CloseHandle(instance_mutex)
