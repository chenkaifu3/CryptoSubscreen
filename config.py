import copy
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

THEMES = {
    "cyberpunk": {
        "name": "夜之城赛博",
        "bg_win": "#080C14",
        "bg_card": "#0F172A",
        "border": "#00F0FF",
        "inner_border": "#1E293B",
        "accent_color": "#FFE600",
        "hud_tag": "CYBER//01",
        "sym_color": "#FFFFFF",
        "price_color": "#FFE600",
        "up_color": "#00FF9D",
        "down_color": "#FF0055",
        "up_badge_bg": "#064E3B",
        "up_badge_border": "#00FF9D",
        "down_badge_bg": "#4C0519",
        "down_badge_border": "#FF0055",
        "up_chart_fill": "#0A3528",
        "down_chart_fill": "#350A1A",
        "grid_line": "#1E293B",
        "glow": True,
        "chamfer": True,
    },
    "synthwave": {
        "name": "紫金霓虹",
        "bg_win": "#120824",
        "bg_card": "#1E0E38",
        "border": "#FF2A8D",
        "inner_border": "#3B185F",
        "accent_color": "#01CDFE",
        "hud_tag": "SYNTH//80s",
        "sym_color": "#FF71CE",
        "price_color": "#01CDFE",
        "up_color": "#05FFA1",
        "down_color": "#FF2A6D",
        "up_badge_bg": "#0D3829",
        "up_badge_border": "#05FFA1",
        "down_badge_bg": "#4A0E24",
        "down_badge_border": "#FF2A6D",
        "up_chart_fill": "#1B0D38",
        "down_chart_fill": "#380D24",
        "grid_line": "#3B185F",
        "glow": True,
        "chamfer": True,
    },
    "matrix": {
        "name": "黑客终端",
        "bg_win": "#020D06",
        "bg_card": "#051A0C",
        "border": "#00FF66",
        "inner_border": "#0D3818",
        "accent_color": "#00FF66",
        "hud_tag": "TERMINAL//SYS",
        "sym_color": "#00FF66",
        "price_color": "#33FF77",
        "up_color": "#00FF66",
        "down_color": "#FF2244",
        "up_badge_bg": "#0B3D18",
        "up_badge_border": "#00FF66",
        "down_badge_bg": "#3D0B14",
        "down_badge_border": "#FF2244",
        "up_chart_fill": "#0A2914",
        "down_chart_fill": "#290A10",
        "grid_line": "#0D3818",
        "glow": True,
        "chamfer": False,
    },
    "gold": {
        "name": "黑金机械",
        "bg_win": "#0E0C08",
        "bg_card": "#1A160F",
        "border": "#D4AF37",
        "inner_border": "#382F1E",
        "accent_color": "#FFD700",
        "hud_tag": "MECH//GOLD",
        "sym_color": "#F5F5F0",
        "price_color": "#FFD700",
        "up_color": "#00E676",
        "down_color": "#FF5252",
        "up_badge_bg": "#1F3827",
        "up_badge_border": "#00E676",
        "down_badge_bg": "#3D1717",
        "down_badge_border": "#FF5252",
        "up_chart_fill": "#2E2412",
        "down_chart_fill": "#2E1212",
        "grid_line": "#382F1E",
        "glow": True,
        "chamfer": True,
    },
    "mecha": {
        "name": "星际战舰",
        "bg_win": "#080E18",
        "bg_card": "#0F1C2E",
        "border": "#38BDF8",
        "inner_border": "#1E3A5F",
        "accent_color": "#F97316",
        "hud_tag": "SHIP//HUD",
        "sym_color": "#F0F9FF",
        "price_color": "#38BDF8",
        "up_color": "#10B981",
        "down_color": "#F43F5E",
        "up_badge_bg": "#064E3B",
        "up_badge_border": "#10B981",
        "down_badge_bg": "#4C0519",
        "down_badge_border": "#F43F5E",
        "up_chart_fill": "#0B2B3D",
        "down_chart_fill": "#3D0B1C",
        "grid_line": "#1E3A5F",
        "glow": True,
        "chamfer": True,
    },
    "pure_white": {
        "name": "极简纯白",
        "bg_win": "#F1F5F9",
        "bg_card": "#FFFFFF",
        "border": "#2563EB",
        "inner_border": "#CBD5E1",
        "accent_color": "#2563EB",
        "hud_tag": "WHITE//MINIMAL",
        "sym_color": "#0F172A",
        "price_color": "#0F172A",
        "up_color": "#16A34A",
        "down_color": "#DC2626",
        "up_badge_bg": "#DCFCE7",
        "up_badge_border": "#16A34A",
        "down_badge_bg": "#FEE2E2",
        "down_badge_border": "#DC2626",
        "up_chart_fill": "#E0F2FE",
        "down_chart_fill": "#FFE4E6",
        "grid_line": "#E2E8F0",
        "muted": "#64748B",
        "glow": False,
        "chamfer": True,
    }
}

DEFAULT_CONFIG = {
    "screen": {
        "width": 640,
        "height": 480,
        "x_offset": -640,
        "y_offset": 0,
    },
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
    "update": {
        "realtime_ms": 5000,
        "klines_ms": 300000,
        "klines_interval": "15m",
        "klines_limit": 25,
    },
    "display": {
        "topmost": True,
        "borderless": True,
        "theme": "cyberpunk",
    },
    "weather": {
        "enabled": True,
        "location_name": "岳麓 · 湘熙水郡",
        "latitude": 28.13,
        "longitude": 112.95,
        "update_ms": 900000,
    },
    "proxy": "",
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULT_CONFIG)

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key in ("screen", "update", "display", "weather"):
        if key in data and isinstance(data[key], dict):
            cfg[key].update(data[key])
    if "symbols" in data and isinstance(data["symbols"], list):
        cfg["symbols"] = [s.upper() for s in data["symbols"] if s]
    if "proxy" in data and isinstance(data["proxy"], str):
        cfg["proxy"] = data["proxy"]
    return cfg


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
