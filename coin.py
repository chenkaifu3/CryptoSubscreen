import copy
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
import webbrowser

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import psutil
import requests

from config import THEMES, load_config
from control import get_view_mode

log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coin.log")
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)d) %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
GATEIO_HOSTS = ["https://api.gateio.ws"]
REQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

WMO_WEATHER_MAP = {
    0: ("晴", "晴天"),
    1: ("少云", "晴间多云"),
    2: ("多云", "多云"),
    3: ("阴", "阴天"),
    45: ("雾", "有雾"),
    48: ("浓雾", "浓雾"),
    51: ("微雨", "毛毛细雨"),
    53: ("细雨", "毛毛雨"),
    55: ("小雨", "持续小雨"),
    61: ("小雨", "小雨"),
    63: ("中雨", "中雨"),
    65: ("大雨", "大雨"),
    71: ("小雪", "小雪"),
    73: ("中雪", "中雪"),
    75: ("大雪", "大雪"),
    80: ("阵雨", "阵雨"),
    81: ("中阵雨", "中阵雨"),
    82: ("暴雨", "暴雨"),
    95: ("雷雨", "雷阵雨"),
    96: ("雷雹", "雷雨伴冰雹"),
}

FNG_CLASSIFICATION_MAP = {
    "Extreme Fear": ("极度恐慌", "#FF2244"),
    "Fear": ("恐慌", "#F97316"),
    "Neutral": ("中性", "#EAB308"),
    "Greed": ("贪婪", "#10B981"),
    "Extreme Greed": ("极度贪婪", "#00FF9D"),
}


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _PDH_FMT_COUNTERVALUE(ctypes.Structure):
    _fields_ = [
        ("CStatus", ctypes.c_ulong),
        ("doubleValue", ctypes.c_double),
    ]


class WindowsCPUCollector:
    """与 Windows 任务管理器（Task Manager）完全一致的 CPU 总利用率采集器"""
    def __init__(self):
        self.hQuery = ctypes.c_void_p()
        self.hCounter = ctypes.c_void_p()
        self.initialized = False
        try:
            pdh = ctypes.windll.pdh
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.hQuery)) == 0:
                # 任务管理器指标: % Processor Utility (所有内核总利用率)
                res = pdh.PdhAddEnglishCounterW(
                    self.hQuery, "\\Processor Information(_Total)\\% Processor Utility", 0, ctypes.byref(self.hCounter)
                )
                if res != 0:
                    res = pdh.PdhAddEnglishCounterW(
                        self.hQuery, "\\Processor(_Total)\\% Processor Time", 0, ctypes.byref(self.hCounter)
                    )
                if res == 0:
                    pdh.PdhCollectQueryData(self.hQuery)
                    self.initialized = True
        except Exception as e:
            logger.warning("PDH 初始化失败，将回退到 psutil: %s", e)
            self.initialized = False

    def get_cpu_percent(self):
        if self.initialized:
            try:
                pdh = ctypes.windll.pdh
                pdh.PdhCollectQueryData(self.hQuery)
                val = _PDH_FMT_COUNTERVALUE()
                if pdh.PdhGetFormattedCounterValue(self.hCounter, 0x00000200, None, ctypes.byref(val)) == 0:
                    return max(0.0, min(100.0, val.doubleValue))
            except Exception:
                pass
        return psutil.cpu_percent(interval=None)


def check_gaming_status():
    """轻量级检测前台游戏与英雄联盟对局状态"""
    # 1. 常见游戏进程识别
    game_processes = {
        "league of legends.exe", "leagueclientux.exe", "game.exe",
        "valorant.exe", "cs2.exe", "dota2.exe", "gta5.exe",
        "overwatch.exe", "genshinimpact.exe", "starrail.exe",
        "naraka.exe", "blackmythwukong.exe", "crossfire.exe"
    }
    try:
        for p in psutil.process_iter(["name"]):
            pname = (p.info.get("name") or "").lower()
            if pname in game_processes:
                return True
    except Exception:
        pass

    # 2. 前台全屏独占窗口检测 (避免与主屏全屏游戏竞争 DWM 资源)
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if hwnd:
            rect = _RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            if (rect.right - rect.left >= sw) and (rect.bottom - rect.top >= sh):
                return True
    except Exception:
        pass
    return False


def fetch_fear_greed(proxies=None):
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        kwargs = {"timeout": 4, "headers": REQ_HEADERS}
        if proxies:
            kwargs["proxies"] = proxies
        res = requests.get(url, **kwargs).json()
        item = res.get("data", [{}])[0]
        val = item.get("value", "50")
        cls_raw = item.get("value_classification", "Neutral")
        cls_cn, cls_col = FNG_CLASSIFICATION_MAP.get(cls_raw, (cls_raw, "#EAB308"))
        return {"value": val, "text": cls_cn, "color": cls_col}
    except Exception as e:
        logger.warning("恐慌贪婪指数获取失败: %s", e)
        return None


def fetch_weather(lat=28.13, lon=112.95, proxies=None):
    # 1. 主节点 Open-Meteo 高精度气象 API
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m"
            "&daily=temperature_2m_max,temperature_2m_min&timezone=Asia%2FShanghai"
        )
        kwargs = {"timeout": 4, "headers": REQ_HEADERS}
        if proxies:
            kwargs["proxies"] = proxies
        res = requests.get(url, **kwargs).json()
        curr = res.get("current", {})
        daily = res.get("daily", {})
        code = curr.get("weather_code", 0)
        tag, desc = WMO_WEATHER_MAP.get(code, ("多云", "多云"))
        temp = curr.get("temperature_2m", 0.0)
        feels = curr.get("apparent_temperature", temp)
        humidity = curr.get("relative_humidity_2m", 0)
        wind = curr.get("wind_speed_10m", 0.0)
        t_max_list = daily.get("temperature_2m_max", [])
        t_min_list = daily.get("temperature_2m_min", [])
        t_max = t_max_list[0] if t_max_list else temp
        t_min = t_min_list[0] if t_min_list else temp
        return {
            "temp": f"{temp:.1f}°C",
            "feels": f"{feels:.1f}°C",
            "humidity": f"{humidity}%",
            "wind": f"{wind:.1f}km/h",
            "desc": desc,
            "tag": tag,
            "range": f"{t_min:.0f}° ~ {t_max:.0f}°C",
            "time": curr.get("time", "")[-5:]
        }
    except Exception as e:
        logger.warning("Open-Meteo请求失败, 尝试国内气象备用源: %s", e)

    # 2. 备用节点 中国气象公开数据 (长沙市岳麓区)
    try:
        url = "http://t.weather.itboy.net/api/weather/city/101250101"
        res = requests.get(url, timeout=4, headers=REQ_HEADERS).json()
        data = res.get("data", {})
        forecast = data.get("forecast", [{}])[0]
        temp_val = float(data.get("wendu", 25.0))
        shidu = data.get("shidu", "60%")
        type_str = forecast.get("type", "多云")
        high_s = forecast.get("high", "高温 30℃").replace("高温", "").replace("℃", "").strip()
        low_s = forecast.get("low", "低温 20℃").replace("低温", "").replace("℃", "").strip()
        return {
            "temp": f"{temp_val:.1f}°C",
            "feels": f"{temp_val:.1f}°C",
            "humidity": shidu,
            "wind": data.get("fx", "") + " " + data.get("fl", ""),
            "desc": type_str,
            "tag": type_str,
            "range": f"{low_s}° ~ {high_s}°C",
            "time": res.get("time", "")[-5:]
        }
    except Exception as e:
        logger.warning("备用天气源请求失败: %s", e)
        return None


class BackendWorker:
    """统一常驻守护工作线程：负责所有网络拉取与硬件监控，零临时线程开销"""
    def __init__(self, monitor_app):
        self.app = monitor_app
        self.running = True
        self.cpu_collector = WindowsCPUCollector()

        self.last_ticker_time = 0.0
        self.last_klines_time = 0.0
        self.last_hw_time = 0.0
        self.last_weather_time = 0.0
        self.last_fng_time = 0.0
        self.last_game_check_time = 0.0
        self.symbol_sources = {}

        self.thread = threading.Thread(target=self._run_loop, daemon=True, name="DashboardWorker")
        self.thread.start()

    def stop(self):
        self.running = False

    def _run_loop(self):
        while self.running:
            now = time.time()
            is_gaming = self.app.is_gaming
            gaming_enabled = self.app.gaming_mode_enabled

            # 1. 游戏状态检测 (每 2.5 秒)
            if gaming_enabled and (now - self.last_game_check_time >= 2.5):
                self.last_game_check_time = now
                detected = check_gaming_status()
                if detected != self.app.is_gaming:
                    self.app.is_gaming = detected
                    self.app.safe_ui_call(self.app.on_gaming_status_changed, detected)

            # 2. 硬件采集 (正常 2 秒 / 游戏对局 6 秒)
            hw_interval = 6.0 if is_gaming else 2.0
            if now - self.last_hw_time >= hw_interval:
                self.last_hw_time = now
                try:
                    cpu_val = self.cpu_collector.get_cpu_percent()
                    ram_val = psutil.virtual_memory().percent
                    hw_stats = {"cpu": f"{cpu_val:.0f}%", "ram": f"{ram_val:.0f}%"}
                    self.app.safe_ui_call(self.app.apply_hardware_stats, hw_stats)
                except Exception:
                    pass

            # 3. 实时行情拉取 (正常 3~5 秒 / 游戏对局 8 秒)
            ticker_interval = 8.0 if is_gaming else (self.app.realtime_ms / 1000.0)
            if now - self.last_ticker_time >= ticker_interval:
                self.last_ticker_time = now
                self._fetch_ticker()

            # 4. K线数据拉取 (每 300 秒)
            klines_interval = self.app.klines_ms / 1000.0
            if now - self.last_klines_time >= klines_interval or self.last_klines_time == 0.0:
                self.last_klines_time = now
                self._fetch_klines()

            # 5. 天气数据拉取 (每 600 秒)
            weather_interval = self.app.weather_ms / 1000.0
            if self.app.weather_enabled and (now - self.last_weather_time >= weather_interval or self.last_weather_time == 0.0):
                self.last_weather_time = now
                lat = self.app.weather_cfg.get("latitude", 28.13)
                lon = self.app.weather_cfg.get("longitude", 112.95)
                w_data = fetch_weather(lat, lon, self.app.proxies)
                if w_data:
                    self.app.safe_ui_call(self.app.apply_weather_data, w_data)

            # 6. 恐慌贪婪指数拉取 (每 1800 秒)
            if now - self.last_fng_time >= 1800.0 or self.last_fng_time == 0.0:
                self.last_fng_time = now
                fng = fetch_fear_greed(self.app.proxies)
                if fng:
                    self.app.safe_ui_call(self.app.apply_fng_data, fng)

            time.sleep(0.3)

    def _get_symbol_source(self, sym):
        """智能路由币种数据源（优先 Binance 官方源，若未收录如最新 Meme 币则自适应路由至 Gate.io 全网池）"""
        if sym in self.symbol_sources:
            return self.symbol_sources[sym]
        b_sym = sym.replace("_", "").upper()
        # 探测 Binance 是否收录
        for host in BINANCE_HOSTS:
            try:
                url = f"{host}/api/v3/ticker/24hr?symbol={b_sym}"
                kwargs = {"timeout": 2, "headers": REQ_HEADERS}
                if self.app.proxies:
                    kwargs["proxies"] = self.app.proxies
                r = requests.get(url, **kwargs)
                if r.status_code == 200:
                    self.symbol_sources[sym] = "binance"
                    return "binance"
            except Exception:
                pass
        self.symbol_sources[sym] = "gateio"
        return "gateio"

    def _fetch_ticker(self):
        try:
            stats = {}
            binance_syms = []
            gate_syms = []

            for sym in self.app.symbols:
                src = self._get_symbol_source(sym)
                if src == "binance":
                    binance_syms.append(sym)
                else:
                    gate_syms.append(sym)

            # 1. 批量拉取 Binance 主流币种
            if binance_syms:
                b_list = [s.replace("_", "").upper() for s in binance_syms]
                symbols_json = json.dumps(b_list, separators=(",", ":"))
                for host in BINANCE_HOSTS:
                    try:
                        url = f"{host}/api/v3/ticker/24hr"
                        kwargs = {"params": {"symbols": symbols_json}, "timeout": 3, "headers": REQ_HEADERS}
                        if self.app.proxies:
                            kwargs["proxies"] = self.app.proxies
                        res = requests.get(url, **kwargs).json()
                        if isinstance(res, list):
                            for item in res:
                                stats[item["symbol"]] = item
                            break
                    except Exception:
                        continue

            # 2. 拉取 Gate.io 新兴 Meme 币种（支持 PONS、Robin、Solana、Base 等全网链上热门币）
            for sym in gate_syms:
                b_sym = sym.replace("_", "").upper()
                g_pair = (b_sym[:-4] + "_USDT") if b_sym.endswith("USDT") else (b_sym + "_USDT")
                for host in GATEIO_HOSTS:
                    try:
                        url = f"{host}/api/v4/spot/tickers?currency_pair={g_pair}"
                        kwargs = {"timeout": 3, "headers": REQ_HEADERS}
                        if self.app.proxies:
                            kwargs["proxies"] = self.app.proxies
                        res = requests.get(url, **kwargs).json()
                        if isinstance(res, list) and res:
                            item = res[0]
                            stats[sym] = {
                                "symbol": sym,
                                "lastPrice": item.get("last", "0"),
                                "priceChangePercent": item.get("change_percentage", "0"),
                                "highPrice": item.get("high_24h", item.get("last", "0")),
                                "lowPrice": item.get("low_24h", item.get("last", "0")),
                                "volume": item.get("base_volume", "0"),
                                "quoteVolume": item.get("quote_volume", "0"),
                            }
                            break
                    except Exception:
                        continue

            if stats:
                self.app.safe_ui_call(self.app.apply_realtime_stats, stats)
        except Exception:
            pass

    def _fetch_klines(self):
        results = {}
        for sym in self.app.symbols:
            src = self._get_symbol_source(sym)
            b_sym = sym.replace("_", "").upper()
            if src == "binance":
                for host in BINANCE_HOSTS:
                    try:
                        url = f"{host}/api/v3/klines?symbol={b_sym}&interval={self.app.klines_interval}&limit={self.app.klines_limit}"
                        kwargs = {"timeout": 3, "headers": REQ_HEADERS}
                        if self.app.proxies:
                            kwargs["proxies"] = self.app.proxies
                        res = requests.get(url, **kwargs).json()
                        if isinstance(res, list):
                            results[sym] = [
                                {
                                    "close": float(k[4]),
                                    "volume": float(k[5]),
                                    "is_up": float(k[4]) >= float(k[1]),
                                }
                                for k in res
                            ]
                            break
                    except Exception:
                        continue
            else:
                # Gate.io K线接口 (时间正序矫正与蜡烛归一化)
                g_pair = (b_sym[:-4] + "_USDT") if b_sym.endswith("USDT") else (b_sym + "_USDT")
                for host in GATEIO_HOSTS:
                    try:
                        url = f"{host}/api/v4/spot/candlesticks?currency_pair={g_pair}&interval={self.app.klines_interval}&limit={self.app.klines_limit}"
                        kwargs = {"timeout": 3, "headers": REQ_HEADERS}
                        if self.app.proxies:
                            kwargs["proxies"] = self.app.proxies
                        res = requests.get(url, **kwargs).json()
                        if isinstance(res, list):
                            candles = res
                            if candles and float(candles[0][0]) > float(candles[-1][0]):
                                candles.reverse()
                            results[sym] = [
                                {
                                    "close": float(k[2]),
                                    "volume": float(k[1]),
                                    "is_up": float(k[2]) >= float(k[5]),
                                }
                                for k in candles
                            ]
                            break
                    except Exception:
                        continue
        if results:
            self.app.safe_ui_call(self.app.apply_klines_data, results)


class HyperCyberMonitor:
    def __init__(self):
        self.cfg = load_config()
        screen = self.cfg["screen"]
        display = self.cfg["display"]
        update = self.cfg["update"]

        self.width = screen["width"]
        self.height = screen["height"]
        self.x_offset = screen["x_offset"]
        self.y_offset = screen.get("y_offset", 0)
        self.symbols = self.cfg["symbols"]
        self.realtime_ms = update["realtime_ms"]
        self.klines_ms = update["klines_ms"]
        self.klines_interval = update["klines_interval"]
        self.klines_limit = update["klines_limit"]
        self.proxy = self.cfg.get("proxy", "")
        self.proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None

        self.theme_name = display.get("theme", "cyberpunk")
        self.theme = THEMES.get(self.theme_name, THEMES["cyberpunk"])

        self.weather_cfg = self.cfg.get("weather", {})
        self.weather_enabled = self.weather_cfg.get("enabled", True)
        self.weather_location = self.weather_cfg.get("location_name", "长沙 · 岳麓区")
        self.weather_ms = self.weather_cfg.get("update_ms", 600000)
        self.weather_data = None

        self.gaming_cfg = self.cfg.get("gaming_mode", {})
        self.gaming_mode_enabled = self.gaming_cfg.get("enabled", True)
        self.pause_price_flash_in_game = self.gaming_cfg.get("pause_price_flash", True)
        self.is_gaming = False

        self.fng_data = None
        self.hw_data = {"cpu": "--%", "ram": "--%"}
        self.prev_prices = {}
        self.price_flash = {}

        self.history_data = {sym: [] for sym in self.symbols}
        self.realtime_stats = {sym: {"price_str": "", "change_str": "", "change_val": 0.0} for sym in self.symbols}
        self.overlay_visible = True

        self.root = tk.Tk()
        if display.get("borderless", True):
            self.root.overrideredirect(True)
        if display.get("topmost", True):
            self.root.attributes("-topmost", True)
        self.root.geometry(
            f"{self.width}x{self.height}+{self.x_offset}+{self.y_offset}"
        )
        self.root.configure(bg=self.theme["bg_win"])

        # 顶部天气与传感器 HUD 状态栏
        self.weather_ids = {}
        self.weather_w = 0
        self.weather_h = 0
        if self.weather_enabled:
            self.weather_frame = tk.Frame(self.root, bg=self.theme["bg_win"], height=38)
            self.weather_frame.pack(side="top", fill="x", padx=6, pady=(4, 0))
            self.weather_canvas = tk.Canvas(self.weather_frame, bg=self.theme["bg_win"], height=34, highlightthickness=0, bd=0)
            self.weather_canvas.pack(fill="both", expand=True)
            self.weather_canvas.bind("<Configure>", lambda e: self._on_weather_configure(e))

        self.container = tk.Frame(self.root, bg=self.theme["bg_win"])
        self.container.pack(side="top", expand=True, fill="both")

        self.cards = {}
        n_syms = max(1, len(self.symbols))
        if n_syms == 1:
            cols = 1
        elif n_syms == 2:
            cols = 1 if self.height >= self.width * 1.2 else 2
        elif n_syms == 3:
            cols = 1 if self.height >= self.width * 1.8 else (3 if self.width >= self.height * 1.5 else 2)
        elif n_syms <= 4:
            # 极度纵向竖屏时单列排版更饱满大气，半屏/横屏时 2 列 2 行
            cols = 1 if self.height >= self.width * 1.6 else 2
        elif n_syms <= 6:
            cols = 2 if self.height >= self.width * 1.3 else 3
        else:
            cols = 2 if self.height >= self.width * 1.3 else 4

        for i, sym in enumerate(self.symbols):
            r, c = divmod(i, cols)

            card_frame = tk.Frame(self.container, bg=self.theme["bg_win"])
            card_frame.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
            self.container.grid_columnconfigure(c, weight=1)
            self.container.grid_rowconfigure(r, weight=1)

            canvas = tk.Canvas(card_frame, bg=self.theme["bg_win"], highlightthickness=0, bd=0)
            canvas.pack(expand=True, fill="both")
            canvas.bind(
                "<Button-1>",
                lambda e, s=sym: webbrowser.open(
                    f"https://www.tradingview.com/symbols/{s}/"
                ),
            )

            card_data = {
                "canvas": canvas, "cw": 1, "ch": 1,
                "initialized": False, "ids": {}, "index": i
            }
            canvas.bind(
                "<Configure>",
                lambda e, d=card_data, s=sym: self._on_card_configure(e, d, s),
            )
            self.cards[sym] = card_data

        self.root.bind("<Button-3>", lambda e: self.root.destroy())

        # 启动后台常驻守护工作线程
        self.worker = BackendWorker(self)

        # 启动主线程轻量时钟 (增量修改文字，不销毁画布)
        self.update_clock()
        self.sync_view_mode()

    def safe_ui_call(self, func, *args):
        """线程安全的主线程调度"""
        try:
            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after(0, lambda: func(*args))
        except Exception:
            pass

    def _on_card_configure(self, event, card_data, sym):
        if event.width != card_data["cw"] or event.height != card_data["ch"] or not card_data["initialized"]:
            card_data["cw"] = event.width
            card_data["ch"] = event.height
            self._init_card_canvas(sym, event.width, event.height)
            stats = self.realtime_stats.get(sym)
            if stats and stats["price_str"]:
                self.draw_card(
                    sym, stats["price_str"], stats["change_str"], stats["change_val"],
                    stats.get("high_str", ""), stats.get("low_str", ""), stats.get("index", 0)
                )

    def _on_weather_configure(self, event):
        self.draw_weather_bar()

    def draw_weather_bar(self):
        """流式自适应排版天气状态栏（完全避免文字重叠，优雅适配任意屏幕宽度）"""
        if not self.weather_enabled or not hasattr(self, "weather_canvas"):
            return
        w = self.weather_canvas.winfo_width()
        h = self.weather_canvas.winfo_height()
        if w <= 1 or h <= 1:
            w = self.width - 12
            h = 34
        if w <= 1:
            return

        canvas = self.weather_canvas
        canvas.delete("all")

        bg_card = self.theme["bg_card"]
        border_color = self.theme["border"]
        accent_color = self.theme.get("accent_color", "#FFE600")
        price_color = self.theme["price_color"]
        sym_color = self.theme["sym_color"]
        muted_color = self.theme.get("muted", "#94A3B8")
        up_color = self.theme.get("up_color", "#00FF9D")

        # 1. 科技切角底框
        chamfer = 6
        poly = [
            2, 2 + chamfer,
            2 + chamfer, 2,
            w - 2 - chamfer, 2,
            w - 2, 2 + chamfer,
            w - 2, h - 2 - chamfer,
            w - 2 - chamfer, h - 2,
            2 + chamfer, h - 2,
            2, h - 2 - chamfer
        ]
        canvas.create_polygon(poly, fill=bg_card, outline=border_color, width=1.5)

        y_center = h // 2

        # 2. 左侧：地点标签
        loc_text = f"📍 {self.weather_location}"
        t_loc = canvas.create_text(
            12, y_center, text=loc_text,
            font=("Microsoft YaHei UI", 9, "bold"), fill=accent_color, anchor="w"
        )
        loc_bbox = canvas.bbox(t_loc)
        next_x = (loc_bbox[2] if loc_bbox else 130) + 12

        # 天气与温湿度
        if self.weather_data:
            desc = self.weather_data.get("desc", "晴")
            temp = self.weather_data.get("temp", "--°C")
            feels = self.weather_data.get("feels", "")
            humidity = self.weather_data.get("humidity", "--%")

            # 天气描述 (如 阴天)
            t_desc = canvas.create_text(
                next_x, y_center, text=desc,
                font=("Microsoft YaHei UI", 9, "bold"), fill=sym_color, anchor="w"
            )
            desc_bbox = canvas.bbox(t_desc)
            next_x = (desc_bbox[2] if desc_bbox else next_x + 40) + 12

            # 温度
            t_temp = canvas.create_text(
                next_x, y_center, text=f"🌡️ {temp}",
                font=("Consolas", 10, "bold"), fill=price_color, anchor="w"
            )
            temp_bbox = canvas.bbox(t_temp)
            next_x = (temp_bbox[2] if temp_bbox else next_x + 55) + 6

            if feels and w >= 550:
                t_feels = canvas.create_text(
                    next_x, y_center, text=f"(体感 {feels})",
                    font=("Microsoft YaHei UI", 8), fill=muted_color, anchor="w"
                )
                feels_bbox = canvas.bbox(t_feels)
                next_x = (feels_bbox[2] if feels_bbox else next_x + 65) + 10

            # 湿度
            if w >= 440:
                t_hum = canvas.create_text(
                    next_x, y_center, text=f"💧 湿度 {humidity}",
                    font=("Microsoft YaHei UI", 9), fill=sym_color, anchor="w"
                )
                hum_bbox = canvas.bbox(t_hum)
                next_x = (hum_bbox[2] if hum_bbox else next_x + 65) + 12

        # 3. 中间区域：全市场情绪 + 电脑硬件监控
        if self.fng_data and w >= 660:
            fng_txt = f"😱 情绪 {self.fng_data['value']} {self.fng_data['text']}"
            t_fng = canvas.create_text(
                next_x, y_center, text=fng_txt,
                font=("Microsoft YaHei UI", 9, "bold"), fill=self.fng_data.get("color", "#10B981"), anchor="w"
            )
            fng_bbox = canvas.bbox(t_fng)
            next_x = (fng_bbox[2] if fng_bbox else next_x + 80) + 12

        if self.hw_data and w >= 760:
            hw_txt = f"💻 CPU:{self.hw_data['cpu']} RAM:{self.hw_data['ram']}"
            canvas.create_text(
                next_x, y_center, text=hw_txt,
                font=("Consolas", 9), fill=muted_color, anchor="w"
            )

        # 4. 右侧区域：数字时钟 + 状态灯 (+ 游戏模式状态)
        now = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_str = weekdays[now.weekday()]
        clock_str = now.strftime(f"%H:%M:%S {weekday_str} %m-%d")

        # 时钟
        canvas.create_text(
            w - 28, y_center, text=clock_str,
            font=("Consolas", 9, "bold"), fill=sym_color, anchor="e"
        )
        dot_color = "#F97316" if self.is_gaming else up_color
        canvas.create_oval(w - 18, y_center - 3, w - 12, y_center + 3, fill=dot_color, outline="")

        if self.is_gaming and w >= 560:
            canvas.create_text(
                w - 180, y_center, text="⚡ 游戏防掉帧",
                font=("Microsoft YaHei UI", 8, "bold"), fill="#F97316", anchor="e"
            )

    def update_clock(self):
        """轻量级主线程秒表（仅修改文字，不触发重绘）"""
        self.draw_weather_bar()
        self.root.after(1000, self.update_clock)

    def on_gaming_status_changed(self, is_gaming):
        """游戏状态切换响应"""
        self.draw_weather_bar()

    def apply_hardware_stats(self, stats):
        self.hw_data = stats
        self.draw_weather_bar()

    def apply_weather_data(self, data):
        self.weather_data = data
        self.draw_weather_bar()

    def apply_fng_data(self, data):
        self.fng_data = data
        self.draw_weather_bar()

    def _init_card_canvas(self, sym, w, h):
        """初始化卡片静态图元结构，建立可复用 ID 字典（仅在尺寸变化或启动时调用）"""
        data = self.cards[sym]
        canvas = data["canvas"]

        if w <= 1 or h <= 1:
            w = canvas.winfo_width()
            h = canvas.winfo_height()
        if w <= 1 or h <= 1:
            return

        canvas.delete("all")
        ids = {}
        data["ids"] = ids
        data["initialized"] = True

        index = data["index"]
        bg_card = self.theme["bg_card"]
        border_color = self.theme["border"]
        inner_border = self.theme.get("inner_border", "#1E293B")
        accent_color = self.theme.get("accent_color", "#FFE600")
        sym_color = self.theme["sym_color"]
        grid_line = self.theme["grid_line"]
        hud_tag = self.theme.get("hud_tag", "HUD//01")

        # 1. 科幻切角外框
        chamfer = min(14, max(8, int(min(w, h) * 0.06)))
        if self.theme.get("chamfer", True):
            poly = [
                3, 3 + chamfer,
                3 + chamfer, 3,
                w - 3 - chamfer, 3,
                w - 3, 3 + chamfer,
                w - 3, h - 3 - chamfer,
                w - 3 - chamfer, h - 3,
                3 + chamfer, h - 3,
                3, h - 3 - chamfer
            ]
            canvas.create_polygon(poly, fill=bg_card, outline=border_color, width=2)
            inner_poly = [
                6, 6 + chamfer,
                6 + chamfer, 6,
                w - 6 - chamfer, 6,
                w - 6, 6 + chamfer,
                w - 6, h - 6 - chamfer,
                w - 6 - chamfer, h - 6,
                6 + chamfer, h - 6,
                6, h - 6 - chamfer
            ]
            canvas.create_polygon(inner_poly, fill="", outline=inner_border, width=1)
        else:
            canvas.create_rectangle(3, 3, w - 3, h - 3, fill=bg_card, outline=border_color, width=2)
            canvas.create_rectangle(6, 6, w - 6, h - 6, fill="", outline=inner_border, width=1)

        # 2. 动态字号计算
        sym_size = max(13, min(int(w * 0.075), int(h * 0.14), 22))
        price_size = max(18, min(int(w * 0.115), int(h * 0.21), 32))
        badge_size = max(11, min(int(w * 0.065), int(h * 0.12), 15))

        y_header = max(20, int(h * 0.11))

        # 顶部 HUD 序号指示槽
        idx_str = f"#{index + 1:02d}"
        idx_w = 26
        idx_h = max(14, int(sym_size * 0.8))
        idx_x1 = 12
        idx_y1 = y_header - idx_h // 2
        canvas.create_rectangle(idx_x1, idx_y1, idx_x1 + idx_w, idx_y1 + idx_h, fill=inner_border, outline=accent_color, width=1)
        canvas.create_text(idx_x1 + idx_w // 2, y_header, text=idx_str, font=("Consolas", 8, "bold"), fill=accent_color, anchor="center")

        # 第一行：币名、LIVE脉冲点、涨跌幅
        clean_sym = sym.replace("USDT", "")
        t_sym = canvas.create_text(
            idx_x1 + idx_w + 8, y_header, text=clean_sym,
            font=("Microsoft YaHei UI", sym_size, "bold"), fill=sym_color, anchor="w"
        )
        sym_bbox = canvas.bbox(t_sym)
        dot_x = (sym_bbox[2] if sym_bbox else (idx_x1 + idx_w + 8 + len(clean_sym) * 14)) + 10

        ids["live_dot"] = canvas.create_oval(dot_x - 3, y_header - 3, dot_x + 3, y_header + 3, fill=self.theme["up_color"], outline="")
        canvas.create_text(
            dot_x + 6, y_header, text="LIVE",
            font=("Consolas", max(7, int(sym_size * 0.42)), "bold"), fill=self.theme.get("muted", "#64748B"), anchor="w"
        )
        ids["badge_text"] = canvas.create_text(
            w - 14, y_header, text="+0.00%",
            font=("Consolas", badge_size, "bold"), fill=self.theme["up_color"], anchor="e"
        )

        # 第二行：大字发光价格与 24H 极值
        y_price = y_header + max(12, int(sym_size * 0.65)) + max(6, int(h * 0.03))
        if self.theme.get("glow", True):
            ids["price_glow"] = canvas.create_text(
                12, y_price + 1, text="---",
                font=("Consolas", price_size, "bold"), fill=bg_card, anchor="nw"
            )
        ids["price_text"] = canvas.create_text(
            12, y_price, text="---",
            font=("Consolas", price_size, "bold"), fill=self.theme["price_color"], anchor="nw"
        )
        ids["high_low_text"] = canvas.create_text(
            w - 14, y_price + int(price_size * 0.4),
            text="", font=("Consolas", 8),
            fill=self.theme.get("muted", "#94A3B8"), anchor="ne"
        )

        # 第三行：底部背景辅助水平参考线
        approx_price_h = int(price_size * 1.25)
        raw_chart_top = y_price + approx_price_h + max(6, int(h * 0.03))
        raw_chart_bottom = h - max(16, int(h * 0.08))
        chart_left = 12
        chart_right = w - 12
        chart_w = chart_right - chart_left
        avail_h = raw_chart_bottom - raw_chart_top

        # 限制折线图最大高度比率 (不超过宽度的 60%)，防止在超长竖卡片下纵向严重拉伸失真
        max_allowed_h = max(24, int(chart_w * 0.60))
        if avail_h > max_allowed_h:
            chart_top = raw_chart_bottom - max_allowed_h
            chart_bottom = raw_chart_bottom
            chart_h = max_allowed_h
        else:
            chart_top = raw_chart_top
            chart_bottom = raw_chart_bottom
            chart_h = max(0, avail_h)

        data["chart_geo"] = {
            "top": chart_top, "bottom": chart_bottom,
            "left": chart_left, "right": chart_right,
            "h": chart_h, "w": chart_w
        }

        if chart_h >= 14 and chart_w > 20:
            for frac in (0.25, 0.5, 0.75):
                gy = chart_top + frac * chart_h
                canvas.create_line(chart_left, gy, chart_right, gy, fill=grid_line, width=1, dash=(3, 3))
            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=grid_line, width=1)

        # 动态图元骨架占位
        ids["chart_poly"] = canvas.create_polygon(0, 0, 0, 0, 0, 0, fill="", outline="")
        ids["volume_bars"] = []
        # 预分配 25 个成交量矩形 ID
        for _ in range(self.klines_limit):
            v_id = canvas.create_rectangle(0, 0, 0, 0, fill="", outline="", width=1)
            ids["volume_bars"].append(v_id)

        ids["chart_glow"] = canvas.create_line(0, 0, 0, 0, fill="", width=5, smooth=True)
        ids["chart_line"] = canvas.create_line(0, 0, 0, 0, fill="", width=2.5, smooth=True)
        ids["max_dot"] = canvas.create_oval(-10, -10, -10, -10, fill="", outline="")
        ids["min_dot"] = canvas.create_oval(-10, -10, -10, -10, fill="", outline="")
        ids["pulse_ring"] = canvas.create_oval(-10, -10, -10, -10, fill="", outline="", width=1.5)
        ids["pulse_dot"] = canvas.create_oval(-10, -10, -10, -10, fill="", outline="")
        ids["pulse_inner"] = canvas.create_oval(-10, -10, -10, -10, fill="", outline="")

        # 底部 HUD 科技水印
        wm_color = self.theme.get("muted", "#94A3B8") if self.theme_name == "pure_white" else inner_border
        canvas.create_text(12, h - 8, text=hud_tag, font=("Consolas", 7, "bold"), fill=wm_color, anchor="w")
        canvas.create_text(w - 12, h - 8, text="/// SEC-TRD", font=("Consolas", 7, "bold"), fill=wm_color, anchor="e")

    def _clear_flash(self, sym):
        if sym in self.price_flash:
            del self.price_flash[sym]
            stats = self.realtime_stats.get(sym)
            if stats and stats["price_str"]:
                self.draw_card(
                    sym, stats["price_str"], stats["change_str"], stats["change_val"],
                    stats.get("high_str", ""), stats.get("low_str", ""), stats.get("index", 0)
                )

    def draw_card(self, sym, price_str, change_str, change_val, high_str="", low_str="", index=0):
        """增量更新卡片图元（零画布销毁，超低 DWM 负载）"""
        data = self.cards[sym]
        if not data["initialized"] or not data["ids"]:
            self._init_card_canvas(sym, data["cw"], data["ch"])
        if not data["initialized"] or not data["ids"]:
            return

        canvas = data["canvas"]
        ids = data["ids"]
        geo = data.get("chart_geo", {})

        is_up = change_val >= 0
        theme_color = self.theme["up_color"] if is_up else self.theme["down_color"]
        badge_bg = self.theme["up_badge_bg"] if is_up else self.theme["down_badge_bg"]
        chart_fill = self.theme["up_chart_fill"] if is_up else self.theme["down_chart_fill"]

        # 游戏模式下不使用呼吸高光颜色，防止高频 DWM 无效化
        if self.is_gaming and self.pause_price_flash_in_game:
            price_color = self.theme["price_color"]
        else:
            price_color = self.price_flash.get(sym, self.theme["price_color"])

        # 1. 增量更新文本与状态指示
        arrow = "▲ " if is_up else "▼ "
        badge_text = arrow + change_str.replace("+", "").replace("-", "")
        canvas.itemconfigure(ids["badge_text"], text=badge_text, fill=theme_color)
        canvas.itemconfigure(ids["live_dot"], fill=theme_color)

        canvas.itemconfigure(ids["price_text"], text=price_str, fill=price_color)
        if "price_glow" in ids:
            canvas.itemconfigure(ids["price_glow"], text=price_str)

        if high_str and low_str and data["cw"] >= 180:
            h_l_text = f"24H H:{high_str}  L:{low_str}"
            canvas.itemconfigure(ids["high_low_text"], text=h_l_text)
        else:
            canvas.itemconfigure(ids["high_low_text"], text="")

        # 2. 增量更新走势折线图与成交量柱
        raw_klines = self.history_data.get(sym, [])
        chart_h = geo.get("h", 0)
        chart_w = geo.get("w", 0)
        chart_top = geo.get("top", 0)
        chart_bottom = geo.get("bottom", 0)
        chart_left = geo.get("left", 0)
        chart_right = geo.get("right", 0)

        if raw_klines and chart_h >= 14 and chart_w > 20 and len(raw_klines) >= 2:
            prices = [k["close"] if isinstance(k, dict) else float(k) for k in raw_klines]
            volumes = [k.get("volume", 0.0) if isinstance(k, dict) else 0.0 for k in raw_klines]

            min_p, max_p = min(prices), max(prices)
            rng = max_p - min_p if max_p != min_p else 1
            n = len(prices) - 1
            pts = []
            max_idx = prices.index(max_p)
            min_idx = prices.index(min_p)

            for i, p in enumerate(prices):
                curr_x = chart_left + (i / n) * chart_w
                curr_y = (chart_bottom - 2) - ((p - min_p) / rng) * (chart_h - 6)
                pts.extend([curr_x, curr_y])

            # 2.1 走势能量背景填充
            poly_pts = [chart_left, chart_bottom] + pts + [chart_right, chart_bottom]
            canvas.coords(ids["chart_poly"], *poly_pts)
            canvas.itemconfigure(ids["chart_poly"], fill=chart_fill)

            # 2.2 成交量柱状图
            max_v = max(volumes) if volumes and max(volumes) > 0 else 1
            bar_w = max(3, int((chart_w / len(raw_klines)) * 0.7))
            vol_h_max = max(6, int(chart_h * 0.32))

            for i, v_rect_id in enumerate(ids["volume_bars"]):
                if i < len(raw_klines):
                    k_item = raw_klines[i]
                    v_val = k_item.get("volume", 0.0) if isinstance(k_item, dict) else 0.0
                    v_up = k_item.get("is_up", True) if isinstance(k_item, dict) else True
                    vx = chart_left + (i / n) * chart_w
                    vh = max(2, int((v_val / max_v) * vol_h_max))
                    vy1 = chart_bottom - vh
                    vy2 = chart_bottom
                    v_border = self.theme["up_color"] if v_up else self.theme["down_color"]
                    v_fill = self.theme["up_badge_bg"] if v_up else self.theme["down_badge_bg"]
                    canvas.coords(v_rect_id, vx - bar_w / 2, vy1, vx + bar_w / 2, vy2)
                    canvas.itemconfigure(v_rect_id, fill=v_fill, outline=v_border)
                else:
                    canvas.coords(v_rect_id, 0, 0, 0, 0)

            # 2.3 霓虹折线
            canvas.coords(ids["chart_glow"], *pts)
            canvas.itemconfigure(ids["chart_glow"], fill=badge_bg)
            canvas.coords(ids["chart_line"], *pts)
            canvas.itemconfigure(ids["chart_line"], fill=theme_color)

            # 2.4 极值标注点
            p_max_x = chart_left + (max_idx / n) * chart_w
            p_max_y = (chart_bottom - 2) - ((max_p - min_p) / rng) * (chart_h - 6)
            p_max_fill = "#2563EB" if self.theme_name == "pure_white" else "#FFFFFF"
            canvas.coords(ids["max_dot"], p_max_x - 2, p_max_y - 2, p_max_x + 2, p_max_y + 2)
            canvas.itemconfigure(ids["max_dot"], fill=p_max_fill)

            p_min_x = chart_left + (min_idx / n) * chart_w
            p_min_y = (chart_bottom - 2) - ((min_p - min_p) / rng) * (chart_h - 6)
            canvas.coords(ids["min_dot"], p_min_x - 2, p_min_y - 2, p_min_x + 2, p_min_y + 2)
            canvas.itemconfigure(ids["min_dot"], fill="#64748B")

            # 2.5 最新点光标
            last_x, last_y = pts[-2], pts[-1]
            pulse_inner_fill = "#2563EB" if self.theme_name == "pure_white" else "#FFFFFF"
            canvas.coords(ids["pulse_ring"], last_x - 5, last_y - 5, last_x + 5, last_y + 5)
            canvas.itemconfigure(ids["pulse_ring"], outline=theme_color)
            canvas.coords(ids["pulse_dot"], last_x - 3, last_y - 3, last_x + 3, last_y + 3)
            canvas.itemconfigure(ids["pulse_dot"], fill=theme_color)
            canvas.coords(ids["pulse_inner"], last_x - 1, last_y - 1, last_x + 1, last_y + 1)
            canvas.itemconfigure(ids["pulse_inner"], fill=pulse_inner_fill)

    def apply_klines_data(self, results):
        for sym, data in results.items():
            if data:
                self.history_data[sym] = data
                stats = self.realtime_stats.get(sym)
                if stats and stats["price_str"]:
                    self.draw_card(
                        sym, stats["price_str"], stats["change_str"], stats["change_val"],
                        stats.get("high_str", ""), stats.get("low_str", ""), stats.get("index", 0)
                    )

    def apply_realtime_stats(self, stats):
        for i, sym in enumerate(self.symbols):
            if sym in stats:
                item = stats[sym]
                price = float(item["lastPrice"])
                change = float(item["priceChangePercent"])
                h_val = float(item.get("highPrice", 0))
                l_val = float(item.get("lowPrice", 0))

                if price >= 10000:
                    p_text = f"${price:,.1f}"
                elif price >= 1:
                    p_text = f"${price:,.2f}"
                elif price >= 0.01:
                    p_text = f"${price:.4f}"
                elif price >= 0.0001:
                    p_text = f"${price:.6f}"
                else:
                    p_text = f"${price:.8f}"

                def _fmt_hl(v):
                    if v >= 1000:
                        return f"${v / 1000:,.1f}K"
                    elif v >= 1:
                        return f"${v:,.2f}"
                    elif v >= 0.01:
                        return f"${v:.4f}"
                    return f"${v:.6f}"

                h_text = _fmt_hl(h_val) if h_val else ""
                l_text = _fmt_hl(l_val) if l_val else ""
                c_text = f"{change:+.2f}%"

                # 价格呼吸闪烁微动效 (仅在非游戏模式且价格跳动时触发)
                if not self.is_gaming:
                    old_p = self.prev_prices.get(sym)
                    if old_p is not None and old_p != price:
                        if price > old_p:
                            self.price_flash[sym] = self.theme["up_color"]
                        else:
                            self.price_flash[sym] = self.theme["down_color"]
                        self.root.after(800, lambda s=sym: self._clear_flash(s))
                self.prev_prices[sym] = price

                self.realtime_stats[sym] = {
                    "price_str": p_text,
                    "change_str": c_text,
                    "change_val": change,
                    "high_str": h_text,
                    "low_str": l_text,
                    "index": i
                }
                self.draw_card(sym, p_text, c_text, change, h_text, l_text, i)

    def sync_view_mode(self):
        if get_view_mode() == "dashboard":
            if not self.overlay_visible:
                self.root.deiconify()
                if self.cfg["display"].get("topmost", True):
                    self.root.attributes("-topmost", True)
                self.root.lift()
                self.overlay_visible = True
        elif self.overlay_visible:
            self.root.withdraw()
            self.overlay_visible = False
        self.root.after(300, self.sync_view_mode)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = HyperCyberMonitor()
    app.run()

