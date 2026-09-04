import copy
import ctypes
import json
import os
import queue
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


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", _POINT),
        ("ptMaxPosition", _POINT),
        ("rcNormalPosition", _RECT),
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
        self.root.geometry("600x825")
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
        self.gaming_var = tk.BooleanVar(value=self.cfg.get("gaming_mode", {}).get("enabled", True))
        self.snap_other_windows_var = tk.BooleanVar(value=True)
        self.theme_btns = {}
        self.icon_queue = queue.Queue()
        self.search_queue = queue.Queue()

        self._build_ui()
        self._load_to_ui()
        self._init_tray()
        self.root.bind("<Unmap>", self._on_unmap)
        self._poll_icon_queue()
        self._poll_search_queue()

    def _label(self, parent, text, **kw):
        return tk.Label(
            parent, text=text, bg=self.CARD, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9), anchor="w", **kw,
        )

    def _section(self, parent, title):
        sec = tk.LabelFrame(
            parent, text=f" {title} ", bg=self.CARD, fg=self.ACCENT,
            font=("Microsoft YaHei UI", 9, "bold"), bd=1, relief="solid", padx=12, pady=8
        )
        sec.pack(fill="x", pady=(0, 8))
        return sec

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
        if pack_opts:
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
            self._style_button(btn, "#1A2333", "#223047", self.ACCENT, self.ACCENT)
        else: # normal
            self._style_button(btn, "#1A1A1A", "#2A2A2A", self.TEXT, self.TEXT)

    def _set_status(self, text, color=None):
        if color is None:
            color = self.MUTED
        self.status_label.config(fg=color)
        self.status_var.set(text)

    def _build_ui(self):
        root_pad = tk.Frame(self.root, bg=self.BG, padx=14, pady=10)
        root_pad.pack(fill="both", expand=True)

        # ==========================================
        # 1. 屏幕与显示设置
        # ==========================================
        sec1 = self._section(root_pad, "🖥️ 屏幕硬件与显示设置")

        # 目标屏幕下拉选择
        mon_row = tk.Frame(sec1, bg=self.CARD)
        mon_row.pack(fill="x", pady=(0, 4))
        self._label(mon_row, "目标屏幕:").pack(side="left", padx=(0, 4))

        mon_names = [f"[{i + 1}] {m['name']} ({m['width']}x{m['height']}) {'[主屏]' if m['primary'] else '[副屏]'}" for i, m in enumerate(self.monitors)]
        self.monitor_combo = ttk.Combobox(
            mon_row, values=mon_names, state="readonly", font=("Microsoft YaHei UI", 8), width=38
        )
        self.monitor_combo.pack(side="left", fill="x", expand=True, padx=(0, 2))
        self.monitor_combo.bind("<<ComboboxSelected>>", self._on_monitor_select)

        # 尺寸与坐标输入行
        pos_row = tk.Frame(sec1, bg=self.CARD)
        pos_row.pack(fill="x", pady=(2, 4))

        for label_text, var in [
            ("宽:", self.width_var),
            ("高:", self.height_var),
            ("X:", self.x_var),
            ("Y:", self.y_var),
        ]:
            self._label(pos_row, label_text).pack(side="left", padx=(0, 2))
            e = tk.Entry(
                pos_row, textvariable=var, width=5, bg="#1E1E1E", fg=self.TEXT,
                insertbackground=self.TEXT, bd=0, relief="flat", font=("Consolas", 9), justify="center"
            )
            e.pack(side="left", padx=(0, 8), ipady=1)
            e.bind("<FocusOut>", self._on_entry_edited)
            e.bind("<Return>", self._on_entry_edited)

        # 快捷分屏与对齐预设
        split_row = tk.Frame(sec1, bg=self.CARD)
        split_row.pack(fill="x", pady=(2, 2))

        self._styled_btn(
            split_row, "📺 监控占全屏", self._set_fullscreen, "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            split_row, "⬆️ 监控占上半屏", self._set_half_top, "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            split_row, "⬇️ 监控占下半屏", self._set_half_bottom, "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        # 副屏独立应用整屏排布工具（无需启动监控也可随时一键等分）
        win_tool_row = tk.Frame(sec1, bg=self.CARD)
        win_tool_row.pack(fill="x", pady=(2, 2))
        self._label(win_tool_row, "整屏排布:").pack(side="left", padx=(0, 4))
        self._styled_btn(
            win_tool_row, "🪟 自动等分", lambda: self.tile_subscreen_windows("auto"), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            win_tool_row, "⚏ 二等分", lambda: self.tile_subscreen_windows("half_2"), "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            win_tool_row, "☰ 三等分", lambda: self.tile_subscreen_windows("third_3"), "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            win_tool_row, "☷ 四等分", lambda: self.tile_subscreen_windows("quarter_4"), "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        tile_pref_row = tk.Frame(sec1, bg=self.CARD)
        tile_pref_row.pack(fill="x", pady=(2, 2))
        self._label(tile_pref_row, "多窗排布:").pack(side="left", padx=(0, 4))
        self.tile_mode_combo = ttk.Combobox(
            tile_pref_row,
            values=[
                "智能自适应 (自动规避重叠/视频宽屏优先)",
                "上下多行堆叠 (每窗满宽横向视口)",
                "左右多列并排 (竖向分栏垂直视口)"
            ],
            state="readonly", font=("Microsoft YaHei UI", 8)
        )
        self.tile_mode_combo.current(0)
        self.tile_mode_combo.pack(side="left", fill="x", expand=True, padx=(0, 2))

        # 桌面图标归拢工具（解决插拔/重排副屏导致的桌面图标跨屏漂移散落问题）
        icon_tool_row = tk.Frame(sec1, bg=self.CARD)
        icon_tool_row.pack(fill="x", pady=(2, 2))
        self._label(icon_tool_row, "桌面图标:").pack(side="left", padx=(0, 4))
        self._styled_btn(
            icon_tool_row, "🖥️ 全部归拢到主屏", self._gather_icons_to_primary, "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            icon_tool_row, "📱 全部归拢到副屏", self._gather_icons_to_secondary, "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        align_row = tk.Frame(sec1, bg=self.CARD)
        align_row.pack(fill="x", pady=(2, 3))

        self._styled_btn(
            align_row, "📐 主屏左侧齐平 (-W,0)", self._align_left_top, "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            align_row, "🔄 重置为系统识别坐标", self._reset_to_detected_monitor, "muted",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        # 显示属性复选框（分两行宽裕排版，彻底避免高DPI或增删选项导致的截断）
        opt_box = tk.Frame(sec1, bg=self.CARD)
        opt_box.pack(fill="x", pady=(2, 4))

        opt_row1 = tk.Frame(opt_box, bg=self.CARD)
        opt_row1.pack(fill="x", pady=(0, 2))

        self.topmost_var = tk.BooleanVar()
        self.borderless_var = tk.BooleanVar()

        tk.Checkbutton(
            opt_row1, text="窗口置顶", variable=self.topmost_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(0, 18))
        tk.Checkbutton(
            opt_row1, text="无边框模式", variable=self.borderless_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(0, 18))
        tk.Checkbutton(
            opt_row1, text="联动分屏吸附", variable=self.snap_other_windows_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT
        ).pack(side="left", padx=(0, 18))

        opt_row2 = tk.Frame(opt_box, bg=self.CARD)
        opt_row2.pack(fill="x", pady=(1, 0))

        tk.Checkbutton(
            opt_row2, text="岳麓区气象", variable=self.weather_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(0, 18))
        tk.Checkbutton(
            opt_row2, text="游戏防卡顿保护", variable=self.gaming_var,
            bg=self.CARD, fg=self.TEXT, selectcolor="#1A1A1A",
            activebackground=self.CARD, font=("Microsoft YaHei UI", 8),
            bd=0, activeforeground=self.TEXT, command=self._auto_save
        ).pack(side="left", padx=(0, 18))

        # 主题风格色系切换（优雅 2x3 网格排版，每个按钮 170px 宽，绝不截断）
        theme_sec = tk.Frame(sec1, bg=self.CARD)
        theme_sec.pack(fill="x", pady=(3, 2))
        self._label(theme_sec, "风格色系:").pack(side="top", anchor="w", pady=(0, 3))

        theme_grid = tk.Frame(theme_sec, bg=self.CARD)
        theme_grid.pack(fill="x")

        theme_keys = list(THEMES.keys())
        for idx, key in enumerate(theme_keys):
            r, c = divmod(idx, 3)
            t_info = THEMES[key]
            btn = self._styled_btn(
                theme_grid, t_info["name"], lambda k=key: self._on_select_theme(k), "muted",
                font=("Microsoft YaHei UI", 8, "bold")
            )
            btn.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
            theme_grid.grid_columnconfigure(c, weight=1)
            self.theme_btns[key] = btn

        # ==========================================
        # 2. 监控币种配置
        # ==========================================
        sec2 = self._section(root_pad, "🪙 监控币种配置 (全网检索与Meme币)")

        # 币种全网检索输入行 (支持 Robin/Solana/Base 链最新 Meme 币)
        search_row = tk.Frame(sec2, bg=self.CARD)
        search_row.pack(fill="x", pady=(2, 2))
        self._label(search_row, "🔍 检索添加:").pack(side="left", padx=(0, 4))

        self.search_entry = tk.Entry(
            search_row, bg="#1E1E1E", fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat", bd=0, font=("Microsoft YaHei UI", 9)
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=2)
        self.search_entry.insert(0, "PONS")
        self.search_entry.bind("<Return>", lambda e: self._on_search_crypto())

        self._styled_btn(
            search_row, "🔎 全网检索", self._on_search_crypto, "accent_border",
            font=("Microsoft YaHei UI", 8, "bold"), side="right"
        )

        # 搜索结果动态展示容器
        self.search_res_frame = tk.Frame(sec2, bg=self.CARD)
        self.search_res_frame.pack(fill="x", pady=(0, 2))

        # 自定义输入行
        custom_row = tk.Frame(sec2, bg=self.CARD)
        custom_row.pack(fill="x", pady=(2, 4))
        self._label(custom_row, "当前监控:").pack(side="left", padx=(0, 4))

        self.symbol_entry = tk.Entry(
            custom_row, textvariable=self.symbols_var, bg="#1E1E1E", fg=self.TEXT,
            insertbackground=self.TEXT, relief="flat", bd=0, font=("Consolas", 9)
        )
        self.symbol_entry.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=3)
        self.symbol_entry.bind("<Return>", lambda e: self._apply_custom_symbols())

        self._styled_btn(
            custom_row, "💾 保存应用", self._apply_custom_symbols, "accent",
            font=("Microsoft YaHei UI", 8, "bold"), side="right"
        )

        # 常用标签快速点选行 (纳入最新热门 Meme 币 PONS)
        tags_frame = tk.Frame(sec2, bg=self.CARD)
        tags_frame.pack(fill="x", pady=(2, 4))
        self._label(tags_frame, "点选加减:").pack(side="left", padx=(0, 4))

        quick_coins = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "PEPE", "PONS"]
        for coin in quick_coins:
            self._styled_btn(
                tags_frame, coin, lambda c=coin: self._toggle_coin_tag(c), "muted",
                font=("Consolas", 8, "bold"), side="left", padx=2
            )

        # 快捷模版行
        presets_row = tk.Frame(sec2, bg=self.CARD)
        presets_row.pack(fill="x", pady=(2, 2))

        self._styled_btn(
            presets_row, "🚀 主流四大", lambda: self._apply_preset_and_restart(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "🔥 热门Meme", lambda: self._apply_preset_and_restart(["PONSUSDT", "PEPEUSDT", "DOGEUSDT", "WIFUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "⛓️ 热门公链", lambda: self._apply_preset_and_restart(["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )
        self._styled_btn(
            presets_row, "🏦 DeFi金融", lambda: self._apply_preset_and_restart(["UNIUSDT", "AAVEUSDT", "LINKUSDT", "MKRUSDT"]), "accent_border",
            font=("Microsoft YaHei UI", 8), side="left", fill="x", expand=True, padx=2
        )

        # ==========================================
        # 3. 底部操作栏
        # ==========================================
        action = tk.Frame(root_pad, bg=self.BG)
        action.pack(fill="x", pady=(4, 2))

        btn_box = tk.Frame(action, bg=self.BG)
        btn_box.pack(fill="x")

        self.launch_btn = tk.Button(
            btn_box, text="⚡ 启动副屏", command=self._toggle_monitor,
            relief="flat", bd=0, padx=16, pady=7,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._apply_btn_style(self.launch_btn, "green")

        self.mate_btn = tk.Button(
            btn_box, text="🐾 启动桌宠", command=self._launch_digital_mate,
            relief="flat", bd=0, padx=14, pady=7,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.mate_btn.pack(side="left", padx=(0, 8))
        self._apply_btn_style(self.mate_btn, "accent_border")

        self.hide_btn = tk.Button(
            btn_box, text="👁️ 隐藏到托盘", command=self._hide_to_tray,
            relief="flat", bd=0, padx=14, pady=7,
            font=("Microsoft YaHei UI", 9),
        )
        self.hide_btn.pack(side="right")
        self._apply_btn_style(self.hide_btn, "muted")

        self.status_var = tk.StringVar(value="就绪")
        self.status_label = tk.Label(
            root_pad, textvariable=self.status_var, bg=self.BG, fg=self.MUTED,
            font=("Microsoft YaHei UI", 9), wraplength=560, justify="center"
        )
        self.status_label.pack(pady=(4, 0), fill="x")

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

    def _poll_search_queue(self):
        """定期接收后台币种搜索结果，保证 Tkinter 界面响应流畅"""
        try:
            while True:
                results = self.search_queue.get_nowait()
                self._render_search_results(results)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_search_queue)

    def _on_search_crypto(self):
        """启动后台线程进行跨平台全网币种检索（Binance + Gate.io Meme池）"""
        kw = self.search_entry.get().strip()
        if not kw:
            return

        for w in self.search_res_frame.winfo_children():
            w.destroy()
        loading_lbl = tk.Label(
            self.search_res_frame, text=f"⏳ 正在全网检索 '{kw}' (Binance + Gate.io Meme池)...",
            bg=self.CARD, fg=self.MUTED, font=("Microsoft YaHei UI", 8)
        )
        loading_lbl.pack(anchor="w", pady=(2, 2))

        def worker():
            res = self.search_crypto(kw)
            self.search_queue.put(res)

        threading.Thread(target=worker, daemon=True).start()

    def _render_search_results(self, results):
        """将检索结果渲染为可一键点选添加的候选条目"""
        for w in self.search_res_frame.winfo_children():
            w.destroy()

        if not results:
            lbl = tk.Label(
                self.search_res_frame, text="⚠️ 未检索到匹配币种，可检查代码或直接输入自定义代码",
                bg=self.CARD, fg=self.RED, font=("Microsoft YaHei UI", 8)
            )
            lbl.pack(anchor="w", pady=(2, 2))
            return

        for item in results[:3]:
            row = tk.Frame(self.search_res_frame, bg="#182234")
            row.pack(fill="x", pady=1)

            sym = item["symbol"]
            src = item.get("source", "")
            price = item.get("price", "0")
            change = item.get("change", "0")
            try:
                p_val = float(price)
                c_val = float(change)
                p_str = f"${p_val:.4f}" if p_val < 1 else f"${p_val:,.2f}"
                c_str = f"{c_val:+.2f}%"
                c_color = self.GREEN if c_val >= 0 else self.RED
            except Exception:
                p_str = f"${price}"
                c_str = f"{change}%"
                c_color = self.TEXT

            clean_name = sym.replace("USDT", "")
            info_text = f"✨ {clean_name} [{src}] {p_str} ({c_str})"
            tk.Label(
                row, text=info_text, bg="#182234", fg=c_color,
                font=("Microsoft YaHei UI", 8, "bold")
            ).pack(side="left", padx=(6, 8), pady=2)

            self._styled_btn(
                row, "➕ 添加", lambda s=sym: self._add_searched_symbol(s), "accent",
                font=("Microsoft YaHei UI", 7, "bold"), side="right", padx=4, pady=1
            )

    def _add_searched_symbol(self, sym):
        """将检索到的币种加入当前监控列表并立即生效"""
        current = self._parse_symbols(self.symbols_var.get())
        norm_sym = sym.upper().replace("_", "").replace("-", "")
        if not norm_sym.endswith("USDT") and not norm_sym.endswith("USDC"):
            norm_sym += "USDT"

        if norm_sym not in current:
            current.append(norm_sym)
            self.cfg["symbols"] = current
            self.symbols_var.set(self._format_symbols_display(current))
            save_config(self.cfg)
            self._set_status(f"已成功添加并保存币种: {norm_sym}", self.GREEN)
            for w in self.search_res_frame.winfo_children():
                w.destroy()
            if self._is_running():
                self._restart_monitor()
        else:
            self._set_status(f"币种 {norm_sym} 已在监控列表中", self.MUTED)

    @staticmethod
    def search_crypto(keyword):
        """全网跨源币种检索引擎（支持 Binance 官方主流与 Gate.io 全网最新 Meme 币）"""
        kw = keyword.strip().upper().replace("_", "").replace("-", "").replace("/", "")
        if not kw:
            return []
        base = kw[:-4] if kw.endswith("USDT") and len(kw) > 4 else kw
        results = []
        b_sym = base + "USDT"

        # 1. 探测 Binance 官方源
        try:
            r = requests.get(f"https://data-api.binance.vision/api/v3/ticker/24hr?symbol={b_sym}", timeout=2.5)
            if r.status_code == 200:
                d = r.json()
                results.append({
                    "symbol": b_sym,
                    "source": "Binance 官方",
                    "price": d.get("lastPrice", "0"),
                    "change": d.get("priceChangePercent", "0"),
                })
        except Exception:
            pass

        # 2. 探测 Gate.io 全网/Meme 源 (精准对齐)
        g_pair = base + "_USDT"
        try:
            r_g = requests.get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={g_pair}", timeout=2.5)
            if r_g.status_code == 200 and r_g.json():
                d = r_g.json()[0]
                tag = "Gate.io (Robin/Meme新币)" if not results else "Gate.io"
                results.append({
                    "symbol": b_sym,
                    "source": tag,
                    "price": d.get("last", "0"),
                    "change": d.get("change_percentage", "0"),
                })
        except Exception:
            pass

        # 3. 若无精准匹配，进行 Gate.io 现货币对模糊搜索
        if not results:
            try:
                r_all = requests.get("https://api.gateio.ws/api/v4/spot/tickers", timeout=3.5)
                if r_all.status_code == 200:
                    for item in r_all.json():
                        cp = item.get("currency_pair", "")
                        if cp.endswith("_USDT") and base in cp:
                            sym_name = cp.replace("_", "")
                            results.append({
                                "symbol": sym_name,
                                "source": "Gate.io (Meme)",
                                "price": item.get("last", "0"),
                                "change": item.get("change_percentage", "0"),
                            })
                        if len(results) >= 4:
                            break
            except Exception:
                pass

        return results

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
        self.gaming_var.set(self.cfg.get("gaming_mode", {}).get("enabled", True))
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

    def _set_fullscreen(self):
        idx = self.monitor_combo.current()
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            self.width_var.set(str(m["width"]))
            self.height_var.set(str(m["height"]))
            self.x_var.set(str(m["x"]))
            self.y_var.set(str(m["y"]))
            self._auto_save()
            self._set_status(f"已设为全屏显示 ({m['width']}x{m['height']})", self.GREEN)

    @staticmethod
    def _is_video_or_wide_window(title):
        keywords = [
            "播放", "video", "player", "potplayer", "bilibili", "哔哩哔哩",
            "爱奇艺", "腾讯视频", "优酷", "vlc", "mpc", "media player",
            "电影", "tv", "netflix", "youtube"
        ]
        lt = title.lower()
        return any(kw in lt for kw in keywords)

    @staticmethod
    def _position_single_window(hwnd, rx, ry, rw, rh):
        """精准安全地将单个窗口（含视频播放器、DirectX独占层、Electron客户端）调整并定位至指定区域"""
        user32 = ctypes.windll.user32
        root_hwnd = user32.GetAncestor(hwnd, 2) # GA_ROOT
        if root_hwnd:
            hwnd = root_hwnd

        # 1. 如果窗口处于最大化或全屏，先通过 WINDOWPLACEMENT 修改其持久化 normal 尺寸避免还原时弹回旧坐标
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            wp.rcNormalPosition.left = int(rx)
            wp.rcNormalPosition.top = int(ry)
            wp.rcNormalPosition.right = int(rx + rw)
            wp.rcNormalPosition.bottom = int(ry + rh)
            if wp.showCmd in (2, 3): # SW_SHOWMINIMIZED or SW_SHOWMAXIMIZED
                wp.showCmd = 1 # SW_SHOWNORMAL
            user32.SetWindowPlacement(hwnd, ctypes.byref(wp))

        if user32.IsZoomed(hwnd) or user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9) # SW_RESTORE

        # 2. 携带 SWP_FRAMECHANGED (0x0020) 强制重绘 DirectComposition / D3D 交换链与非客户区视口
        flags = 0x0020 | 0x0004 | 0x0040 # SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, 0, int(rx), int(ry), int(rw), int(rh), flags)

        # 再次确认获取实际被系统允许的宽度（用于检测是否存在 minTrackWidth 限制）
        rect = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.right - rect.left

    def _calculate_tiling_slots(self, candidates, tx, ty, tw, th):
        """计算 n 个窗口在 (tx, ty, tw, th) 区域内的无重叠平铺网格坐标"""
        n = len(candidates)
        if n <= 0:
            return []
        if n == 1:
            return [(tx, ty, tw, th)]

        mode_idx = 0
        if hasattr(self, "tile_mode_combo"):
            mode_idx = self.tile_mode_combo.current()

        # mode_idx: 0=智能自适应, 1=强制上下双行堆叠, 2=强制左右双列并排
        if n == 2:
            force_rows = (mode_idx == 1)
            force_cols = (mode_idx == 2)

            if not force_cols and not force_rows:
                # 智能自适应逻辑：
                # 1. 若半宽 (tw // 2) 小于 850px（如 2K 竖屏 1440 宽分半只有 720px），
                #    而窗口包含视频播放器（B站、爱优腾、PotPlayer 等有 800px+ 最小宽度限制且需要 16:9 横向宽屏视口），
                #    自动采用「上下双行堆叠」，使每个窗口享有 1440 满宽度，彻底规避尺寸溢出和画面拉伸！
                has_video_app = any(self._is_video_or_wide_window(title) for _, title in candidates)
                if has_video_app and (tw // 2 < 850):
                    force_rows = True
                elif tw >= th:
                    force_cols = True
                else:
                    force_rows = True

            if force_rows:
                # 上下双行堆叠（适合视频播放器、16:9宽屏应用，每窗享受 1440 满宽度）
                h1 = th // 2
                h2 = th - h1
                return [(tx, ty, tw, h1), (tx, ty + h1, tw, h2)]
            else:
                # 左右双列并排（适合长网页、微信等垂直窗口）
                w1 = tw // 2
                w2 = tw - w1
                return [(tx, ty, w1, th), (tx + w1, ty, w2, th)]

        if n == 3:
            has_video_app = any(self._is_video_or_wide_window(title) for _, title in candidates)
            if has_video_app or (tw < th) or mode_idx == 1:
                # 上半部主窗口（全宽横向，视频播放器最佳视口），下半部左右分 2 个小窗
                h1 = th // 2
                h2 = th - h1
                w1 = tw // 2
                w2 = tw - w1
                return [
                    (tx, ty, tw, h1),
                    (tx, ty + h1, w1, h2),
                    (tx + w1, ty + h1, w2, h2),
                ]
            else:
                # 经典主从：左侧主窗口，右侧上下分 2 个小窗
                w1 = tw // 2
                w2 = tw - w1
                h1 = th // 2
                h2 = th - h1
                return [
                    (tx, ty, w1, th),
                    (tx + w1, ty, w2, h1),
                    (tx + w1, ty + h1, w2, h2),
                ]

        if n == 4:
            # 2x2 四宫格对称平铺
            w1 = tw // 2
            w2 = tw - w1
            h1 = th // 2
            h2 = th - h1
            return [
                (tx, ty, w1, h1),
                (tx + w1, ty, w2, h1),
                (tx, ty + h1, w1, h2),
                (tx + w1, ty + h1, w2, h2),
            ]

        # n >= 5: 自适应行列动态网格
        cols = 3 if tw >= th else 2
        rows = (n + cols - 1) // cols
        cell_h = th // rows
        slots = []
        for i in range(n):
            r = i // cols
            c = i % cols
            items_in_this_row = min(cols, n - r * cols)
            cell_w = tw // items_in_this_row
            rx = tx + c * cell_w
            ry = ty + r * cell_h
            rw = cell_w if c < items_in_this_row - 1 else (tw - c * cell_w)
            rh = cell_h if r < rows - 1 else (th - r * cell_h)
            slots.append((rx, ry, rw, rh))
        return slots

    def _arrange_other_windows_to_rect(self, tx, ty, tw, th, monitor_rect):
        """自动寻找副屏上的其他窗口（或当前前台窗口），并无重叠平铺排布到指定区域"""
        user32 = ctypes.windll.user32
        exclude_hwnds = set()
        try:
            mgr_hwnd = int(self.root.wm_frame(), 16)
            exclude_hwnds.add(mgr_hwnd)
        except Exception:
            pass

        mx, my, mw, mh = monitor_rect
        candidates = []
        seen_hwnds = set()

        def enum_cb(hwnd, lparam):
            if hwnd in exclude_hwnds or hwnd in seen_hwnds:
                return 1
            if not user32.IsWindowVisible(hwnd):
                return 1
            style = user32.GetWindowLongW(hwnd, -16) # GWL_STYLE
            if style & 0x40000000: # WS_CHILD
                return 1
            ex_style = user32.GetWindowLongW(hwnd, -20) # GWL_EXSTYLE
            if ex_style & 0x00000080: # WS_EX_TOOLWINDOW
                return 1

            title_len = user32.GetWindowTextLengthW(hwnd)
            if title_len == 0:
                return 1
            buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(hwnd, buf, title_len + 1)
            title = buf.value

            if title in ("Program Manager", "Settings", "Windows 输入体验", "副屏显示管理", "任务切换", "Snap Assist"):
                return 1
            if "副屏" in title or "CryptoSubscreen" in title:
                return 1

            rect = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 100 or h < 100:
                return 1

            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2

            if mx <= cx <= mx + mw and my <= cy <= my + mh:
                candidates.append((hwnd, title))
                seen_hwnds.add(hwnd)
            return 1

        _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(_WNDENUMPROC(enum_cb), 0)

        # 如果副屏上无已放置窗口，尝试寻找用户前台操作窗口
        if not candidates:
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd and fg_hwnd not in exclude_hwnds and fg_hwnd not in seen_hwnds and user32.IsWindowVisible(fg_hwnd):
                title_len = user32.GetWindowTextLengthW(fg_hwnd)
                if title_len > 0:
                    buf = ctypes.create_unicode_buffer(title_len + 1)
                    user32.GetWindowTextW(fg_hwnd, buf, title_len + 1)
                    title = buf.value
                    if title and title not in ("副屏显示管理", "Program Manager"):
                        candidates.append((fg_hwnd, title))
                        seen_hwnds.add(fg_hwnd)

        # 最多平铺前 6 个活动窗口，避免极端窗口过多导致单元格过小
        tile_candidates = candidates[:6]
        slots = self._calculate_tiling_slots(tile_candidates, tx, ty, tw, th)

        arranged_titles = []
        overflow_detected = False

        for (hwnd, title), (rx, ry, rw, rh) in zip(tile_candidates, slots):
            actual_w = self._position_single_window(hwnd, rx, ry, rw, rh)
            arranged_titles.append(title[:10])
            # 如果实际宽度明显大于分配的宽度（说明被应用自身 minTrackWidth 强制撑大导致重叠），触发自动回退保护
            if actual_w > rw + 60:
                overflow_detected = True

        # 如果在左右双列模式下检测到重叠溢出，自动切换为上下双行堆叠（每窗享受满宽，绝不重叠）
        if overflow_detected and len(tile_candidates) == 2 and slots[0][2] < tw:
            h1 = th // 2
            h2 = th - h1
            fallback_slots = [(tx, ty, tw, h1), (tx, ty + h1, tw, h2)]
            for (hwnd, _), (rx, ry, rw, rh) in zip(tile_candidates, fallback_slots):
                self._position_single_window(hwnd, rx, ry, rw, rh)

        return arranged_titles

    def tile_subscreen_windows(self, mode="auto"):
        """独立将副屏上打开的所有应用进行整屏自动排布（支持二等分、三等分、四等分等，无需启动币种监控）"""
        idx = self.monitor_combo.current()
        if idx < 0 or idx >= len(self.monitors):
            return
        m = self.monitors[idx]
        mx, my, mw, mh = m["x"], m["y"], m["width"], m["height"]

        user32 = ctypes.windll.user32
        exclude_hwnds = set()
        try:
            mgr_hwnd = int(self.root.wm_frame(), 16)
            exclude_hwnds.add(mgr_hwnd)
        except Exception:
            pass

        candidates = []
        seen_hwnds = set()

        def enum_cb(hwnd, lparam):
            if hwnd in exclude_hwnds or hwnd in seen_hwnds:
                return 1
            if not user32.IsWindowVisible(hwnd):
                return 1
            style = user32.GetWindowLongW(hwnd, -16) # GWL_STYLE
            if style & 0x40000000: # WS_CHILD
                return 1
            ex_style = user32.GetWindowLongW(hwnd, -20) # GWL_EXSTYLE
            if ex_style & 0x00000080: # WS_EX_TOOLWINDOW
                return 1

            title_len = user32.GetWindowTextLengthW(hwnd)
            if title_len == 0:
                return 1
            buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(hwnd, buf, title_len + 1)
            title = buf.value

            if title in ("Program Manager", "Settings", "Windows 输入体验", "副屏显示管理", "任务切换", "Snap Assist"):
                return 1
            if "副屏" in title or "CryptoSubscreen" in title:
                return 1

            rect = _RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w < 100 or h < 100:
                return 1

            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2

            if mx <= cx <= mx + mw and my <= cy <= my + mh:
                candidates.append((hwnd, title))
                seen_hwnds.add(hwnd)
            return 1

        _WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(_WNDENUMPROC(enum_cb), 0)

        # 若副屏当前没有窗口，尝试拉取当前主屏活动前台窗口
        if not candidates:
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd and fg_hwnd not in exclude_hwnds and fg_hwnd not in seen_hwnds and user32.IsWindowVisible(fg_hwnd):
                title_len = user32.GetWindowTextLengthW(fg_hwnd)
                if title_len > 0:
                    buf = ctypes.create_unicode_buffer(title_len + 1)
                    user32.GetWindowTextW(fg_hwnd, buf, title_len + 1)
                    title = buf.value
                    if title and title not in ("副屏显示管理", "Program Manager"):
                        candidates.append((fg_hwnd, title))
                        seen_hwnds.add(fg_hwnd)

        if not candidates:
            self._set_status("副屏暂未检测到已打开的应用窗口", self.MUTED)
            return

        is_vertical = (mh > mw)
        pref_idx = self.tile_mode_combo.current() if hasattr(self, "tile_mode_combo") else 0

        # 根据指定模式或当前窗口数量计算整屏槽位
        if mode == "half_2":
            target_apps = candidates[:2]
            # 二等分
            if pref_idx == 1 or (pref_idx == 0 and is_vertical):
                # 上下二等分（每窗满宽横向，避免 2K 竖屏宽度溢出）
                h1 = mh // 2
                h2 = mh - h1
                slots = [(mx, my, mw, h1), (mx, my + h1, mw, h2)]
            else:
                # 左右二等分
                w1 = mw // 2
                w2 = mw - w1
                slots = [(mx, my, w1, mh), (mx + w1, my, w2, mh)]
            desc_text = "二等分"
        elif mode == "third_3":
            target_apps = candidates[:3]
            # 三等分
            if pref_idx == 1 or (pref_idx == 0 and is_vertical):
                # 上中下三行堆叠 (1440 满宽 x 853 高，完美 16:9 比例)
                h1 = mh // 3
                h2 = mh // 3
                h3 = mh - h1 - h2
                slots = [(mx, my, mw, h1), (mx, my + h1, mw, h2), (mx, my + h1 + h2, mw, h3)]
            elif pref_idx == 2 or (not is_vertical and pref_idx == 0):
                # 左右三等分
                w1 = mw // 3
                w2 = mw // 3
                w3 = mw - w1 - w2
                slots = [(mx, my, w1, mh), (mx + w1, my, w2, mh), (mx + w1 + w2, my, w3, mh)]
            else:
                slots = self._calculate_tiling_slots(target_apps, mx, my, mw, mh)
            desc_text = "三等分"
        elif mode == "quarter_4":
            target_apps = candidates[:4]
            # 四等分：对称 2x2
            w1 = mw // 2
            w2 = mw - w1
            h1 = mh // 2
            h2 = mh - h1
            slots = [
                (mx, my, w1, h1),
                (mx + w1, my, w2, h1),
                (mx, my + h1, w1, h2),
                (mx + w1, my + h1, w2, h2),
            ]
            desc_text = "四等分"
        else: # auto
            target_apps = candidates[:6]
            slots = self._calculate_tiling_slots(target_apps, mx, my, mw, mh)
            desc_text = f"自动等分({len(target_apps)}窗)"

        arranged_titles = []
        for (hwnd, title), (rx, ry, rw, rh) in zip(target_apps, slots):
            self._position_single_window(hwnd, rx, ry, rw, rh)
            arranged_titles.append(title[:8])

        self._set_status(f"副屏应用已按 [{desc_text}] 整屏排布: {', '.join(arranged_titles)}", self.GREEN)

    def _poll_icon_queue(self):
        """定期从后台队列取出图标归拢结果，保证 Tkinter 线程安全"""
        try:
            while True:
                success, cnt, target_name = self.icon_queue.get_nowait()
                if success:
                    self._set_status(f"已将全部 {cnt} 个桌面图标整齐归拢至{target_name}！", self.GREEN)
                else:
                    self._set_status("归拢图标失败，未检测到系统桌面视图", self.RED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_icon_queue)

    def _gather_icons_to_primary(self):
        """将全部桌面图标整齐归拢到主屏（后台线程执行，确保 Win32 Desktop 权限且不卡顿界面）"""
        pri_m = next((m for m in self.monitors if m.get("primary")), self.monitors[0])
        self._set_status("正在归拢桌面图标到主屏...", self.MUTED)

        def worker():
            cnt, success = self._gather_desktop_icons_to_monitor(pri_m)
            self.icon_queue.put((success, cnt, "主屏"))

        threading.Thread(target=worker, daemon=True).start()

    def _gather_icons_to_secondary(self):
        """将全部桌面图标整齐归拢到副屏（后台线程执行，确保 Win32 Desktop 权限且不卡顿界面）"""
        sec_m = next((m for m in self.monitors if not m.get("primary")), None)
        if not sec_m:
            idx = self.monitor_combo.current()
            if 0 <= idx < len(self.monitors):
                sec_m = self.monitors[idx]
            else:
                self._set_status("未检测到副屏显示器", self.RED)
                return

        self._set_status("正在归拢桌面图标到副屏...", self.MUTED)

        def worker():
            cnt, success = self._gather_desktop_icons_to_monitor(sec_m)
            self.icon_queue.put((success, cnt, "副屏"))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _gather_desktop_icons_to_monitor(target_monitor):
        """精准将桌面所有图标整齐排列至指定显示器（支持主屏/副屏，彻底解除 Windows 跨屏漂移散落）"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        desk = user32.OpenDesktopW("Default", 0, False, 0x00020000 | 0x01FF)
        if desk:
            user32.SetThreadDesktop(desk)

        progman = user32.FindWindowW("Progman", None)
        shell_view = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
        if not shell_view:
            worker = 0
            while True:
                worker = user32.FindWindowExW(0, worker, "WorkerW", None)
                if not worker:
                    break
                shell_view = user32.FindWindowExW(worker, 0, "SHELLDLL_DefView", None)
                if shell_view:
                    break

        if not shell_view:
            return 0, False

        listview = user32.FindWindowExW(shell_view, 0, "SysListView32", None)
        if not listview:
            return 0, False

        count = user32.SendMessageW(listview, 0x1004, 0, 0)
        if count <= 0:
            return 0, True

        # 关闭 Windows 自带的 LVS_AUTOARRANGE（0x0100），防止 Windows 跨屏虚拟高度再次扰乱图标
        style = user32.GetWindowLongW(listview, -16)
        user32.SetWindowLongW(listview, -16, style & ~0x0100)

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(listview, ctypes.byref(pid))
        h_proc = kernel32.OpenProcess(0x0008 | 0x0010 | 0x0020, False, pid.value)
        if not h_proc:
            return 0, False

        remote_buf = kernel32.VirtualAllocEx(h_proc, None, ctypes.sizeof(_POINT), 0x1000, 0x04)
        if not remote_buf:
            kernel32.CloseHandle(h_proc)
            return 0, False

        # 将屏幕物理坐标转换为 SysListView32 的 Client 客户区坐标
        pt_tl = _POINT(target_monitor["x"], target_monitor["y"])
        pt_br = _POINT(target_monitor["x"] + target_monitor["width"], target_monitor["y"] + target_monitor["height"])
        user32.ScreenToClient(listview, ctypes.byref(pt_tl))
        user32.ScreenToClient(listview, ctypes.byref(pt_br))

        start_x = pt_tl.x + 20
        start_y = pt_tl.y + 20
        max_y = pt_br.y - 120
        dx = 112
        dy = 125

        cur_x = start_x
        cur_y = start_y
        pt = _POINT()

        for i in range(count):
            pt.x = cur_x
            pt.y = cur_y
            kernel32.WriteProcessMemory(h_proc, remote_buf, ctypes.byref(pt), ctypes.sizeof(_POINT), None)
            user32.SendMessageW(listview, 0x1031, i, remote_buf) # LVM_SETITEMPOSITION32
            cur_y += dy
            if cur_y + dy > max_y:
                cur_y = start_y
                cur_x += dx

        user32.InvalidateRect(listview, None, True)
        user32.UpdateWindow(listview)

        kernel32.VirtualFreeEx(h_proc, remote_buf, 0, 0x8000)
        kernel32.CloseHandle(h_proc)
        return count, True

    def _set_half_top(self):
        idx = self.monitor_combo.current()
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            half_h = m["height"] // 2
            self.width_var.set(str(m["width"]))
            self.height_var.set(str(half_h))
            self.x_var.set(str(m["x"]))
            self.y_var.set(str(m["y"]))
            self._auto_save()

            other_str = ""
            if self.snap_other_windows_var.get():
                arranged = self._arrange_other_windows_to_rect(
                    m["x"], m["y"] + half_h, m["width"], half_h,
                    (m["x"], m["y"], m["width"], m["height"])
                )
                if arranged:
                    if len(arranged) == 1:
                        other_str = f"，并联动吸附 [{arranged[0]}] 至下半屏"
                    else:
                        other_str = f"，并自动平铺 {len(arranged)} 个应用至下半屏"

            self._set_status(f"已设为上半屏 ({m['width']}x{half_h}){other_str}", self.GREEN)

    def _set_half_bottom(self):
        idx = self.monitor_combo.current()
        if 0 <= idx < len(self.monitors):
            m = self.monitors[idx]
            half_h = m["height"] // 2
            self.width_var.set(str(m["width"]))
            self.height_var.set(str(half_h))
            self.x_var.set(str(m["x"]))
            self.y_var.set(str(m["y"] + half_h))
            self._auto_save()

            other_str = ""
            if self.snap_other_windows_var.get():
                arranged = self._arrange_other_windows_to_rect(
                    m["x"], m["y"], m["width"], half_h,
                    (m["x"], m["y"], m["width"], m["height"])
                )
                if arranged:
                    if len(arranged) == 1:
                        other_str = f"，并联动吸附 [{arranged[0]}] 至上半屏"
                    else:
                        other_str = f"，并自动平铺 {len(arranged)} 个应用至上半屏"

            self._set_status(f"已设为下半屏 ({m['width']}x{half_h}){other_str}", self.GREEN)

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
            "location_name": "长沙 · 岳麓区",
            "latitude": 28.13,
            "longitude": 112.95,
            "update_ms": 600000
        })
        weather_cfg["enabled"] = self.weather_var.get()

        gaming_cfg = self.cfg.get("gaming_mode", {
            "enabled": True,
            "detect_lol": True,
            "detect_fullscreen": True,
            "pause_price_flash": True
        })
        gaming_cfg["enabled"] = self.gaming_var.get()

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
            "gaming_mode": gaming_cfg,
            "proxy": self.cfg.get("proxy", ""),
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

    def _launch_digital_mate(self):
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        candidates = [
            os.path.join(desktop, "DigitalMate.exe.lnk"),
            os.path.join(desktop, "DigitalMate.lnk"),
            os.path.join(desktop, "DigitalMate.exe"),
            r"E:\download\BaiduNetdiskDownload\XJ03886\XJ03886\Digital Mate\DigitalMate.exe",
        ]
        target = None
        for path in candidates:
            if os.path.exists(path):
                target = path
                break

        if not target:
            try:
                for root, _, files in os.walk(desktop):
                    for f in files:
                        if "digitalmate" in f.lower() and (f.endswith(".lnk") or f.endswith(".exe")):
                            target = os.path.join(root, f)
                            break
                    if target:
                        break
            except Exception:
                pass

        if target:
            try:
                os.startfile(target)
                self._set_status("已启动桌宠 DigitalMate", self.GREEN)
            except Exception as e:
                self._set_status(f"启动桌宠失败: {e}", self.RED)
                messagebox.showerror("启动失败", f"启动桌宠程序失败:\n{e}")
        else:
            self._set_status("未找到桌宠 DigitalMate.exe", self.RED)
            messagebox.showwarning("未找到程序", "未在桌面或默认路径找到 DigitalMate.exe 或其快捷方式。")

    def _init_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("显示管理面板", self._on_tray_show, default=True),
            pystray.MenuItem("启动/关闭副屏", self._on_tray_toggle),
            pystray.MenuItem("🐾 启动桌宠 (DigitalMate)", lambda icon, item: self.root.after(0, self._launch_digital_mate)),
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
