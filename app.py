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
from dataclasses import asdict, dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
import pystray


APP_NAME = "划词翻译"
VERSION = "0.6.0"
ACCENT = "#6C5CE7"
ACCENT_DARK = "#5144C8"
SURFACE = "#F5F4FA"
TEXT = "#242235"
MUTED = "#747187"
CONFIG_PATH = Path(os.getenv("APPDATA", Path.home())) / "QuickTranslator" / "config.json"
TM_PATH = CONFIG_PATH.with_name("translation_memory.json")
ERROR_ALREADY_EXISTS = 183
INSTANCE_MUTEX_NAME = "Local\\QuickTranslatorSingleInstance"

PROVIDER_PRESETS = {
    "智谱 GLM": {
        "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4.7-flash", "fallback_model": "glm-4-flash",
    },
    "DeepSeek": {
        "api_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat", "fallback_model": "",
    },
    "Kimi / Moonshot": {
        "api_url": "https://api.moonshot.cn/v1/chat/completions",
        "model": "moonshot-v1-8k", "fallback_model": "",
    },
    "硅基流动": {
        "api_url": "https://api.siliconflow.cn/v1/chat/completions",
        "model": "Qwen/Qwen3-8B", "fallback_model": "",
    },
    "OpenAI": {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-5-mini", "fallback_model": "",
    },
    "自定义兼容接口": {
        "api_url": "", "model": "", "fallback_model": "",
    },
}


@dataclass
class Config:
    qwen_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    qwen_api_key: str = ""
    accurate_model: str = "qwen-mt-plus"
    fast_model: str = "qwen-mt-turbo"
    translation_mode: str = "accurate"
    provider: str = "智谱 GLM"
    api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    api_key: str = ""
    model: str = "glm-4.7-flash"
    fallback_model: str = "glm-4-flash"
    target_language: str = "简体中文"
    research_domain: str = "自动识别科研领域"
    domain_prompt: str = "Academic research paper. Use formal scholarly language and field-standard terminology."
    glossary: str = ""
    hotkey: str = "双击 Ctrl"

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cls(**{k: raw[k] for k in cls.__annotations__ if k in raw and k != "hotkey"})
        except (OSError, ValueError, TypeError):
            return cls(api_key=os.getenv("TRANSLATOR_API_KEY", ""))

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


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
    payload_data = {
        "model": model or cfg.model,
        "temperature": 0.1,
        "stream": True,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
    }
    if cfg.provider == "智谱 GLM":
        payload_data["thinking"] = {"type": "disabled"}
    payload = json.dumps(payload_data, ensure_ascii=False).encode("utf-8")
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
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        # Tk applies alpha to every child as well; keep the reading surface opaque.
        self.root.attributes("-alpha", 1.0)
        self.root.geometry("480x260")
        self.root.minsize(380, 200)
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
        self._edge_resize = None
        self._cursor_widget = None
        self._cursor_original = ""
        self._quitting = False
        self._tray_image = self._make_tray_image()
        self._build_ui()
        self._bind_edge_resize()
        self._start_tray()
        self.root.after(100, self._poll_events)
        if self.cfg.qwen_api_key or self.cfg.api_key:
            self.root.after(500, self.hide)
        else:
            self.root.after(250, self._show_first_run_settings)
        threading.Thread(target=self._double_ctrl_loop, daemon=True).start()
        threading.Thread(target=self._double_shift_loop, daemon=True).start()

    def _build_ui(self) -> None:
        self.root.configure(background=SURFACE)
        self._window_icon = ImageTk.PhotoImage(self._tray_image)
        self.root.iconphoto(True, self._window_icon)
        header = tk.Frame(self.root, background=ACCENT, padx=18, pady=13)
        header.pack(fill="x")
        header.bind("<ButtonPress-1>", self._start_drag)
        header.bind("<B1-Motion>", self._drag_window)
        tk.Label(
            header, text="译", foreground="white", background=ACCENT,
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(side="left")
        titles = tk.Frame(header, background=ACCENT)
        titles.pack(side="left", padx=(10, 0))
        tk.Label(
            titles, text=APP_NAME, foreground="white", background=ACCENT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        self.status = tk.Label(
            titles, text=f"选中文字 · {self.cfg.hotkey}", foreground="#DED9FF",
            background=ACCENT, font=("Microsoft YaHei UI", 9),
        )
        self.status.pack(anchor="w")
        self._flat_button(header, "×", self.hide, ACCENT_DARK).pack(side="right", padx=(8, 0))
        self._flat_button(header, "设置", self.open_settings, ACCENT_DARK).pack(side="right")
        self.mode_button = self._flat_button(header, self._mode_label(), self.toggle_mode, "#8174EA")
        self.mode_button.pack(side="right", padx=(0, 8))
        self._bind_drag_area(header)

        content = tk.Frame(self.root, background="white", highlightthickness=0)
        content.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.scrollbar = tk.Scrollbar(content, width=9, relief="flat", borderwidth=0)
        self.output = tk.Text(
            content, wrap="char", padx=18, pady=16, font=("Microsoft YaHei UI", 11),
            relief="flat", borderwidth=0, background="white", foreground=TEXT,
            selectbackground="#DCEEFF", selectforeground=TEXT,
            inactiveselectbackground="#E7F3FF", selectborderwidth=0,
            spacing1=2, spacing3=2, undo=False,
            yscrollcommand=self._on_output_scroll,
        )
        self.output.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.config(command=self.output.yview)
        self.output.insert("1.0", "翻译结果会显示在这里。")

        bottom = tk.Frame(self.root, background=SURFACE, padx=14, pady=10)
        bottom.pack(fill="x")
        tk.Label(
            bottom, text="双击 Shift 找回窗口 · Esc 隐藏", foreground=MUTED,
            background=SURFACE, font=("Microsoft YaHei UI", 9),
        ).pack(side="left")
        self._flat_button(bottom, "复制译文", self.copy_result, ACCENT).pack(side="right")
        self._flat_button(bottom, "确认译文", self.confirm_translation, "#4D9B82").pack(side="right", padx=(0, 7))
        grip = tk.Label(
            bottom, text="◢", foreground="#9993B5", background=SURFACE,
            cursor="size_nw_se", font=("Segoe UI Symbol", 10), padx=5,
        )
        grip.pack(side="right", padx=(0, 8))
        grip.bind("<ButtonPress-1>", self._start_resize)
        grip.bind("<B1-Motion>", self._resize_window)

    def _on_output_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        overflowing = float(first) > 0.0 or float(last) < 0.999
        if overflowing and not self.scrollbar.winfo_ismapped():
            self.scrollbar.grid(row=0, column=1, sticky="ns")
        elif not overflowing and self.scrollbar.winfo_ismapped():
            self.scrollbar.grid_remove()

    def _flat_button(self, parent, text: str, command, color: str) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, foreground="white", background=color,
            activeforeground="white", activebackground=ACCENT_DARK, relief="flat",
            borderwidth=0, padx=13, pady=6, cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        )

    def _mode_label(self) -> str:
        return "精准 Plus" if self.cfg.translation_mode == "accurate" else "极速 Turbo"

    def toggle_mode(self) -> None:
        self.cfg.translation_mode = "fast" if self.cfg.translation_mode == "accurate" else "accurate"
        self.mode_button.config(text=self._mode_label())
        self.cfg.save()
        self.status.config(text=f"已切换为{self._mode_label()}模式")

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
            new_height = max(170, height + dy)
        if "w" in edge:
            new_width = max(380, width - dx)
            new_x = win_x + width - new_width
        if "n" in edge:
            new_height = max(170, height - dy)
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
        width = max(380, min(self.root.winfo_screenwidth() - 20, start_width + event.x_root - start_x))
        height = max(170, min(self.root.winfo_screenheight() - 80, start_height + event.y_root - start_y))
        self.root.geometry(f"{width}x{height}")

    def _make_tray_image(self) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((3, 3, 61, 61), radius=15, fill=ACCENT)
        draw.rounded_rectangle((14, 14, 34, 34), radius=5, fill="white")
        draw.rounded_rectangle((30, 30, 50, 50), radius=5, fill="#B8AEFF")
        draw.line((20, 24, 29, 24), fill=ACCENT, width=3)
        draw.line((24, 19, 24, 29), fill=ACCENT, width=3)
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

    def _double_ctrl_loop(self) -> None:
        user32 = ctypes.windll.user32
        was_down = False
        last_press = 0.0
        while True:
            down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
            if down and not was_down:
                now = time.monotonic()
                if 0.08 < now - last_press < 0.42:
                    self.events.put(("translate",))
                    last_press = 0.0
                else:
                    last_press = now
            was_down = down
            time.sleep(0.015)

    def _double_shift_loop(self) -> None:
        user32 = ctypes.windll.user32
        was_down = False
        last_press = 0.0
        while True:
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
                    self.status.config(text=f"翻译完成 · {self.cfg.hotkey} 再次翻译")
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
        if not self.cfg.qwen_api_key and not self.cfg.api_key:
            self.status.config(text="请先配置翻译服务和 API Key")
            self.show_window()
            self.open_settings()
            return
        now = time.monotonic()
        if self._capture_in_progress or now - self._last_capture_trigger < 0.9:
            return
        self._capture_in_progress = True
        self._last_capture_trigger = now
        self._key_release_deadline = now + 0.8
        self._capture_after_ctrl_release()

    def _capture_after_ctrl_release(self) -> None:
        ctrl_is_down = bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
        if ctrl_is_down and time.monotonic() < self._key_release_deadline:
            self.root.after(20, self._capture_after_ctrl_release)
            return
        if ctrl_is_down:
            self._capture_in_progress = False
            self._show_error("Ctrl 按键没有及时松开，已安全取消；未操作剪贴板。")
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
        self.status.config(text="正在翻译…（再次双击 Ctrl 可开始新任务）")
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
                self.events.put(("status", request_id, f"百炼暂不可用，正在切换{self.cfg.provider}：{exc}"))

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
        width = max(380, self.root.winfo_width())
        height = max(170, self.root.winfo_height())
        x = min(point.x + 18, self.root.winfo_screenwidth() - width - 12)
        y = min(point.y + 18, self.root.winfo_screenheight() - height - 60)
        self.root.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(800, lambda: self.root.attributes("-topmost", False))

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
            display_lines = max(1, int(self.output.count("1.0", "end-1c", "displaylines")[0]))
        except (TypeError, tk.TclError):
            display_lines = max(1, self.output.get("1.0", "end-1c").count("\n") + 1)
        screen_height = self.root.winfo_screenheight()
        target_height = min(int(screen_height * 0.68), max(170, 132 + display_lines * 24))
        width = max(380, self.root.winfo_width())
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
        width, x = max(380, self.root.winfo_width()), max(0, self.root.winfo_x())
        self.root.geometry(f"{width}x{next_height}+{x}+{max(0, next_y)}")
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
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _show_first_run_settings(self) -> None:
        self.show_window()
        self.status.config(text="首次使用 · 请先配置 API Key")
        self.open_settings(first_run=True)

    def exit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.tray_icon.stop()
        self.root.destroy()

    def open_settings(self, first_run: bool = False) -> None:
        win = tk.Toplevel(self.root)
        win.title("首次配置" if first_run else "设置")
        win.geometry("720x560")
        win.transient(self.root)
        win.grab_set()
        entries: dict[str, ttk.Entry] = {}
        if first_run:
            welcome = ttk.Frame(win, padding=(18, 14, 18, 2))
            welcome.pack(fill="x")
            ttk.Label(welcome, text="欢迎使用划词翻译", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
            ttk.Label(
                welcome,
                text="请先选择翻译服务并填写 API Key。密钥只保存在本机，不会进入安装包或 GitHub。",
                foreground="#555",
            ).pack(anchor="w", pady=(5, 0))
        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=16, pady=(16, 8))

        def add_tab(title: str, fields, note: str):
            frame = ttk.Frame(notebook, padding=18)
            notebook.add(frame, text=title)
            for row, (label, key, secret) in enumerate(fields):
                ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=9)
                entry = ttk.Entry(frame, show="•" if secret else "")
                entry.insert(0, getattr(self.cfg, key))
                entry.grid(row=row, column=1, sticky="ew", padx=(16, 0), pady=9)
                entries[key] = entry
            frame.columnconfigure(1, weight=1)
            ttk.Label(frame, text=note, foreground="#666", wraplength=610).grid(
                row=len(fields), column=0, columnspan=2, sticky="w", pady=(15, 0)
            )

        provider_frame = ttk.Frame(notebook, padding=18)
        notebook.add(provider_frame, text="通用 AI 服务")
        ttk.Label(provider_frame, text="服务商").grid(row=0, column=0, sticky="w", pady=9)
        provider_var = tk.StringVar(value=self.cfg.provider if self.cfg.provider in PROVIDER_PRESETS else "自定义兼容接口")
        provider_box = ttk.Combobox(
            provider_frame, textvariable=provider_var,
            values=list(PROVIDER_PRESETS), state="readonly",
        )
        provider_box.grid(row=0, column=1, sticky="ew", padx=(16, 0), pady=9)
        provider_fields = [
            ("API 地址", "api_url", False), ("API Key", "api_key", True),
            ("首选模型", "model", False), ("备用模型（可空）", "fallback_model", False),
        ]
        for row, (label, key, secret) in enumerate(provider_fields, start=1):
            ttk.Label(provider_frame, text=label).grid(row=row, column=0, sticky="w", pady=9)
            entry = ttk.Entry(provider_frame, show="•" if secret else "")
            entry.insert(0, getattr(self.cfg, key))
            entry.grid(row=row, column=1, sticky="ew", padx=(16, 0), pady=9)
            entries[key] = entry
        provider_frame.columnconfigure(1, weight=1)
        provider_note = ttk.Label(
            provider_frame,
            text="预设会填写推荐地址和模型，所有字段仍可手动修改。通用通道使用 OpenAI Chat Completions 协议。",
            foreground="#666", wraplength=630,
        )
        provider_note.grid(row=5, column=0, columnspan=2, sticky="w", pady=(15, 0))

        def apply_provider(event=None) -> None:
            preset = PROVIDER_PRESETS[provider_var.get()]
            for key in ("api_url", "model", "fallback_model"):
                entries[key].delete(0, "end")
                entries[key].insert(0, preset[key])

        provider_box.bind("<<ComboboxSelected>>", apply_provider)

        add_tab("专业 Qwen-MT", [
            ("API 地址", "qwen_api_url", False), ("API Key", "qwen_api_key", True),
            ("精准模型", "accurate_model", False), ("极速模型", "fast_model", False),
        ], "科研翻译首选。未填写百炼 Key 时，程序会使用“通用 AI 服务”中的接口。")
        add_tab("科研翻译", [
            ("科研领域", "research_domain", False), ("英文领域提示", "domain_prompt", False),
            ("术语表", "glossary", False), ("通用接口目标语言", "target_language", False),
        ], "术语表示例：cell culture=细胞培养; power=统计功效。Qwen-MT 会自动判断中译英或英译中。")

        def save() -> None:
            for key, entry in entries.items():
                setattr(self.cfg, key, entry.get().strip())
            self.cfg.provider = provider_var.get()
            if not self.cfg.qwen_api_key and not self.cfg.api_key:
                messagebox.showwarning(APP_NAME, "请至少填写一个 API Key。", parent=win)
                return
            if self.cfg.api_key and (not self.cfg.api_url or not self.cfg.model):
                messagebox.showwarning(APP_NAME, "通用接口的 API 地址和模型不能为空。", parent=win)
                return
            if self.cfg.qwen_api_key and not self.cfg.qwen_api_url:
                messagebox.showwarning(APP_NAME, "Qwen-MT API 地址不能为空。", parent=win)
                return
            self.cfg.save()
            self.status.config(text=f"设置已保存 · 选中文字后按 {self.cfg.hotkey}")
            win.destroy()

        actions = ttk.Frame(win, padding=(16, 5, 16, 14))
        actions.pack(fill="x")
        ttk.Label(actions, text="双击 Ctrl 翻译 · 双击 Shift 找回窗口", foreground="#666").pack(side="left")
        ttk.Button(actions, text="保存并开始使用", command=save).pack(side="right")

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
    instance_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, INSTANCE_MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        ctypes.windll.user32.MessageBoxW(None, "划词翻译已经在运行。", APP_NAME, 0x40)
    else:
        QuickTranslator().run()
    if instance_mutex:
        ctypes.windll.kernel32.CloseHandle(instance_mutex)
