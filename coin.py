import json
import logging
import os
import threading
import time
import tkinter as tk
import webbrowser
import ctypes
from datetime import datetime

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


cpu_collector = WindowsCPUCollector()


def get_hardware_stats():
    try:
        cpu = cpu_collector.get_cpu_percent()
        ram = psutil.virtual_memory().percent
        return {"cpu": f"{cpu:.0f}%", "ram": f"{ram:.0f}%"}
    except Exception:
        return {"cpu": "--%", "ram": "--%"}


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
        if self.weather_enabled:
            self.weather_frame = tk.Frame(self.root, bg=self.theme["bg_win"], height=38)
            self.weather_frame.pack(side="top", fill="x", padx=6, pady=(4, 0))
            self.weather_canvas = tk.Canvas(self.weather_frame, bg=self.theme["bg_win"], height=34, highlightthickness=0, bd=0)
            self.weather_canvas.pack(fill="both", expand=True)
            self.weather_canvas.bind("<Configure>", lambda e: self.draw_weather_bar())

        self.container = tk.Frame(self.root, bg=self.theme["bg_win"])
        self.container.pack(side="top", expand=True, fill="both")

        self.cards = {}
        n_syms = max(1, len(self.symbols))
        if n_syms == 1:
            cols = 1
        elif n_syms == 2:
            cols = 2 if self.width >= self.height else 1
        elif n_syms == 3:
            cols = 3 if self.width >= self.height * 1.5 else (1 if self.height >= self.width * 1.5 else 2)
        elif n_syms <= 4:
            cols = 2
        elif n_syms <= 6:
            cols = 3 if self.width >= self.height else 2
        else:
            cols = 4 if self.width >= self.height else 3

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

            # 通过 <Configure> 事件缓存 canvas 尺寸
            card_data = {"canvas": canvas, "cw": 1, "ch": 1}
            canvas.bind(
                "<Configure>",
                lambda e, d=card_data: self._on_canvas_configure(e, d),
            )
            self.cards[sym] = card_data

        self.root.bind("<Button-3>", lambda e: self.root.destroy())
        self.update_weather()
        self.update_fng()
        self.update_hardware()
        self.update_clock()
        self.update_klines()
        self.update_realtime()
        self.sync_view_mode()

    @staticmethod
    def _on_canvas_configure(event, card_data):
        card_data["cw"] = event.width
        card_data["ch"] = event.height

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
        data = self.cards[sym]
        canvas = data["canvas"]

        w = data["cw"]
        h = data["ch"]

        # <Configure> 事件未触发时回退到 winfo 获取实际尺寸
        if w <= 1 or h <= 1:
            w = canvas.winfo_width()
            h = canvas.winfo_height()
        if w <= 1 or h <= 1:
            return

        canvas.delete("all")

        # 1. 提取当前主题全套高光色彩
        is_up = change_val >= 0
        theme_color = self.theme["up_color"] if is_up else self.theme["down_color"]
        badge_bg = self.theme["up_badge_bg"] if is_up else self.theme["down_badge_bg"]
        badge_border = self.theme["up_badge_border"] if is_up else self.theme["down_badge_border"]
        chart_fill = self.theme["up_chart_fill"] if is_up else self.theme["down_chart_fill"]
        bg_card = self.theme["bg_card"]
        border_color = self.theme["border"]
        inner_border = self.theme.get("inner_border", "#1E293B")
        accent_color = self.theme.get("accent_color", "#FFE600")
        price_color = self.price_flash.get(sym, self.theme["price_color"])
        sym_color = self.theme["sym_color"]
        grid_line = self.theme["grid_line"]
        hud_tag = self.theme.get("hud_tag", "HUD//01")

        # 2. 绘制科幻切角外框 (Chamfered Sci-Fi Frame)
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
            # 内发光微细边框
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
            # 矩形终端风格
            canvas.create_rectangle(3, 3, w - 3, h - 3, fill=bg_card, outline=border_color, width=2)
            canvas.create_rectangle(6, 6, w - 6, h - 6, fill="", outline=inner_border, width=1)

        # 3. 动态字号计算 (基于宽高极高容错)
        sym_size = max(13, min(int(w * 0.075), int(h * 0.14), 22))
        price_size = max(18, min(int(w * 0.115), int(h * 0.21), 32))
        badge_size = max(11, min(int(w * 0.065), int(h * 0.12), 15))

        # 舒适的顶部下沉间距 (避免贴顶，更显开阔)
        y_header = max(20, int(h * 0.11))

        # 顶部 HUD 序号指示槽
        idx_str = f"#{index + 1:02d}"
        idx_w = 26
        idx_h = max(14, int(sym_size * 0.8))
        idx_x1 = 12
        idx_y1 = y_header - idx_h // 2
        canvas.create_rectangle(idx_x1, idx_y1, idx_x1 + idx_w, idx_y1 + idx_h, fill=inner_border, outline=accent_color, width=1)
        canvas.create_text(idx_x1 + idx_w // 2, y_header, text=idx_str, font=("Consolas", 8, "bold"), fill=accent_color, anchor="center")

        # --- 第一行: 币种大名 + 实时状态脉冲点 + 纯净无框涨跌幅 ---
        clean_sym = sym.replace("USDT", "")
        t_sym = canvas.create_text(
            idx_x1 + idx_w + 8, y_header, text=clean_sym,
            font=("Microsoft YaHei UI", sym_size, "bold"), fill=sym_color, anchor="w"
        )

        # 准确测量币种文本右边界，彻底杜绝 LIVE 字样与币名重叠！
        sym_bbox = canvas.bbox(t_sym)
        dot_x = (sym_bbox[2] if sym_bbox else (idx_x1 + idx_w + 8 + len(clean_sym) * 14)) + 10

        # 绿色/红色实时脉冲小圆点与 LIVE 字样
        canvas.create_oval(dot_x - 3, y_header - 3, dot_x + 3, y_header + 3, fill=theme_color, outline="")
        canvas.create_text(
            dot_x + 6, y_header, text="LIVE",
            font=("Consolas", max(7, int(sym_size * 0.42)), "bold"), fill=self.theme.get("muted", "#64748B"), anchor="w"
        )

        # 右侧：纯净无框涨跌幅文字 (去掉外框线与底框，保持干净高级)
        arrow = "▲ " if is_up else "▼ "
        badge_text = arrow + change_str.replace("+", "").replace("-", "")
        canvas.create_text(
            w - 14, y_header,
            text=badge_text, font=("Consolas", badge_size, "bold"),
            fill=theme_color, anchor="e"
        )

        # --- 第二行: 超大发光价格 + 24H High/Low 微型状态 ---
        y_price = y_header + max(12, int(sym_size * 0.65)) + max(6, int(h * 0.03))
        # 价格发光微重影背景 (Glow effect)
        if self.theme.get("glow", True):
            canvas.create_text(
                12, y_price + 1, text=price_str,
                font=("Consolas", price_size, "bold"), fill=bg_card, anchor="nw"
            )
        canvas.create_text(
            12, y_price, text=price_str,
            font=("Consolas", price_size, "bold"), fill=price_color, anchor="nw"
        )

        # 24H 极值 (右侧)
        if high_str and low_str and w >= 180:
            h_l_text = f"24H H:{high_str}  L:{low_str}"
            canvas.create_text(
                w - 14, y_price + int(price_size * 0.4),
                text=h_l_text, font=("Consolas", 8),
                fill=self.theme.get("muted", "#94A3B8"), anchor="ne"
            )

        # --- 第三行: 底部科幻折线图、阴影与网格 ---
        approx_price_h = int(price_size * 1.25)
        chart_top = y_price + approx_price_h + max(6, int(h * 0.03))
        chart_bottom = h - max(16, int(h * 0.08))
        chart_left = 12
        chart_right = w - 12
        chart_h = chart_bottom - chart_top
        chart_w = chart_right - chart_left

        if chart_h >= 14 and chart_w > 20:
            # 绘制 3 道背景辅助水平参考线
            for frac in (0.25, 0.5, 0.75):
                gy = chart_top + frac * chart_h
                canvas.create_line(chart_left, gy, chart_right, gy, fill=grid_line, width=1, dash=(3, 3))

            canvas.create_line(chart_left, chart_bottom, chart_right, chart_bottom, fill=grid_line, width=1)

        raw_klines = self.history_data.get(sym, [])
        if raw_klines and chart_h >= 14 and chart_w > 20 and len(raw_klines) >= 2:
            prices = [k["close"] if isinstance(k, dict) else float(k) for k in raw_klines]
            volumes = [k.get("volume", 0.0) if isinstance(k, dict) else 0.0 for k in raw_klines]

            min_p, max_p = min(prices), max(prices)
            rng = max_p - min_p if max_p != min_p else 1
            n = len(prices) - 1
            pts = []
            max_idx = prices.index(max_p)
            min_idx = prices.index(min_p)

            # 1. 计算折线图点集
            for i, p in enumerate(prices):
                curr_x = chart_left + (i / n) * chart_w
                curr_y = (chart_bottom - 2) - ((p - min_p) / rng) * (chart_h - 6)
                pts.extend([curr_x, curr_y])

            # 2. 走势图半透明能量填充
            poly_pts = [chart_left, chart_bottom] + pts + [chart_right, chart_bottom]
            canvas.create_polygon(poly_pts, fill=chart_fill, outline="")

            # 3. 绘制 24H 迷你成交量柱状图 (Volume Bars - 叠加在能量背景之上，鲜明可见)
            max_v = max(volumes) if volumes and max(volumes) > 0 else 1
            bar_w = max(3, int((chart_w / len(raw_klines)) * 0.7))
            vol_h_max = max(6, int(chart_h * 0.32))
            for i, k_item in enumerate(raw_klines):
                if isinstance(k_item, dict):
                    v_val = k_item.get("volume", 0.0)
                    v_up = k_item.get("is_up", True)
                    vx = chart_left + (i / n) * chart_w
                    vh = max(2, int((v_val / max_v) * vol_h_max))
                    vy1 = chart_bottom - vh
                    vy2 = chart_bottom
                    v_border = self.theme["up_color"] if v_up else self.theme["down_color"]
                    v_fill = self.theme["up_badge_bg"] if v_up else self.theme["down_badge_bg"]
                    canvas.create_rectangle(
                        vx - bar_w / 2, vy1, vx + bar_w / 2, vy2,
                        fill=v_fill, outline=v_border, width=1
                    )

            # 4. 霓虹发光衬底 (厚发光线)
            canvas.create_line(pts, fill=badge_bg, width=5, smooth=True)

            # 5. 亮色主折线
            canvas.create_line(pts, fill=theme_color, width=2.5, smooth=True)

            # 6. 极值标注点
            p_max_x = chart_left + (max_idx / n) * chart_w
            p_max_y = (chart_bottom - 2) - ((max_p - min_p) / rng) * (chart_h - 6)
            p_max_fill = "#2563EB" if self.theme_name == "pure_white" else "#FFFFFF"
            canvas.create_oval(p_max_x - 2, p_max_y - 2, p_max_x + 2, p_max_y + 2, fill=p_max_fill, outline="")

            p_min_x = chart_left + (min_idx / n) * chart_w
            p_min_y = (chart_bottom - 2) - ((min_p - min_p) / rng) * (chart_h - 6)
            canvas.create_oval(p_min_x - 2, p_min_y - 2, p_min_x + 2, p_min_y + 2, fill="#64748B", outline="")

            # 7. 最新点科幻脉冲光标
            last_x, last_y = pts[-2], pts[-1]
            pulse_ring_fill = ""
            pulse_inner_fill = "#2563EB" if self.theme_name == "pure_white" else "#FFFFFF"
            canvas.create_oval(last_x - 5, last_y - 5, last_x + 5, last_y + 5, fill=pulse_ring_fill, outline=theme_color, width=1.5)
            canvas.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3, fill=theme_color, outline="")
            canvas.create_oval(last_x - 1, last_y - 1, last_x + 1, last_y + 1, fill=pulse_inner_fill, outline="")

        # 底部 HUD 科技水印
        wm_color = self.theme.get("muted", "#94A3B8") if self.theme_name == "pure_white" else inner_border
        canvas.create_text(
            12, h - 8, text=hud_tag,
            font=("Consolas", 7, "bold"), fill=wm_color, anchor="w"
        )
        canvas.create_text(
            w - 12, h - 8, text="/// SEC-TRD",
            font=("Consolas", 7, "bold"), fill=wm_color, anchor="e"
        )

    def draw_weather_bar(self):
        if not self.weather_enabled or not hasattr(self, "weather_canvas"):
            return
        w = self.weather_canvas.winfo_width()
        h = self.weather_canvas.winfo_height()
        if w <= 1 or h <= 1:
            w = self.width - 12
            h = 34
        if w <= 1:
            return

        self.weather_canvas.delete("all")

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
        self.weather_canvas.create_polygon(poly, fill=bg_card, outline=border_color, width=1.5)

        y_center = h // 2

        # 2. 左侧：地点标签
        loc_text = f"📍 {self.weather_location}"
        t_loc = self.weather_canvas.create_text(
            12, y_center, text=loc_text,
            font=("Microsoft YaHei UI", 9, "bold"), fill=accent_color, anchor="w"
        )
        loc_bbox = self.weather_canvas.bbox(t_loc)
        next_x = (loc_bbox[2] if loc_bbox else 130) + 12

        # 天气与温湿度
        if self.weather_data:
            desc = self.weather_data.get("desc", "晴")
            temp = self.weather_data.get("temp", "--°C")
            feels = self.weather_data.get("feels", "")
            humidity = self.weather_data.get("humidity", "--%")
            wind = self.weather_data.get("wind", "--")
            t_range = self.weather_data.get("range", "")

            # 天气描述 (如 阴天)
            t_desc = self.weather_canvas.create_text(
                next_x, y_center, text=desc,
                font=("Microsoft YaHei UI", 9, "bold"), fill=sym_color, anchor="w"
            )
            desc_bbox = self.weather_canvas.bbox(t_desc)
            next_x = (desc_bbox[2] if desc_bbox else next_x + 40) + 12

            # 温度
            t_temp = self.weather_canvas.create_text(
                next_x, y_center, text=f"🌡️ {temp}",
                font=("Consolas", 10, "bold"), fill=price_color, anchor="w"
            )
            temp_bbox = self.weather_canvas.bbox(t_temp)
            next_x = (temp_bbox[2] if temp_bbox else next_x + 55) + 6

            if feels and w >= 550:
                t_feels = self.weather_canvas.create_text(
                    next_x, y_center, text=f"(体感 {feels})",
                    font=("Microsoft YaHei UI", 8), fill=muted_color, anchor="w"
                )
                feels_bbox = self.weather_canvas.bbox(t_feels)
                next_x = (feels_bbox[2] if feels_bbox else next_x + 65) + 10

            # 湿度
            if w >= 440:
                t_hum = self.weather_canvas.create_text(
                    next_x, y_center, text=f"💧 湿度 {humidity}",
                    font=("Microsoft YaHei UI", 9), fill=sym_color, anchor="w"
                )
                hum_bbox = self.weather_canvas.bbox(t_hum)
                next_x = (hum_bbox[2] if hum_bbox else next_x + 65) + 12

        # 3. 中间区域：全市场情绪 + 电脑硬件监控
        if self.fng_data and w >= 660:
            fng_txt = f"😱 情绪 {self.fng_data['value']} {self.fng_data['text']}"
            t_fng = self.weather_canvas.create_text(
                next_x, y_center, text=fng_txt,
                font=("Microsoft YaHei UI", 9, "bold"), fill=self.fng_data["color"], anchor="w"
            )
            fng_bbox = self.weather_canvas.bbox(t_fng)
            next_x = (fng_bbox[2] if fng_bbox else next_x + 80) + 12

        if self.hw_data and w >= 780:
            hw_txt = f"💻 CPU:{self.hw_data['cpu']} RAM:{self.hw_data['ram']}"
            t_hw = self.weather_canvas.create_text(
                next_x, y_center, text=hw_txt,
                font=("Consolas", 9), fill=muted_color, anchor="w"
            )

        # 4. 右侧区域：数字时钟 + 实时呼吸灯
        now = datetime.now()
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday_str = weekdays[now.weekday()]
        clock_str = now.strftime(f"%H:%M:%S {weekday_str} %m-%d")

        self.weather_canvas.create_text(
            w - 28, y_center, text=clock_str,
            font=("Consolas", 9, "bold"), fill=sym_color, anchor="e"
        )
        self.weather_canvas.create_oval(w - 18, y_center - 3, w - 12, y_center + 3, fill=up_color, outline="")

    def _safe_after(self, ms, func):
        try:
            if hasattr(self, "root") and self.root.winfo_exists():
                self.root.after(ms, func)
        except Exception:
            pass

    def update_clock(self):
        if self.weather_enabled and hasattr(self, "weather_canvas"):
            self.draw_weather_bar()
        self._safe_after(1000, self.update_clock)

    def update_fng(self):
        def _fetch():
            data = fetch_fear_greed(self.proxies)
            if data:
                self.fng_data = data
                self._safe_after(0, self.draw_weather_bar)

        threading.Thread(target=_fetch, daemon=True).start()
        self._safe_after(1800000, self.update_fng)

    def update_hardware(self):
        def _fetch():
            stats = get_hardware_stats()
            self.hw_data = stats
            self._safe_after(0, self.draw_weather_bar)

        threading.Thread(target=_fetch, daemon=True).start()
        self._safe_after(2000, self.update_hardware)

    def update_weather(self):
        if not self.weather_enabled:
            return

        def _fetch():
            lat = self.weather_cfg.get("latitude", 28.13)
            lon = self.weather_cfg.get("longitude", 112.95)
            data = fetch_weather(lat, lon, self.proxies)
            if data:
                self.weather_data = data
                self._safe_after(0, self.draw_weather_bar)

        threading.Thread(target=_fetch, daemon=True).start()
        self._safe_after(self.weather_ms, self.update_weather)

    def get_klines(self, symbol):
        for host in BINANCE_HOSTS:
            try:
                url = f"{host}/api/v3/klines?symbol={symbol}&interval={self.klines_interval}&limit={self.klines_limit}"
                kwargs = {"timeout": 3, "headers": REQ_HEADERS}
                if self.proxies:
                    kwargs["proxies"] = self.proxies
                res = requests.get(url, **kwargs).json()
                if isinstance(res, list):
                    return [
                        {
                            "close": float(k[4]),
                            "volume": float(k[5]),
                            "is_up": float(k[4]) >= float(k[1]),
                        }
                        for k in res
                    ]
            except Exception:
                continue
        logger.warning("K线数据请求失败: %s", symbol)
        return []

    def update_klines(self):
        def _fetch_all():
            results = {}
            threads = []
            for sym in self.symbols:
                def _fetch(s=sym):
                    results[s] = self.get_klines(s)
                t = threading.Thread(target=_fetch, daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=5)
            # 回到主线程更新数据
            self._safe_after(0, lambda: self._apply_klines(results))

        threading.Thread(target=_fetch_all, daemon=True).start()
        self._safe_after(self.klines_ms, self.update_klines)

    def _apply_klines(self, results):
        for sym, data in results.items():
            if data:
                self.history_data[sym] = data
                stats = self.realtime_stats.get(sym)
                if stats and stats["price_str"]:
                    self.draw_card(
                        sym, stats["price_str"], stats["change_str"], stats["change_val"],
                        stats.get("high_str", ""), stats.get("low_str", ""), stats.get("index", 0)
                    )

    def update_realtime(self):
        def _fetch_realtime():
            stats = {}
            symbols_json = json.dumps(self.symbols, separators=(',', ':'))
            for host in BINANCE_HOSTS:
                try:
                    url = f"{host}/api/v3/ticker/24hr"
                    kwargs = {"params": {"symbols": symbols_json}, "timeout": 3, "headers": REQ_HEADERS}
                    if self.proxies:
                        kwargs["proxies"] = self.proxies
                    res = requests.get(url, **kwargs).json()
                    if isinstance(res, list):
                        stats = {item["symbol"]: item for item in res}
                        break
                except Exception:
                    continue

            # 回到主线程更新 UI
            self._safe_after(0, lambda: self._apply_realtime(stats))

        threading.Thread(target=_fetch_realtime, daemon=True).start()
        self._safe_after(self.realtime_ms, self.update_realtime)

    def _apply_realtime(self, stats):
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
                else:
                    p_text = f"${price:.6f}"

                def _fmt_hl(v):
                    if v >= 1000:
                        return f"${v / 1000:,.1f}K"
                    elif v >= 1:
                        return f"${v:,.2f}"
                    return f"${v:.4f}"

                h_text = _fmt_hl(h_val) if h_val else ""
                l_text = _fmt_hl(l_val) if l_val else ""

                c_text = f"{change:+.2f}%"

                # 价格呼吸闪烁微动效判断
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

