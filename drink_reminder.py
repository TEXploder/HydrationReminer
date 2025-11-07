#!/usr/bin/env python3
"""
Hydration reminder overlay implemented in Python with Qt (PySide6).

The overlay can be positioned on any screen corner, contains
animated PNG/GIF support, exposes live customization via a
system-tray settings window, and stays top-most (for borderless
windowed scenarios) with optional random jitter per reminder.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import random
import shutil
import sys
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets  # LGPL-licensed Qt bindings


# Ensure Windows-only runtime.
if sys.platform != "win32":
    raise SystemExit("This hydration overlay currently supports Windows only.")


# ===== Storage + resource helpers ==========================================


APP_NAME = "HydrationReminder"


def get_resource_root() -> Path:
    """Return path that contains bundled resources (PyInstaller-safe)."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def determine_storage_root() -> Path:
    """Ensure preferred storage directory exists, fallback to HOME if needed."""
    preferred = Path(r"C:\TEX-Programme") / APP_NAME
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except OSError as exc:
        fallback = Path.home() / APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        print(f"[HydrationReminder] Warning: {exc}. Falling back to {fallback}", file=sys.stderr)
        return fallback


RESOURCE_ROOT = get_resource_root()
STORAGE_ROOT = determine_storage_root()
STORAGE_ASSETS_DIR = STORAGE_ROOT / "assets"
SETTINGS_FILE = STORAGE_ROOT / "settings.json"
DEFAULT_ASSETS_SOURCE = RESOURCE_ROOT / "assets"
DEFAULT_BOTTLE_GIF = RESOURCE_ROOT / "bottle.gif"
RUN_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def ensure_storage_tree() -> None:
    STORAGE_ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def sync_default_assets() -> None:
    """Copy bundled assets into storage dir if they do not exist yet."""
    ensure_storage_tree()
    if not DEFAULT_ASSETS_SOURCE.exists():
        return
    for src in DEFAULT_ASSETS_SOURCE.iterdir():
        if not src.is_file():
            continue
        dest = STORAGE_ASSETS_DIR / src.name
        if dest.exists():
            continue
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            print(f"[HydrationReminder] Could not copy asset {src} -> {dest}: {exc}", file=sys.stderr)


sync_default_assets()


# ===== Utility helpers ======================================================


def enable_high_dpi_awareness() -> None:
    """Enable per-monitor DPI awareness for crisp rendering on modern Windows."""
    awareness_context = getattr(ctypes.windll.user32, "SetProcessDpiAwarenessContext", None)
    if awareness_context:
        awareness_context(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        return

    shcore = getattr(ctypes.windll, "shcore", None)
    if shcore:
        try:
            shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
        except OSError:
            pass

    ctypes.windll.user32.SetProcessDPIAware()  # Fallback for very old Windows builds.


def format_interval(milliseconds: int) -> str:
    """Format a large interval (e.g. 45 minutes) into a readable string."""
    minutes_total = max(0, milliseconds // 60000)
    hours, minutes = divmod(minutes_total, 60)

    parts: List[str] = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    if not parts:
        return "less than a minute"
    return " ".join(parts)


def format_short_duration(milliseconds: int) -> str:
    """Return a short h/m/s text for a countdown label."""
    if milliseconds <= 0:
        return "now"

    seconds_total = milliseconds // 1000
    hours, remainder = divmod(seconds_total, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: List[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or hours:
        parts.append(f"{minutes}m")
    if hours == 0:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def color_to_dict(color: QtGui.QColor) -> Dict[str, int]:
    return {
        "r": color.red(),
        "g": color.green(),
        "b": color.blue(),
        "a": color.alpha(),
    }


def color_from_dict(data: object, fallback: QtGui.QColor) -> QtGui.QColor:
    if not isinstance(data, dict):
        return QtGui.QColor(fallback)
    try:
        r = int(data.get("r", fallback.red()))
        g = int(data.get("g", fallback.green()))
        b = int(data.get("b", fallback.blue()))
        a = int(data.get("a", fallback.alpha()))
    except (TypeError, ValueError):
        return QtGui.QColor(fallback)
    color = QtGui.QColor(r, g, b, a)
    if not color.isValid():
        return QtGui.QColor(fallback)
    return color


def create_fallback_pixmap(size: int = 120) -> QtGui.QPixmap:
    """Draw a simple water droplet when no assets are available."""
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)

    gradient = QtGui.QLinearGradient(0, 0, 0, size)
    gradient.setColorAt(0.0, QtGui.QColor(54, 178, 255))
    gradient.setColorAt(1.0, QtGui.QColor(28, 120, 240))

    path = QtGui.QPainterPath()
    path.moveTo(size / 2, size * 0.05)
    path.cubicTo(size * 0.1, size * 0.35, size * 0.2, size * 0.75, size / 2, size * 0.95)
    path.cubicTo(size * 0.8, size * 0.75, size * 0.9, size * 0.35, size / 2, size * 0.05)

    painter.fillPath(path, gradient)
    pen = QtGui.QPen(QtGui.QColor(20, 70, 160), 2)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.drawPath(path)
    painter.end()

    return pixmap


# ===== Configuration ========================================================


@dataclass
class AppConfig:
    reminder_interval_ms: int = 45 * 60 * 1000  # 45 minutes
    auto_hide_ms: int = 15 * 1000               # 15 seconds
    animation_interval_ms: int = 200            # 0.2 seconds
    show_preview_on_launch: bool = True
    preview_delay_ms: int = 2000
    asset_directory: Path = field(default_factory=lambda: STORAGE_ASSETS_DIR)

    position: str = "bottom_right"  # bottom_right, bottom_left, top_right, top_left
    margin_x: int = 16
    margin_y: int = 16
    random_offset_ms: int = 0

    overlay_width: int = 360
    overlay_height: int = 180
    overlay_opacity: float = 1.0  # 0.0 - 1.0
    background_radius: int = 24

    gradient_top: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(28, 116, 235, 235))
    gradient_bottom: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(80, 170, 255, 235))
    border_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(255, 255, 255, 200))
    shadow_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(0, 0, 0, 90))

    title_text: str = "Hydration break"
    title_font_size: int = 22
    title_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(255, 255, 255))

    message_template: str = "It's time to take a sip of water.\nEvery {interval}"
    message_font_size: int = 14
    text_opacity: float = 0.95
    text_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(235, 238, 245))

    countdown_enabled: bool = True
    countdown_template: str = "Next reminder in {remaining}."
    countdown_font_size: int = 12
    countdown_color: QtGui.QColor = field(default_factory=lambda: QtGui.QColor(255, 255, 255))

    animation_enabled: bool = True
    entry_animation: str = "fade"  # fade, slide, pop
    monitor_id: str = "auto"
    autostart_enabled: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "reminder_interval_ms": self.reminder_interval_ms,
            "auto_hide_ms": self.auto_hide_ms,
            "animation_interval_ms": self.animation_interval_ms,
            "show_preview_on_launch": self.show_preview_on_launch,
            "preview_delay_ms": self.preview_delay_ms,
            "asset_directory": str(self.asset_directory),
            "position": self.position,
            "margin_x": self.margin_x,
            "margin_y": self.margin_y,
            "random_offset_ms": self.random_offset_ms,
            "overlay_width": self.overlay_width,
            "overlay_height": self.overlay_height,
            "overlay_opacity": self.overlay_opacity,
            "background_radius": self.background_radius,
            "gradient_top": color_to_dict(self.gradient_top),
            "gradient_bottom": color_to_dict(self.gradient_bottom),
            "border_color": color_to_dict(self.border_color),
            "shadow_color": color_to_dict(self.shadow_color),
            "title_text": self.title_text,
            "title_font_size": self.title_font_size,
            "title_color": color_to_dict(self.title_color),
            "message_template": self.message_template,
            "message_font_size": self.message_font_size,
            "text_opacity": self.text_opacity,
            "text_color": color_to_dict(self.text_color),
            "countdown_enabled": self.countdown_enabled,
            "countdown_template": self.countdown_template,
            "countdown_font_size": self.countdown_font_size,
            "countdown_color": color_to_dict(self.countdown_color),
            "animation_enabled": self.animation_enabled,
            "entry_animation": self.entry_animation,
            "monitor_id": self.monitor_id,
            "autostart_enabled": self.autostart_enabled,
        }

    @classmethod
    def from_dict(cls, data: object) -> "AppConfig":
        config = cls()
        if not isinstance(data, dict):
            return config

        def get_int(key: str, default: int) -> int:
            try:
                return int(data.get(key, default))
            except (TypeError, ValueError):
                return default

        def get_float(key: str, default: float) -> float:
            try:
                return float(data.get(key, default))
            except (TypeError, ValueError):
                return default

        config.reminder_interval_ms = get_int("reminder_interval_ms", config.reminder_interval_ms)
        config.auto_hide_ms = get_int("auto_hide_ms", config.auto_hide_ms)
        config.animation_interval_ms = get_int("animation_interval_ms", config.animation_interval_ms)
        config.show_preview_on_launch = bool(data.get("show_preview_on_launch", config.show_preview_on_launch))
        config.preview_delay_ms = get_int("preview_delay_ms", config.preview_delay_ms)
        config.position = str(data.get("position", config.position))
        config.margin_x = get_int("margin_x", config.margin_x)
        config.margin_y = get_int("margin_y", config.margin_y)
        config.random_offset_ms = get_int("random_offset_ms", config.random_offset_ms)
        config.overlay_width = get_int("overlay_width", config.overlay_width)
        config.overlay_height = get_int("overlay_height", config.overlay_height)
        config.overlay_opacity = get_float("overlay_opacity", config.overlay_opacity)
        config.background_radius = get_int("background_radius", config.background_radius)
        config.gradient_top = color_from_dict(data.get("gradient_top"), config.gradient_top)
        config.gradient_bottom = color_from_dict(data.get("gradient_bottom"), config.gradient_bottom)
        config.border_color = color_from_dict(data.get("border_color"), config.border_color)
        config.shadow_color = color_from_dict(data.get("shadow_color"), config.shadow_color)
        config.title_text = str(data.get("title_text", config.title_text))
        config.title_font_size = get_int("title_font_size", config.title_font_size)
        config.title_color = color_from_dict(data.get("title_color"), config.title_color)
        config.message_template = str(data.get("message_template", config.message_template))
        config.message_font_size = get_int("message_font_size", config.message_font_size)
        config.text_opacity = get_float("text_opacity", config.text_opacity)
        config.text_color = color_from_dict(data.get("text_color"), config.text_color)
        config.countdown_enabled = bool(data.get("countdown_enabled", config.countdown_enabled))
        config.countdown_template = str(data.get("countdown_template", config.countdown_template))
        config.countdown_font_size = get_int("countdown_font_size", config.countdown_font_size)
        config.countdown_color = color_from_dict(data.get("countdown_color"), config.countdown_color)
        config.animation_enabled = bool(data.get("animation_enabled", config.animation_enabled))
        config.entry_animation = str(data.get("entry_animation", config.entry_animation))
        config.monitor_id = str(data.get("monitor_id", config.monitor_id))
        config.autostart_enabled = bool(data.get("autostart_enabled", config.autostart_enabled))
        config.asset_directory = STORAGE_ASSETS_DIR
        return config


# ===== Settings + autostart helpers ========================================


def get_launch_command() -> str:
    executable = Path(sys.argv[0]).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    python = Path(sys.executable).resolve()
    return f'"{python}" "{executable}"'


def is_autostart_enabled() -> bool:
    command = get_launch_command()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return value == command
    except FileNotFoundError:
        return False
    except OSError:
        return False


def update_autostart(enabled: bool) -> None:
    command = get_launch_command()
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH, 0, winreg.KEY_SET_VALUE)
    except FileNotFoundError:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_PATH)

    with key:
        try:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"[HydrationReminder] Failed to update autostart registry: {exc}", file=sys.stderr)


def load_config_from_disk() -> AppConfig:
    ensure_storage_tree()
    if SETTINGS_FILE.exists():
        try:
            raw = SETTINGS_FILE.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            data = {}
    else:
        data = {}

    config = AppConfig.from_dict(data)
    config.asset_directory = STORAGE_ASSETS_DIR
    config.autostart_enabled = is_autostart_enabled()
    return config


def save_config(config: AppConfig) -> None:
    ensure_storage_tree()
    try:
        SETTINGS_FILE.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[HydrationReminder] Failed to save settings: {exc}", file=sys.stderr)


# ===== Overlay content widget ==============================================


class OverlayContent(QtWidgets.QWidget):
    """Reusable UI block shared between the overlay and live preview."""

    def __init__(self, config: AppConfig, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground)

        self.animation_label = QtWidgets.QLabel()
        self.animation_label.setScaledContents(True)
        self.animation_label.setMinimumSize(64, 64)
        self.animation_label.setMaximumSize(220, 220)
        self.animation_label.setAlignment(QtCore.Qt.AlignCenter)

        self.title_label = QtWidgets.QLabel()
        self.title_label.setWordWrap(False)
        self.title_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)

        self.message_label = QtWidgets.QLabel()
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.message_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)

        self.countdown_label = QtWidgets.QLabel()
        self.countdown_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        self.countdown_label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)

        text_layout = QtWidgets.QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(8)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.message_label)
        text_layout.addWidget(self.countdown_label)
        text_layout.addStretch()

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        main_layout.addWidget(self.animation_label)
        main_layout.addLayout(text_layout)

        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        self.setGraphicsEffect(shadow)

        self.current_movie: Optional[QtGui.QMovie] = None
        self.apply_config(config)

    def apply_config(self, config: AppConfig, interval_text: Optional[str] = None) -> None:
        self.config = config

        padding = 24 * 2
        spacing = 20
        available_height = max(80, config.overlay_height - padding)
        image_size = int(max(64, min(220, available_height)))
        self.animation_label.setFixedSize(image_size, image_size)

        title_font = QtGui.QFont("Segoe UI", config.title_font_size, QtGui.QFont.Bold)
        message_font = QtGui.QFont("Segoe UI", config.message_font_size)
        countdown_font = QtGui.QFont("Segoe UI", config.countdown_font_size, QtGui.QFont.Medium)

        self.title_label.setFont(title_font)
        self.message_label.setFont(message_font)
        self.countdown_label.setFont(countdown_font)

        title_color = QtGui.QColor(config.title_color)
        self.title_label.setStyleSheet(f"color: rgba({title_color.red()}, {title_color.green()}, {title_color.blue()}, {title_color.alpha()});")

        text_color = QtGui.QColor(config.text_color)
        text_color.setAlpha(int(config.text_opacity * 255))
        self.message_label.setStyleSheet(
            f"color: rgba({text_color.red()}, {text_color.green()}, {text_color.blue()}, {text_color.alpha()});"
        )

        countdown_color = QtGui.QColor(config.countdown_color)
        countdown_color.setAlpha(int(config.text_opacity * 255))
        self.countdown_label.setStyleSheet(
            f"color: rgba({countdown_color.red()}, {countdown_color.green()}, {countdown_color.blue()}, {countdown_color.alpha()});"
        )

        self.countdown_label.setVisible(config.countdown_enabled)
        self.update()

        title_metrics = QtGui.QFontMetrics(title_font)
        message_metrics = QtGui.QFontMetrics(message_font)
        interval_preview = interval_text or format_interval(config.reminder_interval_ms)
        message_preview = config.message_template.format(interval=interval_preview)
        max_message_line = max((message_metrics.horizontalAdvance(line) for line in message_preview.splitlines()), default=0)
        text_block_width = max(title_metrics.horizontalAdvance(config.title_text), max_message_line, 160)

        total_width = padding + image_size + spacing + text_block_width
        total_width = math.ceil(max(config.overlay_width, total_width))

        self.setFixedSize(total_width, config.overlay_height)
        self.update_texts(interval_preview)

    def update_texts(self, interval_text: str) -> None:
        text = self.config.message_template.format(interval=interval_text)
        self.message_label.setText(text)
        self.title_label.setText(self.config.title_text)

    def set_countdown_text(self, countdown_text: str) -> None:
        if not self.config.countdown_enabled:
            self.countdown_label.clear()
        else:
            self.countdown_label.setText(countdown_text)

    def set_static_pixmap(self, pixmap: QtGui.QPixmap) -> None:
        self.animation_label.setMovie(None)
        self.current_movie = None
        self.animation_label.setPixmap(pixmap)

    def set_movie(self, movie: Optional[QtGui.QMovie]) -> None:
        self.current_movie = movie
        if movie:
            self.animation_label.setMovie(movie)
            movie.start()
        else:
            self.animation_label.setMovie(None)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # type: ignore[override]
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        rect = QtCore.QRectF(self.rect())
        rect.adjust(2, 2, -2, -4)
        radius = float(self.config.background_radius)

        shadow_rect = rect.adjusted(4, 10, 6, 12)
        shadow_path = QtGui.QPainterPath()
        shadow_path.addRoundedRect(shadow_rect, radius, radius)
        painter.fillPath(shadow_path, self.config.shadow_color)

        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, self.config.gradient_top)
        gradient.setColorAt(1.0, self.config.gradient_bottom)
        painter.fillPath(path, gradient)

        pen = QtGui.QPen(self.config.border_color, 1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPath(path)


# ===== Asset loading ========================================================


def load_animation_assets(config: AppConfig) -> Tuple[List[QtGui.QPixmap], Optional[QtGui.QMovie]]:
    """Load PNG frame sequence or GIF/WebP fallback."""
    frames: List[QtGui.QPixmap] = []
    fallback_movie: Optional[QtGui.QMovie] = None

    asset_dir = config.asset_directory
    if asset_dir.exists():
        for index in range(1, 25):
            frame_path = asset_dir / f"frame{index}.png"
            if not frame_path.exists():
                if index == 1:
                    continue
                break
            pixmap = QtGui.QPixmap(str(frame_path))
            if not pixmap.isNull():
                frames.append(pixmap)

        if not frames:
            for candidate in ("animation.gif", "animation.webp", "animation.apng"):
                candidate_path = asset_dir / candidate
                if candidate_path.exists():
                    movie = QtGui.QMovie(str(candidate_path))
                    if movie.isValid():
                        fallback_movie = movie
                        break

    if not frames and fallback_movie is None:
        alt_gif = DEFAULT_BOTTLE_GIF
        if alt_gif.exists():
            movie = QtGui.QMovie(str(alt_gif))
            if movie.isValid():
                fallback_movie = movie

    return frames, fallback_movie


# ===== Overlay implementation ==============================================


class ReminderOverlay(QtWidgets.QWidget):
    countdownUpdated = QtCore.Signal(str)
    """Top-most reminder overlay with configurable styling."""

    def __init__(self, config: AppConfig, *, preview: bool = False,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        flags = QtCore.Qt.Widget if preview else (
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
            | QtCore.Qt.NoDropShadowWindowHint
        )
        super().__init__(parent, flags)

        self.preview = preview
        self.config = config
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        if not preview:
            self.setAttribute(QtCore.Qt.WA_NoSystemBackground)
            self.setWindowFlag(QtCore.Qt.WindowDoesNotAcceptFocus)

        self.content = OverlayContent(config, self)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.content)

        self.frame_pixmaps: List[QtGui.QPixmap] = []
        self.fallback_movie: Optional[QtGui.QMovie] = None
        self.fallback_pixmap = create_fallback_pixmap()
        self.animation_index = 0
        self.loaded_asset_dir: Optional[Path] = None

        self.animation_timer = QtCore.QTimer(self)
        self.animation_timer.timeout.connect(self.advance_animation)

        self.active_animation: Optional[QtCore.QAbstractAnimation] = None

        self.auto_hide_timer: Optional[QtCore.QTimer] = None
        self.reminder_timer: Optional[QtCore.QTimer] = None
        self.countdown_timer: Optional[QtCore.QTimer] = None
        self.next_reminder = QtCore.QDateTime.currentDateTime().addMSecs(config.reminder_interval_ms)

        if not preview:
            self.auto_hide_timer = QtCore.QTimer(self)
            self.auto_hide_timer.setSingleShot(True)
            self.auto_hide_timer.timeout.connect(self.hide_overlay)

            self.reminder_timer = QtCore.QTimer(self)
            self.reminder_timer.setSingleShot(True)
            self.reminder_timer.timeout.connect(self.trigger_reminder)

            self.countdown_timer = QtCore.QTimer(self)
            self.countdown_timer.setInterval(1000)
            self.countdown_timer.timeout.connect(self.update_countdown)
            self.countdown_timer.start()
        else:
            self.setCursor(QtCore.Qt.ArrowCursor)

        self.load_assets()
        self.apply_config(config)

        if preview:
            self.animation_timer.start(self.config.animation_interval_ms)
            self.content.set_countdown_text("Preview")
            self.show()
        else:
            self.hide()

    def load_assets(self) -> None:
        frames, movie = load_animation_assets(self.config)
        self.frame_pixmaps = frames
        self.fallback_movie = movie
        self.loaded_asset_dir = self.config.asset_directory

        if self.frame_pixmaps:
            self.animation_index = 0
            self.content.set_static_pixmap(self.frame_pixmaps[0])
        elif self.fallback_movie:
            self.content.set_movie(self.fallback_movie)
        else:
            self.content.set_static_pixmap(self.fallback_pixmap)

    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        interval_text = format_interval(config.reminder_interval_ms)

        self.setWindowOpacity(config.overlay_opacity)
        self.content.apply_config(config, interval_text)
        self.resize(self.content.size())

        if self.loaded_asset_dir != self.config.asset_directory:
            self.load_assets()

        if not self.preview:
            self.position_overlay()

        self.update_countdown()

        self.animation_timer.setInterval(max(10, config.animation_interval_ms))
        if config.animation_enabled and (self.frame_pixmaps or self.fallback_movie):
            if not self.animation_timer.isActive():
                self.animation_timer.start()
        else:
            self.animation_timer.stop()

    def schedule_next_reminder(self) -> None:
        if self.preview or self.reminder_timer is None:
            return

        interval_ms = self.config.reminder_interval_ms
        if self.config.random_offset_ms > 0:
            interval_ms += random.randint(0, self.config.random_offset_ms)
        interval_ms = max(1000, interval_ms)

        self.next_reminder = QtCore.QDateTime.currentDateTime().addMSecs(interval_ms)
        self.reminder_timer.start(interval_ms)
        self.update_countdown()

    def trigger_reminder(self) -> None:
        if self.preview:
            return
        self.schedule_next_reminder()
        self.show_overlay()

    def reset_reminder_timer(self) -> None:
        if self.preview or self.reminder_timer is None:
            return
        self.reminder_timer.stop()
        self.schedule_next_reminder()

    def show_overlay(self) -> None:
        self.apply_config(self.config)

        if not self.preview:
            self.position_overlay()
            self.apply_topmost()

        if self.config.animation_enabled and self.frame_pixmaps:
            self.animation_index = 0
            self.content.set_static_pixmap(self.frame_pixmaps[self.animation_index])
            if not self.animation_timer.isActive():
                self.animation_timer.start(self.config.animation_interval_ms)
        elif self.fallback_movie and self.config.animation_enabled:
            self.content.set_movie(self.fallback_movie)
        else:
            self.content.set_static_pixmap(self.fallback_pixmap)

        self.update_countdown()

        if not self.preview and self.auto_hide_timer:
            self.auto_hide_timer.start(self.config.auto_hide_ms)

        if self.preview:
            self.setFixedSize(self.config.overlay_width, self.config.overlay_height)
        self.show()
        self.raise_()
        if not self.preview:
            self.run_entry_animation()

    def hide_overlay(self) -> None:
        if self.preview:
            self.hide()
            return
        if self.auto_hide_timer:
            self.auto_hide_timer.stop()
        self.animation_timer.stop()
        if self.fallback_movie:
            self.fallback_movie.stop()
        if self.active_animation:
            self.active_animation.stop()
            self.active_animation.deleteLater()
            self.active_animation = None

        def finalize() -> None:
            self.hide()

        if self.run_exit_animation(finalize):
            return

        self.hide()

    def advance_animation(self) -> None:
        if not self.config.animation_enabled:
            return

        if self.frame_pixmaps:
            self.animation_index = (self.animation_index + 1) % len(self.frame_pixmaps)
            self.content.set_static_pixmap(self.frame_pixmaps[self.animation_index])
        elif self.fallback_movie:
            if self.content.current_movie is None:
                self.content.set_movie(self.fallback_movie)
        else:
            self.content.set_static_pixmap(self.fallback_pixmap)

    def _target_screen(self) -> QtGui.QScreen:
        if self.preview:
            host_screen = self.parentWidget().screen() if self.parentWidget() else None
            if host_screen:
                return host_screen
        if self.config.monitor_id != "auto":
            for screen in QtWidgets.QApplication.screens():
                if screen.name() == self.config.monitor_id:
                    return screen
        screen = QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
        if screen:
            return screen
        return QtWidgets.QApplication.primaryScreen()

    def position_overlay(self) -> None:
        if self.preview:
            return

        screen = self._target_screen()
        geo = screen.availableGeometry()

        width = self.width()
        height = self.height()
        margin_x = max(0, self.config.margin_x)
        margin_y = max(0, self.config.margin_y)

        if "right" in self.config.position:
            x = geo.x() + geo.width() - width - margin_x
        else:
            x = geo.x() + margin_x

        if "bottom" in self.config.position:
            y = geo.y() + geo.height() - height - margin_y
        else:
            y = geo.y() + margin_y

        x = max(geo.x(), min(x, geo.x() + geo.width() - width))
        y = max(geo.y(), min(y, geo.y() + geo.height() - height))

        self.move(x, y)

    def apply_topmost(self) -> None:
        if self.preview:
            return
        hwnd = int(self.winId())
        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        ctypes.windll.user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def update_countdown(self) -> None:
        if self.preview:
            text = "Next reminder in preview"
            friendly = "preview"
        else:
            remaining_ms = max(0, QtCore.QDateTime.currentDateTime().msecsTo(self.next_reminder))
            remaining_text = format_short_duration(remaining_ms)
            text = self.config.countdown_template.format(remaining=remaining_text)
            friendly = remaining_text
        self.content.set_countdown_text(text)
        if not self.preview:
            self.countdownUpdated.emit(friendly)

    def _start_animation(self, animation: QtCore.QAbstractAnimation, finalize: Optional[Callable[[], None]] = None) -> None:
        if self.active_animation:
            self.active_animation.stop()
            self.active_animation.deleteLater()
        self.active_animation = animation

        def cleanup() -> None:
            if finalize:
                finalize()
            animation.deleteLater()
            if self.active_animation is animation:
                self.active_animation = None

        animation.finished.connect(cleanup)
        animation.start()

    def run_entry_animation(self) -> None:
        if self.preview:
            return

        style = self.config.entry_animation
        end_rect = self.geometry()
        target_opacity = self.config.overlay_opacity

        if style == "fade":
            self.setWindowOpacity(0.0)
            animation = QtCore.QPropertyAnimation(self, b"windowOpacity")
            animation.setStartValue(0.0)
            animation.setEndValue(target_opacity)
            animation.setDuration(280)
        elif style == "slide":
            start_rect = QtCore.QRect(end_rect)
            start_rect.moveTop(start_rect.top() + 60)
            self.setGeometry(start_rect)
            animation = QtCore.QPropertyAnimation(self, b"geometry")
            animation.setStartValue(start_rect)
            animation.setEndValue(end_rect)
            animation.setDuration(320)
        elif style == "pop":
            start_rect = QtCore.QRect(end_rect)
            shrink_w = max(40, int(end_rect.width() * 0.9))
            shrink_h = max(40, int(end_rect.height() * 0.9))
            start_rect.setWidth(shrink_w)
            start_rect.setHeight(shrink_h)
            start_rect.moveCenter(end_rect.center())
            self.setGeometry(start_rect)
            animation = QtCore.QPropertyAnimation(self, b"geometry")
            animation.setStartValue(start_rect)
            animation.setEndValue(end_rect)
            animation.setDuration(250)
        else:
            self.setWindowOpacity(target_opacity)
            return

        animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)

        def finalize() -> None:
            self.setWindowOpacity(target_opacity)
            self.setGeometry(end_rect)

        self._start_animation(animation, finalize)

    def run_exit_animation(self, on_finished: Callable[[], None]) -> bool:
        if self.preview:
            return False

        style = self.config.entry_animation
        start_rect = self.geometry()

        if style == "fade":
            animation = QtCore.QPropertyAnimation(self, b"windowOpacity")
            animation.setStartValue(self.windowOpacity())
            animation.setEndValue(0.0)
            animation.setDuration(200)
            animation.setEasingCurve(QtCore.QEasingCurve.Type.InQuad)

            def finalize() -> None:
                self.setWindowOpacity(self.config.overlay_opacity)
                on_finished()

            self._start_animation(animation, finalize)
            return True

        if style == "slide":
            end_rect = QtCore.QRect(start_rect)
            end_rect.moveTop(end_rect.top() + 80)
            animation = QtCore.QPropertyAnimation(self, b"geometry")
            animation.setStartValue(start_rect)
            animation.setEndValue(end_rect)
            animation.setDuration(240)
            animation.setEasingCurve(QtCore.QEasingCurve.Type.InCubic)

            def finalize() -> None:
                self.setGeometry(start_rect)
                on_finished()

            self._start_animation(animation, finalize)
            return True

        if style == "pop":
            end_rect = QtCore.QRect(start_rect)
            shrink_w = max(20, int(start_rect.width() * 0.9))
            shrink_h = max(20, int(start_rect.height() * 0.9))
            end_rect.setWidth(shrink_w)
            end_rect.setHeight(shrink_h)
            end_rect.moveCenter(start_rect.center())
            animation = QtCore.QPropertyAnimation(self, b"geometry")
            animation.setStartValue(start_rect)
            animation.setEndValue(end_rect)
            animation.setDuration(200)
            animation.setEasingCurve(QtCore.QEasingCurve.Type.InBack)

            def finalize() -> None:
                self.setGeometry(start_rect)
                on_finished()

            self._start_animation(animation, finalize)
            return True

        return False

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:  # type: ignore[override]
        if self.preview:
            event.ignore()
            return
        self.hide_overlay()
        self.reset_reminder_timer()
        event.accept()


class OverlayPreview(ReminderOverlay):
    """Embedded preview widget used inside the settings dialog."""

    def __init__(self, config: AppConfig, parent: QtWidgets.QWidget) -> None:
        super().__init__(config, preview=True, parent=parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.show_overlay()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # type: ignore[override]
        self.content.setFixedSize(event.size())
        super().resizeEvent(event)


# ===== Settings window ======================================================


class ColorButton(QtWidgets.QPushButton):
    """Small helper button that displays and picks a color."""

    colorChanged = QtCore.Signal(QtGui.QColor)

    def __init__(self, color: QtGui.QColor, text: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(text, parent)
        self._color = QtGui.QColor(color)
        self.clicked.connect(self.choose_color)
        self.update_style()

    def color(self) -> QtGui.QColor:
        return QtGui.QColor(self._color)

    def setColor(self, color: QtGui.QColor) -> None:
        self._color = QtGui.QColor(color)
        self.update_style()
        self.colorChanged.emit(self._color)

    def choose_color(self) -> None:
        color = QtWidgets.QColorDialog.getColor(self._color, self.window(), "Choose color")
        if color.isValid():
            self.setColor(color)

    def update_style(self) -> None:
        self.setStyleSheet(
            f"""
            ColorButton {{
                background-color: rgba({self._color.red()}, {self._color.green()}, {self._color.blue()}, {self._color.alpha()});
                border: 1px solid #888;
                border-radius: 4px;
                padding: 4px 8px;
            }}
            """
        )


class WheelBlocker(QtCore.QObject):
    """Prevents wheel events from changing widget values."""

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:  # type: ignore[override]
        if event.type() == QtCore.QEvent.Type.Wheel and isinstance(obj, QtWidgets.QWidget):
            event.ignore()
            return True
        return super().eventFilter(obj, event)


_wheel_blocker = WheelBlocker()


def disable_wheel_scrolling(widget: QtWidgets.QWidget) -> None:
    widget.installEventFilter(_wheel_blocker)


class SettingsWindow(QtWidgets.QDialog):
    """System-tray settings dialog with live preview."""

    def __init__(self, config: AppConfig, overlay: ReminderOverlay,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hydration Reminder – Einstellungen")
        self.setMinimumWidth(580)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #121826;
                color: #F2F4FF;
            }
            QLabel {
                color: #F2F4FF;
            }
            QGroupBox {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                margin-top: 16px;
                font-weight: 600;
                padding: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 6px;
                color: #96B1FF;
            }
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #1C2235;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 4px 6px;
                color: #F2F4FF;
            }
            QPlainTextEdit {
                min-height: 64px;
            }
            QPushButton, QCheckBox {
                color: #F2F4FF;
            }
            QPushButton#primaryButton {
                background-color: #516BFF;
                border-radius: 18px;
                padding: 10px 18px;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover {
                background-color: #6D83FF;
            }
            """
        )
        self.setWindowModality(QtCore.Qt.NonModal)
        self.overlay = overlay
        self.config = config

        self.preview = OverlayPreview(config, self)

        self.position_combo = QtWidgets.QComboBox()
        self.position_combo.addItem("Unten rechts", "bottom_right")
        self.position_combo.addItem("Unten links", "bottom_left")
        self.position_combo.addItem("Oben rechts", "top_right")
        self.position_combo.addItem("Oben links", "top_left")
        idx = self.position_combo.findData(config.position)
        self.position_combo.setCurrentIndex(max(0, idx))

        self.random_spin = QtWidgets.QSpinBox()
        self.random_spin.setRange(0, 3600)
        self.random_spin.setSuffix(" s")
        self.random_spin.setValue(config.random_offset_ms // 1000)

        self.interval_spin = QtWidgets.QDoubleSpinBox()
        self.interval_spin.setRange(1.0, 240.0)
        self.interval_spin.setSuffix(" min")
        self.interval_spin.setValue(config.reminder_interval_ms / 60000.0)

        self.autohide_spin = QtWidgets.QDoubleSpinBox()
        self.autohide_spin.setRange(1.0, 120.0)
        self.autohide_spin.setSuffix(" s")
        self.autohide_spin.setValue(config.auto_hide_ms / 1000.0)

        self.animation_interval_spin = QtWidgets.QDoubleSpinBox()
        self.animation_interval_spin.setRange(0.05, 5.0)
        self.animation_interval_spin.setDecimals(2)
        self.animation_interval_spin.setSuffix(" s")
        self.animation_interval_spin.setValue(config.animation_interval_ms / 1000.0)

        self.animation_enabled_check = QtWidgets.QCheckBox("Animation aktivieren")
        self.animation_enabled_check.setChecked(config.animation_enabled)

        self.margin_x_spin = QtWidgets.QSpinBox()
        self.margin_x_spin.setRange(0, 400)
        self.margin_x_spin.setValue(config.margin_x)

        self.margin_y_spin = QtWidgets.QSpinBox()
        self.margin_y_spin.setRange(0, 400)
        self.margin_y_spin.setValue(config.margin_y)

        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(180, 600)
        self.width_spin.setValue(config.overlay_width)

        self.height_spin = QtWidgets.QSpinBox()
        self.height_spin.setRange(120, 400)
        self.height_spin.setValue(config.overlay_height)

        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.opacity_slider.setRange(10, 100)
        self.opacity_slider.setValue(int(config.overlay_opacity * 100))

        self.text_opacity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.text_opacity_slider.setRange(10, 100)
        self.text_opacity_slider.setValue(int(config.text_opacity * 100))

        self.title_edit = QtWidgets.QLineEdit(config.title_text)
        self.message_edit = QtWidgets.QPlainTextEdit(config.message_template)

        self.countdown_check = QtWidgets.QCheckBox("Countdown anzeigen")
        self.countdown_check.setChecked(config.countdown_enabled)

        self.countdown_template_edit = QtWidgets.QLineEdit(config.countdown_template)

        self.autostart_check = QtWidgets.QCheckBox("Beim Windows-Start automatisch starten")
        self.autostart_check.setChecked(config.autostart_enabled)

        self.monitor_combo = QtWidgets.QComboBox()
        self._populate_monitors()

        self.entry_animation_combo = QtWidgets.QComboBox()
        self.entry_animation_combo.addItem("Weiches Einblenden", "fade")
        self.entry_animation_combo.addItem("Gleiten von unten", "slide")
        self.entry_animation_combo.addItem("Pop-in", "pop")
        idx_anim = self.entry_animation_combo.findData(config.entry_animation)
        self.entry_animation_combo.setCurrentIndex(max(0, idx_anim))

        self.gradient_top_button = ColorButton(config.gradient_top, "Verlauf oben")
        self.gradient_bottom_button = ColorButton(config.gradient_bottom, "Verlauf unten")
        self.border_color_button = ColorButton(config.border_color, "Rahmen")

        wheel_sensitive_widgets = [
            self.random_spin,
            self.interval_spin,
            self.autohide_spin,
            self.animation_interval_spin,
            self.margin_x_spin,
            self.margin_y_spin,
            self.width_spin,
            self.height_spin,
            self.opacity_slider,
            self.text_opacity_slider,
            self.monitor_combo,
            self.position_combo,
            self.entry_animation_combo,
        ]
        for widget in wheel_sensitive_widgets:
            disable_wheel_scrolling(widget)

        def build_form(title: str, rows: List[Tuple[str, QtWidgets.QWidget]]) -> QtWidgets.QGroupBox:
            group = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout()
            form.setHorizontalSpacing(24)
            form.setVerticalSpacing(6)
            for label, widget in rows:
                form.addRow(label, widget)
            group.setLayout(form)
            return group

        color_row_widget = QtWidgets.QWidget()
        color_row = QtWidgets.QHBoxLayout(color_row_widget)
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(8)
        color_row.addWidget(self.gradient_top_button)
        color_row.addWidget(self.gradient_bottom_button)

        placement_group = build_form(
            "Platzierung & Größe",
            [
                ("Position", self.position_combo),
                ("Abstand X", self.margin_x_spin),
                ("Abstand Y", self.margin_y_spin),
                ("Breite (min.)", self.width_spin),
                ("Höhe", self.height_spin),
            ],
        )

        timing_group = build_form(
            "Timer & Verhalten",
            [
                ("Zufälliger Zeitversatz (s)", self.random_spin),
                ("Reminder Intervall", self.interval_spin),
                ("Auto-Hide", self.autohide_spin),
                ("Animationstempo", self.animation_interval_spin),
                ("Animation aktiv", self.animation_enabled_check),
                ("Erscheinungseffekt", self.entry_animation_combo),
            ],
        )

        appearance_group = build_form(
            "Farben & Transparenz",
            [
                ("Overlay-Deckkraft", self.opacity_slider),
                ("Text-Deckkraft", self.text_opacity_slider),
                ("Verlauf", color_row_widget),
                ("Rahmenfarbe", self.border_color_button),
            ],
        )

        text_group = build_form(
            "Texte & Inhalte",
            [
                ("Titel", self.title_edit),
                ("Nachricht", self.message_edit),
                ("Countdown aktiv", self.countdown_check),
                ("Countdown-Text", self.countdown_template_edit),
            ],
        )

        behavior_group = build_form(
            "System & Anzeige",
            [
                ("Autostart", self.autostart_check),
                ("Monitor", self.monitor_combo),
            ],
        )

        controls_widget = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(12)
        controls_layout.addWidget(placement_group)
        controls_layout.addWidget(timing_group)
        controls_layout.addWidget(appearance_group)
        controls_layout.addWidget(text_group)
        controls_layout.addWidget(behavior_group)
        controls_layout.addStretch()

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(controls_widget)

        preview_group = QtWidgets.QGroupBox("Live Vorschau")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        preview_layout.addWidget(self.preview, alignment=QtCore.Qt.AlignCenter)

        self.show_overlay_button = QtWidgets.QPushButton("Overlay jetzt anzeigen")
        self.show_overlay_button.setObjectName("primaryButton")
        self.show_overlay_button.clicked.connect(self._handle_show_overlay)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)

        outer_layout = QtWidgets.QVBoxLayout(self)
        outer_layout.addWidget(scroll, stretch=1)
        outer_layout.addWidget(preview_group)
        outer_layout.addWidget(self.show_overlay_button, alignment=QtCore.Qt.AlignCenter)
        outer_layout.addWidget(buttons)

        self.position_combo.currentIndexChanged.connect(self.apply_changes)
        self.random_spin.valueChanged.connect(self.apply_changes)
        self.interval_spin.valueChanged.connect(self.apply_changes)
        self.autohide_spin.valueChanged.connect(self.apply_changes)
        self.animation_interval_spin.valueChanged.connect(self.apply_changes)
        self.animation_enabled_check.toggled.connect(self.apply_changes)
        self.margin_x_spin.valueChanged.connect(self.apply_changes)
        self.margin_y_spin.valueChanged.connect(self.apply_changes)
        self.width_spin.valueChanged.connect(self.apply_changes)
        self.height_spin.valueChanged.connect(self.apply_changes)
        self.opacity_slider.valueChanged.connect(self.apply_changes)
        self.text_opacity_slider.valueChanged.connect(self.apply_changes)
        self.title_edit.textChanged.connect(self.apply_changes)
        self.message_edit.textChanged.connect(self.apply_changes)
        self.countdown_check.toggled.connect(self.apply_changes)
        self.countdown_template_edit.textChanged.connect(self.apply_changes)
        self.monitor_combo.currentIndexChanged.connect(self.apply_changes)
        self.autostart_check.toggled.connect(self.apply_changes)
        self.gradient_top_button.colorChanged.connect(self.apply_changes)
        self.gradient_bottom_button.colorChanged.connect(self.apply_changes)
        self.border_color_button.colorChanged.connect(self.apply_changes)

    def _handle_show_overlay(self) -> None:
        self.overlay.show_overlay()
        self.overlay.reset_reminder_timer()

    def _populate_monitors(self) -> None:
        if not hasattr(self, "monitor_combo"):
            return

        current = getattr(self.config, "monitor_id", "auto")
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.clear()
        self.monitor_combo.addItem("Aktiver Bildschirm", "auto")
        for screen in QtWidgets.QApplication.screens():
            self.monitor_combo.addItem(screen.name(), screen.name())
        index = self.monitor_combo.findData(current)
        if index < 0:
            index = 0
        self.monitor_combo.setCurrentIndex(index)
        self.monitor_combo.blockSignals(False)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # type: ignore[override]
        self._populate_monitors()
        super().showEvent(event)

    def apply_changes(self) -> None:
        self.config.position = self.position_combo.currentData()
        self.config.random_offset_ms = self.random_spin.value() * 1000
        self.config.reminder_interval_ms = int(self.interval_spin.value() * 60_000)
        self.config.auto_hide_ms = int(self.autohide_spin.value() * 1000)
        self.config.animation_interval_ms = int(self.animation_interval_spin.value() * 1000)
        self.config.animation_enabled = self.animation_enabled_check.isChecked()
        self.config.entry_animation = self.entry_animation_combo.currentData()
        self.config.margin_x = self.margin_x_spin.value()
        self.config.margin_y = self.margin_y_spin.value()
        self.config.overlay_width = self.width_spin.value()
        self.config.overlay_height = self.height_spin.value()
        self.config.overlay_opacity = max(0.1, self.opacity_slider.value() / 100.0)
        self.config.text_opacity = max(0.1, self.text_opacity_slider.value() / 100.0)
        self.config.title_text = self.title_edit.text()
        self.config.message_template = self.message_edit.toPlainText()
        self.config.countdown_enabled = self.countdown_check.isChecked()
        self.config.countdown_template = self.countdown_template_edit.text()
        self.config.monitor_id = self.monitor_combo.currentData()
        self.config.autostart_enabled = self.autostart_check.isChecked()
        self.config.gradient_top = self.gradient_top_button.color()
        self.config.gradient_bottom = self.gradient_bottom_button.color()
        self.config.border_color = self.border_color_button.color()

        self.overlay.apply_config(self.config)
        self.overlay.reset_reminder_timer()

        self.preview.apply_config(self.config)
        self.preview.show_overlay()
        update_autostart(self.config.autostart_enabled)
        save_config(self.config)


# ===== System tray integration =============================================


class TrayController(QtCore.QObject):
    """System-tray icon with quick commands and settings access."""

    def __init__(self, config: AppConfig, overlay: ReminderOverlay,
                 parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.overlay = overlay

        icon = self._load_tray_icon()
        self.tray = QtWidgets.QSystemTrayIcon(icon, parent)

        menu = QtWidgets.QMenu()
        self.remaining_action = menu.addAction("Verbleibend: --")
        self.remaining_action.setEnabled(False)
        menu.addSeparator()
        show_action = menu.addAction("Jetzt erinnern")
        show_action.triggered.connect(self.overlay.show_overlay)

        settings_action = menu.addAction("Einstellungen...")
        settings_action.triggered.connect(self.show_settings)

        exit_action = menu.addAction("Beenden")
        exit_action.triggered.connect(QtWidgets.QApplication.instance().quit)

        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Hydration Reminder")
        self.tray.show()

        self.settings_window = SettingsWindow(config, overlay)

    def _load_tray_icon(self) -> QtGui.QIcon:
        icon_path = self.config.asset_directory / "frame1.png"
        if icon_path.exists():
            pixmap = QtGui.QPixmap(str(icon_path))
            if not pixmap.isNull():
                return QtGui.QIcon(pixmap)

        fallback = DEFAULT_BOTTLE_GIF
        if fallback.exists():
            movie = QtGui.QMovie(str(fallback))
            if movie.isValid():
                movie.jumpToFrame(0)
                return QtGui.QIcon(movie.currentPixmap())

        return QtGui.QIcon(create_fallback_pixmap(64))

    def show_settings(self) -> None:
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def update_remaining_display(self, countdown_text: str) -> None:
        self.remaining_action.setText(f"Verbleibend: {countdown_text}")


# ===== Application bootstrap ===============================================


class ReminderApplication(QtWidgets.QApplication):
    """Main application wrapper that manages the overlay and tray icon."""

    def __init__(self, argv: List[str], config: AppConfig, first_run: bool = False) -> None:
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)

        self.config = config
        self.overlay = ReminderOverlay(config)
        self.tray = TrayController(config, self.overlay)
        self.overlay.countdownUpdated.connect(self.tray.update_remaining_display)

        if config.show_preview_on_launch:
            QtCore.QTimer.singleShot(config.preview_delay_ms, self.overlay.show_overlay)
        if first_run:
            QtCore.QTimer.singleShot(300, self.tray.show_settings)

        self.overlay.schedule_next_reminder()


# ===== CLI argument parsing ================================================


def apply_cli_overrides(config: AppConfig, argv: List[str]) -> AppConfig:
    parser = argparse.ArgumentParser(description="Hydration overlay reminder written in Python.")
    parser.add_argument("--interval", type=float, help="Reminder interval in minutes (default: 45).")
    parser.add_argument("--autohide", type=float, help="Auto-hide delay in seconds (default: 15).")
    parser.add_argument("--animation-speed", type=float, help="Animation interval in seconds (default: 0.2).")
    parser.add_argument("--no-preview", action="store_true", help="Skip the initial preview reminder.")
    parser.add_argument("--position", choices=["bottom_right", "bottom_left", "top_right", "top_left"],
                        help="Fixed overlay corner (default: bottom_right).")
    parser.add_argument("--margin-x", type=int, help="Horizontal margin from screen edge (px).")
    parser.add_argument("--margin-y", type=int, help="Vertical margin from screen edge (px).")
    parser.add_argument("--random", type=int, help="Random delay (seconds) added to each reminder interval.")
    parser.add_argument("--width", type=int, help="Overlay width in pixels.")
    parser.add_argument("--height", type=int, help="Overlay height in pixels.")
    parser.add_argument("--opacity", type=float, help="Overlay opacity 0.1-1.0.")
    parser.add_argument("--monitor", help="Monitor name (as shown in Einstellungen) to force overlay onto.")
    parser.add_argument(
        "--entry-animation",
        choices=["fade", "slide", "pop"],
        help="Choose how the reminder enters the screen (fade, slide, pop).",
    )

    args = parser.parse_args(argv)

    if args.interval:
        config.reminder_interval_ms = int(max(1.0, args.interval) * 60_000)
    if args.autohide:
        config.auto_hide_ms = int(max(1.0, args.autohide) * 1000)
    if args.animation_speed:
        config.animation_interval_ms = int(max(0.05, args.animation_speed) * 1000)
    if args.no_preview:
        config.show_preview_on_launch = False
    if args.position:
        config.position = args.position
    if args.margin_x is not None:
        config.margin_x = max(0, args.margin_x)
    if args.margin_y is not None:
        config.margin_y = max(0, args.margin_y)
    if args.random is not None:
        config.random_offset_ms = max(0, args.random) * 1000
    if args.width:
        config.overlay_width = max(180, args.width)
    if args.height:
        config.overlay_height = max(120, args.height)
    if args.opacity:
        config.overlay_opacity = min(1.0, max(0.1, args.opacity))
    if args.monitor:
        config.monitor_id = args.monitor
    if args.entry_animation:
        config.entry_animation = args.entry_animation

    return config


def main(argv: List[str]) -> int:
    enable_high_dpi_awareness()
    first_run = not SETTINGS_FILE.exists()
    config = load_config_from_disk()
    config = apply_cli_overrides(config, argv)
    save_config(config)

    app = ReminderApplication(sys.argv, config, first_run=first_run)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
