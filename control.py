import copy
import json
import os

CONTROL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control.json")

DEFAULT_CONTROL = {"view_mode": "dashboard"}


import time

def load_control():
    if not os.path.exists(CONTROL_PATH):
        return copy.deepcopy(DEFAULT_CONTROL)
    for _ in range(5):
        try:
            with open(CONTROL_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            mode = data.get("view_mode", "dashboard")
            if mode not in ("dashboard", "desktop"):
                mode = "dashboard"
            return {"view_mode": mode}
        except (json.JSONDecodeError, OSError):
            time.sleep(0.05)
    return copy.deepcopy(DEFAULT_CONTROL)


def save_control(control):
    for _ in range(5):
        try:
            with open(CONTROL_PATH, "w", encoding="utf-8") as f:
                json.dump(control, f, ensure_ascii=False, indent=2)
            return True
        except OSError:
            time.sleep(0.05)
    return False


def get_view_mode():
    try:
        return load_control()["view_mode"]
    except Exception:
        return "dashboard"


def set_view_mode(mode):
    if mode not in ("dashboard", "desktop"):
        mode = "dashboard"
    try:
        return save_control({"view_mode": mode})
    except Exception:
        return False
