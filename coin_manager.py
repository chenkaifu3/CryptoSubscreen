import copy
import ctypes
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw
import pystray
import requests

from config import DEFAULT_CONFIG, THEMES, load_config, save_config
from control import get_view_mode, set_view_mode


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
        ("szDevice", ctypes.c_wchar * 32),
    ]


_MonitorEnumProc = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.POINTER(_RECT),
    ctypes.c_double,
)


def get_monitors():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    monitors = []
    user32 = ctypes.windll.user32

    def callback(hmon, hdc, rect, _):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        monitors.append({
            "name": info.szDevice,
            "x": r.left,
            "y": r.top,
            "width": r.right - r.left,
            "height": r.bottom - r.top,
            "primary": bool(info.dwFlags & 1),
        })
        return 1

    user32.EnumDisplayMonitors(None, None, _MonitorEnumProc(callback), 0)
    return monitors


class CoinManager:
    _COIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coin.py")
    _PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coin.pid")

    def _coin_script(self):
        return self._COIN_SCRIPT

    BG = "#0A0A0A"
    CARD = "#111111"
    ACCENT = "#00F0FF"
    TEXT = "#E0E0E0"
    MUTED = "#888888"
    GREEN = "#00FF66"
    RED = "#FF0055"

    def __init__(self):
        self.cfg = load_config()
        self.monitors = get_monitors()
        self.monitor_proc = None

        self.root = tk.Tk()
        self.root.title("副屏显示管理")
        self.root.geometry("550x545")
        self.root.configure(bg=self.BG)
        self.root.resizable(False, False)

        # 内部变量
        self.width_var = tk.StringVar()
        self.height_var = tk.StringVar()
        self.x_var = tk.StringVar()
        self.y_var = tk.StringVar()
        self.symbols_var = tk.StringVar()
        self.theme_var = tk.StringVar(value=self.cfg["display"].get("theme", "cyberpunk"))
        self.weather_var = tk.BooleanVar(value=self.cfg.get("weather", {}).get("enabled", True))
        self.theme_btns = {}

        self._build_ui()
        self._load_to_ui()
        self._init_tray()
        self.root.bind("<Unmap>", self._on_unmap)

    def _label(self, parent, text, **kw):
        return tk.Label(
            parent, text=text, bg=self.CARD, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w", **kw,
        )

    def _section(self, parent, title):
        frame = tk.Frame(parent, bg=self.CARD, padx=14, pady=8)
        frame.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(
            frame, text=title, bg=self.CARD, fg=self.ACCENT,
            font=("Microsoft YaHei UI", 10, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 4))
        return frame

    def _style_button(self, btn, normal_bg, hover_bg, normal_fg, hover_fg):
        btn.config(bg=normal_bg, fg=normal_fg, activebackground=hover_bg, activeforeground=hover_fg)
        btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg, fg=hover_fg) if btn["state"] != "disabled" else None)
        btn.bind("<Leave>", lambda e: btn.config(bg=normal_bg, fg=normal_fg) if btn["state"] != "disabled" else None)

    def _styled_btn(self, parent, text, command, style_type="normal", font=None, **pack_opts):
        btn = tk.Button(parent, text=text, command=command, relief="flat", bd=0, padx=8, pady=4)
        if font:
            btn.config(font=font)
        else:
            btn.config(font=("Microsoft YaHei UI", 8))
            
        self._apply_btn_style(btn, style_type)
        btn.pack(**pack_opts)
        return btn

    def _apply_btn_style(self, btn, style_type):
        if style_type == "accent":
            self._style_button(btn, self.ACCENT, "#00B8CC", "#000000", "#000000")
        elif style_type == "green":
            self._style_button(btn, self.GREEN, "#00CC52", "#000000", "#000000")
        elif style_type == "red":
            self._style_button(btn, self.RED, "#CC0044", "#000000", "#000000")
        elif style_type == "muted":
            self._style_button(btn, "#1A1A1A", "#2A2A2A", self.MUTED, self.TEXT)
        elif style_type == "danger":
            self._style_button(btn, "#1A1A1A", self.RED, self.TEXT, "#000000")
        elif style_type == "accent_border":
            self._style_button(btn, "#1A1A1A", "#2A2A2A", self.ACCENT, self.ACCENT)
        else: # normal
            self._style_button(btn, "#1A1A1A", "#2A2A2A", self.TEXT, self.TEXT)

    def _set_status(self, text, color=None):
        if color is None:
            color = self.MUTED
        self.status_label.config(fg=color)
        self.status_var.set(text)

    def _build_ui(self):
        tk.Label(
            self.root, text="副屏仪表盘管理", bg=self.BG, fg=self.ACCENT,
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(pady=(10, 6))

        # --- 设备与风格设置 ---
        sec = self._section(self.root, "显示设备与风格")

        row = tk.Frame(sec, bg=self.CARD)
        row.pack(fill="x", pady=2)
        self._label(row, "选择显示器:").pack(side="left")
        self.monitor_var = tk.StringVar()
        names = []
        for i, m in enumerate(self.monitors):
            tag = " [主屏]" if m["primary"] else ""
            names.append(f"显示器 {i + 1}{tag}  {m['width']}x{m['height']}")
        self.monitor_combo = ttk.Combobox(
            row, textvariable=self.monitor_var, values=names,
            state="readonly", width=25,
        )
        self.monitor_combo.pack(side="right")
        self.monitor_combo.bind("<<ComboboxSelected>>", self._on_monitor_select)

        # 手动修改坐标与尺寸区域
        param_row = tk.Frame(sec, bg=self.CARD)
        param_row.pack(fill="x", pady=(3, 2))

        def _add_param_entry(parent, label_text, var):
            f = tk.Frame(parent, bg=self.CARD)
            f.pack(side="left", expand=True, fill="x", padx=1)
            tk.Label(f, text=label_text, bg=self.CARD, fg=self.MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left")
            e = tk.Entry(
                f, textvariable=var, bg="#1A1A1A", fg=self.TEXT,
                insertbackground=self.TEXT, relief="flat", bd=0, width=6,
                font=("Consolas", 9), justify="center"
            )
            e.pack(side="left", padx=(2, 0))
            e.bind("<FocusOut>", self._on_entry_edited)
            e.bind("<Return>", self._on_entry_edited)
            e.bind("<KeyRelease>", self._on_entry_edited)
            return e

        _add_param_entry(param_row, "X:", self.x_var)
        _add_param_entry(param_row, "Y:", self.y_var)
        _add_param_entry(param_row, "宽:", self.width_var)
        _add_param_entry(param_row, "高:", self.height_var)

        # 快捷对齐与显示选项行
        opt_row = tk.Frame(sec, bg=self.CARD)
        opt_row.pack(fill="x", pady=(3, 2))

        self._styled_btn(
            opt_row, "主屏左侧齐平 (-W,0)", self._align_left_top, "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", padx=(0, 4)
        )
        self._styled_btn(
            opt_row, "重置坐标", self._reset_to_detected_monitor, "muted",
            font=("Microsoft YaHei UI", 8), side="left", padx=(0, 6)
        )

        self.topmost_var = tk.BooleanVar()
        self.borderless_var = tk.BooleanVar()

        tk.Checkbutton(
            opt_row, text="置顶", variable=self.topmost_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(2, 0))
        tk.Checkbutton(
            opt_row, text="无边框", variable=self.borderless_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(2, 0))
        tk.Checkbutton(
            opt_row, text="湘熙水郡天气", variable=self.weather_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(2, 0))

        # 主题风格色系切换行
        theme_row = tk.Frame(sec, bg=self.CARD)
        theme_row.pack(fill="x", pady=(4, 2))
        self._label(theme_row, "风格色系:").pack(side="left", padx=(0, 4))

        theme_box = tk.Frame(theme_row, bg=self.CARD)
        theme_box.pack(side="left", fill="x", expand=True)

        for key, t_info in THEMES.items():
            btn = self._styled_btn(
                theme_box, t_info["name"], lambda k=key: self._on_select_theme(k), "muted",
                font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=1
            )
            self.theme_btns[key] = btn

        # --- 监控币种配置 ---
        sec2 = self._section(self.root, "监控币种配置 (自定义与模版)")

        # 自定义输入行
        custom_row = tk.Frame(sec2, bg=self.CARD)
        custom_row.pack(fill="x", pady=(2, 4))
        self._label(custom_row, "币种列表:").pack(side="left", padx=(0, 4))

        self.symbol_entry = tk.Entry(
            custom_row, textvariable=self.symbols_var, bg="#1A1A1A", fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat", bd=0, font=("Consolas", 9)
        )
        self.symbol_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=2)
        self.symbol_entry.bind("<Return>", lambda e: self._apply_custom_symbols())

        self._styled_btn(
            custom_row, "应用", self._apply_custom_symbols, "accent",
            font=("Microsoft YaHei UI", 8, "bold"), side="right"
        )

        # 常用标签快速点选行
        tags_frame = tk.Frame(sec2, bg=self.CARD)
        tags_frame.pack(fill="x", pady=(2, 4))
        self._label(tags_frame, "点选加减:").pack(side="left", padx=(0, 4))

        quick_coins = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "SUI", "PEPE"]
        for coin in quick_coins:
            self._styled_btn(
                tags_frame, coin, lambda c=coin: self._toggle_coin_tag(c), "muted",
                font=("Consolas", 8, "bold"), side="left", padx=2
            )

        # 快捷模版行
        presets_row = tk.Frame(sec2, bg=self.CARD)
        presets_row.pack(fill="x", pady=(2, 2))

        self._styled_btn(
            presets_row, "主流四大", lambda: self._apply_preset_and_restart(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "热门公链", lambda: self._apply_preset_and_restart(["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "Meme板块", lambda: self._apply_preset_and_restart(["DOGEUSDT", "SHIBUSDT", "PEPEUSDT", "FLOKIUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "DeFi金融", lambda: self._apply_preset_and_restart(["UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        # --- 底部操作 ---
        action = tk.Frame(self.root, bg=self.BG)
        action.pack(fill="x", padx=14, pady=(8, 4))

        btn_box = tk.Frame(action, bg=self.BG)
        btn_box.pack(fill="x")

        self.launch_btn = tk.Button(
            btn_box, text="启动副屏", command=self._toggle_monitor,
            relief="flat", bd=0, padx=16, pady=7,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._apply_btn_style(self.launch_btn, "green")

        self.hide_btn = tk.Button(
            btn_box, text="最小化到托盘", command=self._hide_to_tray,
            relief="flat", bd=0, padx=12, pady=7,
            font=("Microsoft YaHei UI", 9),
        )
        self.hide_btn.pack(side="right")
        self._apply_btn_style(self.hide_btn, "muted")

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(
            self.root, textvariable=self.status_var, bg=self.BG, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.pack(pady=(2, 6))

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _parse_symbols(self, text):
        raw = text.replace(",", " ").replace(";", " ").replace("，", " ").replace("；", " ").split()
        res = []
        for sym in raw:
            sym = sym.strip().upper()
            if not sym:
                continue
            if not sym.endswith("USDT") and not sym.endswith("BUSD") and not sym.endswith("USDC"):
                sym += "USDT"
            if sym not in res:
                res.append(sym)
        return res if res else ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

    def _format_symbols_display(self, symbols):
        return ", ".join([s.replace("USDT", "") for s in symbols])

    def _apply_custom_symbols(self):
        parsed = self._parse_symbols(self.symbols_var.get())
        self.cfg["symbols"] = parsed
        self.symbols_var.set(self._format_symbols_display(parsed))
        save_config(self.cfg)
        self._set_status(f"已更新监控币种: {self.symbols_var.get()}", self.GREEN)
        if self._is_running():
            self._restart_monitor()

    def _toggle_coin_tag(self, coin_base):
        current = self._parse_symbols(self.symbols_var.get())
        target = coin_base.upper() + "USDT"
        if target in current:
            if len(current) > 1:
                current.remove(target)
        else:
            current.append(target)
        self.cfg["symbols"] = current
        self.symbols_var.set(self._format_symbols_display(current))
        save_config(self.cfg)
        self._set_status(f"已更新币种: {self.symbols_var.get()}", self.GREEN)
        if self._is_running():
            self._restart_monitor()

    def _load_to_ui(self):
        s = self.cfg["screen"]
        self.width_var.set(str(s["width"]))
        self.height_var.set(str(s["height"]))
        self.x_var.set(str(s["x_offset"]))
        self.y_var.set(str(s.get("y_offset", 0)))

        self.symbols_var.set(self._format_symbols_display(self.cfg.get("symbols", [])))
        self.topmost_var.set(self.cfg["display"].get("topmost", True))
        self.borderless_var.set(self.cfg["display"].get("borderless", True))
        self.theme_var.set(self.cfg["display"].get("theme", "cyberpunk"))
        self.weather_var.set(self.cfg.get("weather", {}).get("enabled", True))
        self._update_theme_btn_styles()

        if self.monitors:
            idx = self._find_matching_monitor(s)
            self.monitor_combo.current(idx)
            
        self._update_launch_btn()

    def _on_select_theme(self, theme_key):
        self.theme_var.set(theme_key)
        self._update_theme_btn_styles()
        self._auto_save()
        t_name = THEMES.get(theme_key, {}).get("name", theme_key)
        self._set_status(f"已切换风格: {t_name}", self.GREEN)

    def _update_theme_btn_styles(self):
        current = self.theme_var.get()
        for key, btn in self.theme_btns.items():
            if key == current:
                self._apply_btn_style(btn, "accent_border")
            else:
                self._apply_btn_style(btn, "muted")

    def _find_matching_monitor(self, screen):
        for i, m in enumerate(self.monitors):
            if m["x"] == screen["x_offset"] and m["y"] == screen.get("y_offset", 0):
                return i
        for i, m in enumerate(self.monitors):
            if not m["primary"]:
                return i
        return 0

    def _on_monitor_select(self, _=None):
        self._apply_monitor()

    def _apply_monitor(self):
        idx = self.monitor_combo.current()
        if idx < 0 or idx >= len(self.monitors):
            return
        m = self.monitors[idx]
        self.width_var.set(str(m["width"]))
        self.height_var.set(str(m["height"]))
        self.x_var.set(str(m["x"]))
        self.y_var.set(str(m["y"]))
        self._auto_save()
        self._set_status(f"设备切换成功并自动保存", self.GREEN)

    def _align_left_top(self):
        try:
            w = int(self.width_var.get())
        except ValueError:
            w = 1024
        self.x_var.set(str(-w))
        self.y_var.set("0")
        self._auto_save()
        self._set_status(f"位置已设为主屏左侧齐平 ({ -w }, 0)", self.GREEN)

    def _reset_to_detected_monitor(self):
        idx = self.monitor_combo.current()
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            self.width_var.set(str(m["width"]))
            self.height_var.set(str(m["height"]))
            self.x_var.set(str(m["x"]))
            self.y_var.set(str(m["y"]))
            self._auto_save()
            self._set_status("已重置为系统识别坐标", self.GREEN)

    def _on_entry_edited(self, _=None):
        self._auto_save()

    def _apply_preset_and_restart(self, symbols):
        self.cfg["symbols"] = symbols
        self.symbols_var.set(self._format_symbols_display(symbols))
        save_config(self.cfg)
        self._set_status(f"模版应用成功: {self.symbols_var.get()}", self.GREEN)
        
        # 只要副屏在运行，点击模版后立即平滑热重启应用新币种
        if self._is_running():
            self._restart_monitor()

    def _collect_config(self):
        try:
            width = int(self.width_var.get())
            height = int(self.height_var.get())
            x = int(self.x_var.get())
            y = int(self.y_var.get())
        except ValueError:
            return None

        # 保持其他参数不变
        symbols = self.cfg.get("symbols", ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
        update_cfg = self.cfg.get("update", {
            "realtime_ms": 5000,
            "klines_ms": 300000,
            "klines_interval": "15m",
            "klines_limit": 25
        })

        weather_cfg = self.cfg.get("weather", {
            "enabled": True,
            "location_name": "岳麓 · 湘熙水郡",
            "latitude": 28.13,
            "longitude": 112.95,
            "update_ms": 900000
        })
        weather_cfg["enabled"] = self.weather_var.get()

        return {
            "screen": {
                "width": width,
                "height": height,
                "x_offset": x,
                "y_offset": y,
            },
            "symbols": symbols,
            "update": update_cfg,
            "display": {
                "topmost": self.topmost_var.get(),
                "borderless": self.borderless_var.get(),
                "theme": self.theme_var.get(),
            },
            "weather": weather_cfg,
        }

    def _auto_save(self):
        cfg = self._collect_config()
        if cfg is None:
            return
        save_config(cfg)
        self.cfg = cfg
        if self._is_running():
            self._restart_monitor()

    def _start_monitor(self, silent=False):
        cfg = self._collect_config()
        if cfg is None:
            return False
        save_config(cfg)
        self.cfg = cfg
        try:
            self.monitor_proc = subprocess.Popen(
                [sys.executable, self._coin_script()],
                cwd=os.path.dirname(self._coin_script()),
            )
            self._save_pid(self.monitor_proc.pid)
            self._update_launch_btn()
            self.root.after(1000, self._poll_monitor)
            if not silent:
                self._set_status("副屏已成功启动", self.GREEN)
            return True
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动副屏进程: {str(e)}")
            self._set_status("副屏启动失败", self.RED)
            return False

    def _restart_monitor(self):
        # 优雅终止原进程
        if self.monitor_proc is not None:
            self.monitor_proc.terminate()
            try:
                self.monitor_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.monitor_proc.kill()
            self.monitor_proc = None
        else:
            self._terminate_pid(self._get_saved_pid())
            
        try:
            if os.path.exists(self._PID_FILE):
                os.remove(self._PID_FILE)
        except Exception:
            pass
            
        # 延迟半秒启动新进程
        self.root.after(500, lambda: self._start_monitor(silent=True))

    def _save_pid(self, pid):
        try:
            with open(self._PID_FILE, "w") as f:
                f.write(str(pid))
        except Exception:
            pass

    def _get_saved_pid(self):
        try:
            if os.path.exists(self._PID_FILE):
                with open(self._PID_FILE, "r") as f:
                    return int(f.read().strip())
        except Exception:
            pass
        return None

    def _is_pid_running(self, pid):
        if pid is None:
            return False
        try:
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                return exit_code.value == 259 # STILL_ACTIVE
        except Exception:
            pass
        return False

    def _terminate_pid(self, pid):
        if not pid:
            return
        try:
            PROCESS_TERMINATE = 0x0001
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                ctypes.windll.kernel32.TerminateProcess(handle, 0)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def _is_running(self):
        if self.monitor_proc is not None and self.monitor_proc.poll() is None:
            return True
        saved_pid = self._get_saved_pid()
        if self._is_pid_running(saved_pid):
            return True
        return False

    def _update_launch_btn(self):
        if self._is_running():
            self.launch_btn.config(text="关闭副屏")
            self._apply_btn_style(self.launch_btn, "red")
        else:
            self.launch_btn.config(text="启动副屏")
            self._apply_btn_style(self.launch_btn, "green")

    def _toggle_monitor(self):
        if self._is_running():
            self._set_status("正在关闭副屏仪表盘...", self.ACCENT)
            if self.monitor_proc is not None:
                self.monitor_proc.terminate()
                try:
                    self.monitor_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.monitor_proc.kill()
                self.monitor_proc = None
            else:
                self._terminate_pid(self._get_saved_pid())
            
            try:
                if os.path.exists(self._PID_FILE):
                    os.remove(self._PID_FILE)
            except Exception:
                pass

            # 写入桌面视图模式并退出
            set_view_mode("desktop")
            self._set_status("副屏已安全关闭", self.MUTED)
            self._update_launch_btn()
            return

        set_view_mode("dashboard")
        self._start_monitor()

    def _poll_monitor(self):
        is_running = self._is_running()
        self._update_launch_btn()
        if not is_running:
            self.monitor_proc = None
            self._set_status("副屏已退出", self.MUTED)
        
        if is_running:
            self.root.after(2000, self._poll_monitor)

    def _create_tray_image(self):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=14, fill="#0E1017", outline="#00F0FF", width=3)
        points = [(14, 44), (26, 32), (38, 38), (50, 18)]
        d.line(points, fill="#00FF88", width=4)
        for x, y in points:
            d.ellipse([x - 3, y - 3, x + 3, y + 3], fill="#00FF88")
        d.ellipse([47, 15, 53, 21], fill="#FFFFFF")
        return img

    def _init_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("显示管理面板", self._on_tray_show, default=True),
            pystray.MenuItem("启动/关闭副屏", self._on_tray_toggle),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出程序", self._on_tray_exit),
        )
        self.tray_icon = pystray.Icon(
            "CryptoMonitor",
            self._create_tray_image(),
            "副屏仪表盘管理",
            menu
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _on_tray_show(self, icon=None, item=None):
        self.root.after(0, self._restore_window)

    def _on_tray_toggle(self, icon=None, item=None):
        self.root.after(0, self._toggle_monitor)

    def _on_tray_exit(self, icon=None, item=None):
        self.root.after(0, self._exit_all)

    def _hide_to_tray(self):
        self.root.withdraw()

    def _restore_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.attributes("-topmost", False)
        self.root.focus_force()

    def _on_unmap(self, event):
        if event.widget == self.root and self.root.state() == "iconic":
            self.root.after(10, self._hide_to_tray)

    def _on_close(self):
        # 窗口关闭行为：如果副屏在运行，则最小化到右下角托盘保持后台服务；否则退出
        if self._is_running():
            self._hide_to_tray()
        else:
            self._exit_all()

    def _exit_all(self):
        try:
            if hasattr(self, "tray_icon") and self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass

        if self._is_running():
            if self.monitor_proc is not None:
                self.monitor_proc.terminate()
                try:
                    self.monitor_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.monitor_proc.kill()
                self.monitor_proc = None
            else:
                self._terminate_pid(self._get_saved_pid())
            try:
                if os.path.exists(self._PID_FILE):
                    os.remove(self._PID_FILE)
            except Exception:
                pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = CoinManager()
    app.run()
