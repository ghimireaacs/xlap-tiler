#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Xlap: A lightweight window tiling and snapping tool for X11-based desktops.

This script provides context-aware window tiling capabilities through global
hotkeys (Super + Alt + Arrow Keys) and a system tray indicator menu.

Dependencies on Debian/Kali Linux:
- python3-gi
- python3-pynput
- gir1.2-gtk-3.0
- gir1.2-appindicator3-0.1 (or gir1.2-ayatanaappindicator3-0.1)
- xdotool
- libnotify-bin (for notify-send)

Installation of Dependencies:
sudo apt update
sudo apt install python3-gi python3-pynput gir1.2-gtk-3.0 \
                 gir1.2-appindicator3-0.1 xdotool libnotify-bin
"""

import json
import os
import re
import signal
import subprocess
import sys
from functools import partial
from json import JSONDecodeError
from pathlib import Path
from threading import Thread
from typing import Dict, List, Optional, Tuple, Any

from pynput import keyboard

import gi

# --- GTK Indicator Setup ---
try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ValueError, ImportError):
    print("Error: GTK 3.0 bindings not found. Please install 'gir1.2-gtk-3.0'.")
    sys.exit(1)


def gtk_module_exists(module_name: str, version: str) -> bool:
    """Checks if a GI repository is available."""
    try:
        gi.require_version(module_name, version)
        return True
    except (ValueError, ImportError):
        return False


if gtk_module_exists("AppIndicator3", "0.1"):
    from gi.repository import AppIndicator3
elif gtk_module_exists("AyatanaAppIndicator3", "0.1"):
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
else:
    print("Error: Requires either AppIndicator3 or AyatanaAppIndicator3.")
    print(
        "Please install 'gir1.2-appindicator3-0.1' or 'gir1.2-ayatanaappindicator3-0.1'."
    )
    sys.exit(1)

# --- Debug Flag ---
XLAP_DEBUG = os.environ.get("XLAP_DEBUG", "false").lower() in ("true", "1", "t")


# --- Configuration Management ---
class Config:
    """Manages application configuration from a JSON file."""

    window_margin_top: int = 30
    window_margin_left: int = 30
    screen_margin_bottom: int = 30
    screen_margin_right: int = 30
    notify_on_apply_layout: bool = False
    notify_on_launch: bool = True

    _config_path = Path.home() / ".xlap-conf.json"

    @classmethod
    def get_path(cls) -> Path:
        """Returns the path to the configuration file."""
        return cls._config_path

    @classmethod
    def load(cls) -> None:
        """Loads configuration from the JSON file."""
        if XLAP_DEBUG:
            print("Config: Loading configuration...")

        if not cls._config_path.exists():
            cls.save_default()

        try:
            with open(cls._config_path, "r") as f:
                data = json.load(f)
                cls.window_margin_top = data.get(
                    "window_margin_top", cls.window_margin_top
                )
                cls.window_margin_left = data.get(
                    "window_margin_left", cls.window_margin_left
                )
                cls.screen_margin_bottom = data.get(
                    "screen_margin_bottom", cls.screen_margin_bottom
                )
                cls.screen_margin_right = data.get(
                    "screen_margin_right", cls.screen_margin_right
                )
                cls.notify_on_apply_layout = data.get(
                    "notify_on_apply_layout", cls.notify_on_apply_layout
                )
                cls.notify_on_launch = data.get(
                    "notify_on_launch", cls.notify_on_launch
                )
        except (JSONDecodeError, TypeError) as e:
            Notify.send(
                summary="XLAP: Invalid Configuration",
                description=f"Using default config. Error in {cls._config_path}: {e}",
                expire_time=10000,
            )
            if XLAP_DEBUG:
                print(f"Config Error: {e}")

        if XLAP_DEBUG:
            print(f"Config: Loaded. Notify on apply: {cls.notify_on_apply_layout}")

    @classmethod
    def save_default(cls) -> None:
        """Saves the default configuration to a file."""
        default_conf = {
            "window_margin_top": cls.window_margin_top,
            "window_margin_left": cls.window_margin_left,
            "screen_margin_bottom": cls.screen_margin_bottom,
            "screen_margin_right": cls.screen_margin_right,
            "notify_on_apply_layout": cls.notify_on_apply_layout,
            "notify_on_launch": cls.notify_on_launch,
        }
        with open(cls._config_path, "w") as f:
            json.dump(default_conf, f, indent=4)
        if XLAP_DEBUG:
            print(f"Config: Saved default configuration to {cls._config_path}")


# --- Layout Definitions ---
class Layouts:
    """Namespace for layout string constants."""

    # Special states
    FULL_SCREEN = "Full Screen"
    MAXIMIZED = "Maximized"
    ALMOST_MAXIMIZED = "Almost Maximized"
    # Columns
    COL_50_LEFT = "50% Left"
    COL_50_RIGHT = "50% Right"
    COL_66_LEFT = "66% Left"
    COL_66_RIGHT = "66% Right"
    COL_33_LEFT = "33% Left"
    COL_33_CENTER = "33% Center"
    COL_33_RIGHT = "33% Right"
    # Rows
    ROW_50_TOP = "50% Top"
    ROW_50_BOTTOM = "50% Bottom"
    ROW_66_TOP = "66% Top"
    ROW_66_BOTTOM = "66% Bottom"
    ROW_33_TOP = "33% Top"
    ROW_33_CENTER = "33% Middle"
    ROW_33_BOTTOM = "33% Bottom"
    # 2x2 Cells
    CELL_50_LEFT_TOP = "50% Top Left"
    CELL_50_LEFT_BOTTOM = "50% Bottom Left"
    CELL_50_RIGHT_TOP = "50% Top Right"
    CELL_50_RIGHT_BOTTOM = "50% Bottom Right"
    # 3x3 Cells
    CELL_33_LEFT_TOP = "33% Top Left"
    CELL_33_LEFT_CENTER = "33% Middle Left"
    CELL_33_LEFT_BOTTOM = "33% Bottom Left"
    CELL_33_CENTER_TOP = "33% Top Center"
    CELL_33_CENTER_CENTER = "33% Middle Center"
    CELL_33_CENTER_BOTTOM = "33% Bottom Center"
    CELL_33_RIGHT_TOP = "33% Top Right"
    CELL_33_RIGHT_CENTER = "33% Middle Right"
    CELL_33_RIGHT_BOTTOM = "33% Bottom Right"


# --- Data-Driven Layout Geometry ---
# (x_factor, y_factor, width_factor, height_factor)
LAYOUT_GEOMETRY: Dict[str, Tuple[float, float, float, float]] = {
    Layouts.ALMOST_MAXIMIZED: (0.0, 0.0, 1.0, 1.0),
    Layouts.COL_50_LEFT: (0.0, 0.0, 0.5, 1.0),
    Layouts.COL_50_RIGHT: (0.5, 0.0, 0.5, 1.0),
    Layouts.COL_66_LEFT: (0.0, 0.0, 2 / 3, 1.0),
    Layouts.COL_66_RIGHT: (1 / 3, 0.0, 2 / 3, 1.0),
    Layouts.COL_33_LEFT: (0.0, 0.0, 1 / 3, 1.0),
    Layouts.COL_33_CENTER: (1 / 3, 0.0, 1 / 3, 1.0),
    Layouts.COL_33_RIGHT: (2 / 3, 0.0, 1 / 3, 1.0),
    Layouts.ROW_50_TOP: (0.0, 0.0, 1.0, 0.5),
    Layouts.ROW_50_BOTTOM: (0.0, 0.5, 1.0, 0.5),
    Layouts.ROW_66_TOP: (0.0, 0.0, 1.0, 2 / 3),
    Layouts.ROW_66_BOTTOM: (0.0, 1 / 3, 1.0, 2 / 3),
    Layouts.ROW_33_TOP: (0.0, 0.0, 1.0, 1 / 3),
    Layouts.ROW_33_CENTER: (0.0, 1 / 3, 1.0, 1 / 3),
    Layouts.ROW_33_BOTTOM: (0.0, 2 / 3, 1.0, 1 / 3),
    Layouts.CELL_50_LEFT_TOP: (0.0, 0.0, 0.5, 0.5),
    Layouts.CELL_50_LEFT_BOTTOM: (0.0, 0.5, 0.5, 0.5),
    Layouts.CELL_50_RIGHT_TOP: (0.5, 0.0, 0.5, 0.5),
    Layouts.CELL_50_RIGHT_BOTTOM: (0.5, 0.5, 0.5, 0.5),
    Layouts.CELL_33_LEFT_TOP: (0.0, 0.0, 1 / 3, 1 / 3),
    Layouts.CELL_33_LEFT_CENTER: (0.0, 1 / 3, 1 / 3, 1 / 3),
    Layouts.CELL_33_LEFT_BOTTOM: (0.0, 2 / 3, 1 / 3, 1 / 3),
    Layouts.CELL_33_CENTER_TOP: (1 / 3, 0.0, 1 / 3, 1 / 3),
    Layouts.CELL_33_CENTER_CENTER: (1 / 3, 1 / 3, 1 / 3, 1 / 3),
    Layouts.CELL_33_CENTER_BOTTOM: (1 / 3, 2 / 3, 1 / 3, 1 / 3),
    Layouts.CELL_33_RIGHT_TOP: (2 / 3, 0.0, 1 / 3, 1 / 3),
    Layouts.CELL_33_RIGHT_CENTER: (2 / 3, 1 / 3, 1 / 3, 1 / 3),
    Layouts.CELL_33_RIGHT_BOTTOM: (2 / 3, 2 / 3, 1 / 3, 1 / 3),
}

# Ordered list for cycling through layouts
LAYOUT_SEQUENCE = [
    Layouts.FULL_SCREEN,
    Layouts.MAXIMIZED,
    Layouts.ALMOST_MAXIMIZED,
    Layouts.COL_50_LEFT,
    Layouts.COL_50_RIGHT,
    Layouts.COL_66_LEFT,
    Layouts.COL_66_RIGHT,
    Layouts.COL_33_LEFT,
    Layouts.COL_33_CENTER,
    Layouts.COL_33_RIGHT,
    Layouts.ROW_50_TOP,
    Layouts.ROW_50_BOTTOM,
    Layouts.ROW_66_TOP,
    Layouts.ROW_66_BOTTOM,
    Layouts.ROW_33_TOP,
    Layouts.ROW_33_CENTER,
    Layouts.ROW_33_BOTTOM,
    Layouts.CELL_50_LEFT_TOP,
    Layouts.CELL_50_LEFT_BOTTOM,
    Layouts.CELL_50_RIGHT_TOP,
    Layouts.CELL_50_RIGHT_BOTTOM,
    Layouts.CELL_33_LEFT_TOP,
    Layouts.CELL_33_LEFT_CENTER,
    Layouts.CELL_33_LEFT_BOTTOM,
    Layouts.CELL_33_CENTER_TOP,
    Layouts.CELL_33_CENTER_CENTER,
    Layouts.CELL_33_CENTER_BOTTOM,
    Layouts.CELL_33_RIGHT_TOP,
    Layouts.CELL_33_RIGHT_CENTER,
    Layouts.CELL_33_RIGHT_BOTTOM,
]

# Defines what layout to switch to from a source layout given a direction
LAYOUT_TRANSITIONS: Dict[Tuple[str, str], str] = {
    # From a 50% split, refine to a corner
    (Layouts.COL_50_LEFT, "up"): Layouts.CELL_50_LEFT_TOP,
    (Layouts.COL_50_LEFT, "down"): Layouts.CELL_50_LEFT_BOTTOM,
    (Layouts.COL_50_RIGHT, "up"): Layouts.CELL_50_RIGHT_TOP,
    (Layouts.COL_50_RIGHT, "down"): Layouts.CELL_50_RIGHT_BOTTOM,
    (Layouts.ROW_50_TOP, "left"): Layouts.CELL_50_LEFT_TOP,
    (Layouts.ROW_50_TOP, "right"): Layouts.CELL_50_RIGHT_TOP,
    (Layouts.ROW_50_BOTTOM, "left"): Layouts.CELL_50_LEFT_BOTTOM,
    (Layouts.ROW_50_BOTTOM, "right"): Layouts.CELL_50_RIGHT_BOTTOM,
}

# Defines the default layout for a given direction if no other rule matches
DEFAULT_TRANSITIONS: Dict[str, str] = {
    "left": Layouts.COL_50_LEFT,
    "right": Layouts.COL_50_RIGHT,
    "up": Layouts.ROW_50_TOP,
    "down": Layouts.ROW_50_BOTTOM,
}


# --- Core Logic ---
class XlapCore:
    """Encapsulates the core window management logic."""

    def __init__(self):
        self._window_state: Dict[str, int] = {}  # {window_id: layout_index}

    def _run_command(self, cmd: List[str]) -> str:
        """Executes a shell command and returns its stdout."""
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=True, encoding="utf-8"
            )
            return result.stdout.strip()
        except FileNotFoundError:
            Notify.send(
                f"Error: Command '{cmd[0]}' not found.",
                "Please ensure it's installed and in your PATH.",
                expire_time=10000,
            )
            if XLAP_DEBUG:
                print(f"Command not found: {cmd}")
            return ""
        except subprocess.CalledProcessError as e:
            if XLAP_DEBUG:
                print(f"Command failed: {cmd}\nError: {e.stderr}")
            return ""

    def get_active_window_id(self) -> Optional[str]:
        """Gets the ID of the currently focused window."""
        return self._run_command(["xdotool", "getwindowfocus"]) or None

    def get_window_position(self, window_id: str) -> Optional[Tuple[int, int]]:
        """Gets the (left, top) position of a window."""
        output = self._run_command(["xdotool", "getwindowgeometry", window_id])
        match = re.search(r"Position: (\d+),(\d+)", output)
        if match:
            left, top = int(match.group(1)), int(match.group(2))
            return max(0, left), max(0, top)
        return None

    def get_connected_displays(self) -> List[Dict[str, int]]:
        """Parses xrandr output to find connected display geometries."""
        output = self._run_command(["xrandr"])
        displays = []
        pattern = re.compile(r" connected(?: primary)? (\d+)x(\d+)\+(\d+)\+(\d+)")
        for line in output.splitlines():
            match = pattern.search(line)
            if match:
                w, h, ox, oy = map(int, match.groups())
                displays.append(
                    {
                        "x_start": ox,
                        "x_end": ox + w,
                        "y_start": oy,
                        "y_end": oy + h,
                        "offset_left": ox,
                        "offset_top": oy,
                        "width": w,
                        "height": h,
                    }
                )
        if XLAP_DEBUG:
            print(f"Displays found: {displays}")
        return displays

    def get_display_for_window(self, window_id: str) -> Optional[Dict[str, int]]:
        """Finds which display a given window is on."""
        pos = self.get_window_position(window_id)
        if not pos:
            return None
        left, top = pos

        for display in self.get_connected_displays():
            if (
                display["x_start"] <= left < display["x_end"]
                and display["y_start"] <= top < display["y_end"]
            ):
                if XLAP_DEBUG:
                    print(f"Window {window_id} is on display: {display}")
                return display

        displays = self.get_connected_displays()
        return displays[0] if displays else None

    def _set_window_state(self, window_id: str, state_action: str, state_prop: str):
        self._run_command(
            ["xdotool", "windowstate", state_action, state_prop, window_id]
        )

    def apply_layout(self, layout: str, window_id: str) -> None:
        """Applies a given layout to a window."""
        if not window_id:
            return
        if XLAP_DEBUG:
            print(f"Applying layout '{layout}' to window {window_id}")

        if layout not in LAYOUT_SEQUENCE:
            if XLAP_DEBUG:
                print(
                    f"Warning: Layout '{layout}' not in LAYOUT_SEQUENCE. Defaulting to index 0."
                )
            self._window_state[window_id] = 0
        else:
            self._window_state[window_id] = LAYOUT_SEQUENCE.index(layout)

        if layout == Layouts.FULL_SCREEN:
            self._set_window_state(window_id, "--add", "fullscreen")
        elif layout == Layouts.MAXIMIZED:
            self._set_window_state(window_id, "--remove", "fullscreen")
            self._set_window_state(window_id, "--add", "maximized_vert,maximized_horz")
        else:
            display = self.get_display_for_window(window_id)
            if not display:
                Notify.send("Layout Error", f"Display not found for window {window_id}")
                return

            geom = LAYOUT_GEOMETRY.get(layout)
            if not geom:
                if XLAP_DEBUG:
                    print(f"Error: No geometry defined for layout '{layout}'")
                return

            screen_w = display["width"] - Config.screen_margin_right
            screen_h = display["height"] - Config.screen_margin_bottom
            x_f, y_f, w_f, h_f = geom

            width = int(screen_w * w_f) - Config.window_margin_left
            height = int(screen_h * h_f) - Config.window_margin_top
            left = (
                display["offset_left"] + int(screen_w * x_f) + Config.window_margin_left
            )
            top = display["offset_top"] + int(screen_h * y_f) + Config.window_margin_top

            self._set_window_state(
                window_id, "--remove", "fullscreen,maximized_vert,maximized_horz"
            )
            self._run_command(
                ["xdotool", "windowsize", window_id, str(width), str(height)]
            )
            self._run_command(["xdotool", "windowmove", window_id, str(left), str(top)])

        if Config.notify_on_apply_layout:
            Notify.send(layout)

    def modify_layout(self, direction: str) -> None:
        """Applies a new layout based on the current one and a direction."""
        active_window = self.get_active_window_id()
        if not active_window:
            return

        # Get the current layout name from the stored state index
        # Default to 'Maximized' if no state is recorded yet.
        last_layout_index = self._window_state.get(
            active_window, LAYOUT_SEQUENCE.index(Layouts.MAXIMIZED)
        )
        current_layout = LAYOUT_SEQUENCE[last_layout_index]

        if XLAP_DEBUG:
            print(
                f"\nModifying layout. Current: '{current_layout}', Direction: '{direction}'"
            )

        # Look for a specific transition (e.g., from 50% Right + up -> Top Right)
        transition_key = (current_layout, direction)
        new_layout = LAYOUT_TRANSITIONS.get(transition_key)

        # If no specific transition is found, use the default for the direction
        if not new_layout:
            new_layout = DEFAULT_TRANSITIONS.get(direction)

        if new_layout:
            if XLAP_DEBUG:
                print(f"Transitioning to new layout: '{new_layout}'")
            self.apply_layout(layout=new_layout, window_id=active_window)
        elif XLAP_DEBUG:
            print("No valid transition found.")


# --- Hotkey Listener ---
class HotkeyListener(Thread):
    """Listens for global hotkeys in a separate thread."""

    def __init__(self, core: XlapCore):
        super().__init__(daemon=True)
        self._core = core

        self.hotkeys = {
            "<cmd>+<alt>+<left>": partial(self._core.modify_layout, "left"),
            "<cmd>+<alt>+<right>": partial(self._core.modify_layout, "right"),
            "<cmd>+<alt>+<up>": partial(self._core.modify_layout, "up"),
            "<cmd>+<alt>+<down>": partial(self._core.modify_layout, "down"),
        }

    def run(self) -> None:
        if XLAP_DEBUG:
            print(f"HotkeyListener: Starting with hotkeys: {list(self.hotkeys.keys())}")
        with keyboard.GlobalHotKeys(self.hotkeys) as h:
            h.join()


# --- System Tray Indicator ---
class IndicatorApp:
    """Manages the GTK system tray indicator and its menu."""

    MENU_STRUCTURE = [
        {"label": "Context-Aware Tiling", "type": "header"},
        {"label": "Snap Left (Super + Alt + ←)", "action": "snap_left"},
        {"label": "Snap Right (Super + Alt + →)", "action": "snap_right"},
        {"label": "Snap Up (Super + Alt + ↑)", "action": "snap_up"},
        {"label": "Snap Down (Super + Alt + ↓)", "action": "snap_down"},
        {"type": "separator"},
        {"label": "Manual Layouts", "type": "header"},
        {
            "label": "Single Window",
            "submenu": [
                {"label": Layouts.FULL_SCREEN, "type": "layout"},
                {"label": Layouts.MAXIMIZED, "type": "layout"},
                {"label": Layouts.ALMOST_MAXIMIZED, "type": "layout"},
            ],
        },
        {
            "label": "Columns",
            "submenu": [
                {"label": Layouts.COL_50_LEFT, "type": "layout"},
                {"label": Layouts.COL_50_RIGHT, "type": "layout"},
                {"type": "separator"},
                {"label": Layouts.COL_66_LEFT, "type": "layout"},
                {"label": Layouts.COL_66_RIGHT, "type": "layout"},
                {"type": "separator"},
                {"label": Layouts.COL_33_LEFT, "type": "layout"},
                {"label": Layouts.COL_33_CENTER, "type": "layout"},
                {"label": Layouts.COL_33_RIGHT, "type": "layout"},
            ],
        },
        {
            "label": "Rows & Cells",
            "submenu": [
                {"label": Layouts.ROW_50_TOP, "type": "layout"},
                {"label": Layouts.ROW_50_BOTTOM, "type": "layout"},
                {"type": "separator"},
                {"label": Layouts.CELL_50_LEFT_TOP, "type": "layout"},
                {"label": Layouts.CELL_50_RIGHT_TOP, "type": "layout"},
                {"label": Layouts.CELL_50_LEFT_BOTTOM, "type": "layout"},
                {"label": Layouts.CELL_50_RIGHT_BOTTOM, "type": "layout"},
            ],
        },
        {"type": "separator"},
        {"label": "Xlap", "type": "header"},
        {"label": "Settings...", "action": "settings"},
        {"label": "Reload Config", "action": "reload_config"},
        {"label": "About", "action": "about"},
        {"type": "separator"},
        {"label": "Exit", "action": "exit"},
    ]

    def __init__(self, core: XlapCore):
        self._core = core
        self.indicator = AppIndicator3.Indicator.new(
            "xlap",
            "view-grid-symbolic",
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Xlap")
        self.indicator.set_icon_full("view-grid-symbolic", "Window snap assistant")
        self.indicator.set_menu(self._build_menu())

    def _build_menu_items(self, items: List[Dict[str, Any]]) -> Gtk.Menu:
        """Recursively builds a GTK menu from a list of dictionaries."""
        menu = Gtk.Menu()
        for item_def in items:
            item_type = item_def.get("type")
            label = item_def.get("label", "")

            if item_type == "separator":
                menu.append(Gtk.SeparatorMenuItem())
                continue

            menu_item = Gtk.MenuItem(label=label)

            if "submenu" in item_def:
                submenu = self._build_menu_items(item_def["submenu"])
                menu_item.set_submenu(submenu)
            elif item_type == "header":
                menu_item.set_sensitive(False)
            else:  # Actionable item
                action_name = item_def.get("action")
                if item_type == "layout":
                    menu_item.connect("activate", self._on_layout_activate, label)
                elif action_name:
                    action_handler = getattr(self, f"_action_{action_name}", None)
                    if action_handler:
                        menu_item.connect("activate", action_handler)
            menu.append(menu_item)
        return menu

    def _build_menu(self) -> Gtk.Menu:
        menu = self._build_menu_items(self.MENU_STRUCTURE)
        menu.show_all()
        return menu

    def _on_layout_activate(self, _: Gtk.MenuItem, layout_name: str) -> None:
        active_window = self._core.get_active_window_id()
        if active_window:
            self._core.apply_layout(layout=layout_name, window_id=active_window)

    def _action_snap_left(self, _: Gtk.MenuItem) -> None:
        self._core.modify_layout("left")

    def _action_snap_right(self, _: Gtk.MenuItem) -> None:
        self._core.modify_layout("right")

    def _action_snap_up(self, _: Gtk.MenuItem) -> None:
        self._core.modify_layout("up")

    def _action_snap_down(self, _: Gtk.MenuItem) -> None:
        self._core.modify_layout("down")

    def _action_settings(self, _: Gtk.MenuItem) -> None:
        subprocess.Popen(["xdg-open", str(Config.get_path())])

    def _action_reload_config(self, _: Gtk.MenuItem) -> None:
        Config.load()
        Notify.send("Xlap Config Reloaded")

    def _action_about(self, _: Gtk.MenuItem) -> None:
        subprocess.Popen(["xdg-open", "https://gitlab.com/sri-at-gitlab/projects/xlap"])

    def _action_exit(self, _: Gtk.MenuItem) -> None:
        Gtk.main_quit()


# --- Notification Utility ---
class Notify:
    @staticmethod
    def send(
        summary: str,
        description: str = "",
        icon: str = "preferences-desktop-display",
        expire_time: int = 2000,
    ):
        try:
            subprocess.run(
                [
                    "notify-send",
                    "--icon",
                    icon,
                    "--app-name",
                    "Xlap",
                    "--expire-time",
                    str(expire_time),
                    summary,
                    description,
                ],
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            if XLAP_DEBUG:
                print(
                    f"Notification failed: {e}\nSummary: {summary}\nDesc: {description}"
                )


# --- Main Execution ---
def check_dependencies():
    """Checks for required command-line tools."""
    deps = ["xdotool", "xrandr", "notify-send"]
    missing = [
        dep
        for dep in deps
        if subprocess.run(["which", dep], capture_output=True).returncode != 0
    ]
    if missing:
        print(
            f"Error: Missing required command(s): {', '.join(missing)}.",
            file=sys.stderr,
        )
        print(
            "On Debian/Kali, install them with: sudo apt install xdotool libnotify-bin",
            file=sys.stderr,
        )
        sys.exit(1)


def main():
    """Main function to initialize and run the application."""
    check_dependencies()
    Config.load()
    core = XlapCore()
    signal.signal(signal.SIGINT, lambda s, f: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda s, f: Gtk.main_quit())
    hotkey_thread = HotkeyListener(core)
    hotkey_thread.start()
    IndicatorApp(core)
    if Config.notify_on_launch:
        Notify.send("Xlap launched", "Context-aware tiling is active.")
    Gtk.main()
    if XLAP_DEBUG:
        print("Xlap is shutting down.")


if __name__ == "__main__":
    main()
