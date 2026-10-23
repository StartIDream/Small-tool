"""
鼠标连点器 - Windows 版
功能：
  1. 设置点击间隔（毫秒）
  2. 设置点击次数（0 为无限）
  3. 快捷键控制（默认 F6 开始/停止）
  4. 选择点击按钮（左键 / 右键 / 中键）
  5. 选择点击位置（当前位置 / 固定坐标）
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import json
import os
import sys
import ctypes
from ctypes import wintypes

# ---------- Windows API ----------
try:
    import pydirectinput
    pydirectinput.PAUSE = 0   # 去掉每次调用后默认的 0.1 秒延迟，避免限制最小间隔
    USE_PYD = True
except ImportError:
    USE_PYD = False
    import mouse

# ---------- Windows 全局热键 ----------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

VK_MAP = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09,
    "BACKSPACE": 0x08, "INSERT": 0x2D, "DELETE": 0x2E,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
}

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008


def parse_hotkey(text):
    """把 "Ctrl+F6" 这类字符串解析为 (modifiers, vk)，无法识别时 vk 为 None"""
    parts = [p.strip() for p in str(text).split("+") if p.strip()]
    if not parts:
        return 0, None
    mods = MOD_NOREPEAT
    for m in parts[:-1]:
        mu = m.upper()
        if mu in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif mu == "ALT":
            mods |= MOD_ALT
        elif mu == "SHIFT":
            mods |= MOD_SHIFT
        elif mu in ("WIN", "WINDOWS"):
            mods |= MOD_WIN
    key = parts[-1].upper()
    vk = VK_MAP.get(key)
    if vk is None and len(key) == 1 and key.isalnum():
        vk = ord(key)
    return mods, vk

# ---------- 配置文件路径 ----------
# 打包成 exe 后 __file__ 位于临时解压目录，配置需存到 exe 所在目录
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "clicker_config.json")

# ---------- 默认配置 ----------
DEFAULT_CONFIG = {
    "interval_ms": 100,
    "hold_sec": 0,           # 长按时长（秒），0=普通点击
    "click_count": 0,
    "button": "left",
    "hotkey": "F6",          # 开始/停止快捷键
    "mode": "current",       # current / fixed
    "fixed_x": 500,
    "fixed_y": 500,
}


class MouseClicker:
    def __init__(self):
        self.config = dict(DEFAULT_CONFIG)
        self.running = False
        self.thread = None
        self.clicked_count = 0
        self.run_hold_sec = 0
        self.load_config()

        self.root = None

    # -------- 配置读写 --------
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # 兼容旧版本：合并为一个快捷键、毫秒级长按时长
                if "hotkey" not in saved and "hotkey_click" in saved:
                    saved["hotkey"] = saved.pop("hotkey_click")
                saved.pop("hotkey_hold", None)
                if "hold_sec" not in saved and "hold_ms" in saved:
                    saved["hold_sec"] = saved.pop("hold_ms") / 1000.0
                self.config.update(saved)
            except (json.JSONDecodeError, IOError):
                pass

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    # -------- 点击执行 --------
    def do_click(self):
        btn = self.config["button"]
        hold = self.run_hold_sec
        if hold > 0:
            # 长按：按下后保持 hold 秒再松开
            if USE_PYD:
                pydirectinput.mouseDown(button=btn)
            else:
                mouse.press(button=btn)
            end = time.time() + hold
            while self.running and time.time() < end:
                time.sleep(min(0.01, end - time.time()))
            if USE_PYD:
                pydirectinput.mouseUp(button=btn)
            else:
                mouse.release(button=btn)
        elif USE_PYD:
            if btn == "left":
                pydirectinput.click()
            elif btn == "right":
                pydirectinput.rightClick()
            else:
                pydirectinput.middleClick()
        else:
            mouse.click(button=btn)

    def move_to(self, x, y):
        if USE_PYD:
            pydirectinput.moveTo(x, y)
        else:
            mouse.move(x, y, absolute=True, duration=0)

    def click_loop(self):
        interval = self.config["interval_ms"] / 1000.0
        hold = self.run_hold_sec
        wait_time = max(0.0, interval - hold)
        max_count = self.config["click_count"]
        mode = self.config["mode"]
        fx, fy = self.config["fixed_x"], self.config["fixed_y"]

        self.clicked_count = 0
        while self.running:
            if mode == "fixed":
                self.move_to(fx, fy)
            self.do_click()
            self.clicked_count += 1

            if max_count > 0 and self.clicked_count >= max_count:
                break

            # 可中断的高精度睡眠
            end = time.perf_counter() + wait_time
            while self.running:
                remain = end - time.perf_counter()
                if remain <= 0:
                    break
                time.sleep(min(0.01, remain))

        self.running = False

    def start(self, hold_override=None):
        if self.running:
            return
        # hold_override=0 表示普通点击；None 表示使用界面配置的长按时长（秒）
        self.run_hold_sec = self.config.get("hold_sec", 0) if hold_override is None else hold_override
        self.running = True
        self.thread = threading.Thread(target=self.click_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

# ---------- 图形界面 ----------
class App:
    BUTTONS = {"左键": "left", "右键": "right", "中键": "middle"}
    HOTKEY_ID_CLICK = 1
    HOTKEY_CHOICES = [f"F{i}" for i in range(1, 13)] + ["自定义"]
    FKEYS = {f"F{i}" for i in range(1, 13)}
    MODIFIER_KEYS = {"Control_L", "Control_R", "Shift_L", "Shift_R",
                     "Alt_L", "Alt_R", "Win_L", "Win_R", "Super_L", "Super_R",
                     "Caps_Lock", "Num_Lock"}
    KEY_NAMES = {"Escape": "Esc", "space": "Space", "Return": "Enter",
                 "BackSpace": "Backspace", "Insert": "Insert", "Delete": "Delete",
                 "Home": "Home", "End": "End", "Prior": "PageUp", "Next": "PageDown"}

    def __init__(self, root):
        self.root = root
        self.root.title("鼠标连点器 v1.3")
        self.root.geometry("460x510")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.clicker = MouseClicker()
        self.clicker.root = self.root

        self._hotkey_tid = None
        self._hotkey_thread = None
        self._hotkey_event = threading.Event()
        self._hotkey_error = None
        self._registered_hotkey = None
        self._save_job = None
        self._was_running = False

        self._build_ui()
        self._load_to_ui()
        self._bind_auto_save()
        self._register_hotkey()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_status_loop()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 5}
        frm = ttk.Frame(self.root, padding=15)
        frm.pack(fill="both", expand=True)

        # 标题
        ttk.Label(frm, text="🖱️ 鼠标连点器", font=("Microsoft YaHei", 16, "bold")).pack(pady=(0, 10))

        # 点击间隔
        row1 = ttk.Frame(frm)
        row1.pack(fill="x", **pad)
        ttk.Label(row1, text="点击间隔 (ms)：", width=14).pack(side="left")
        self.var_interval = tk.StringVar()
        ttk.Entry(row1, textvariable=self.var_interval, width=12).pack(side="left", padx=5)
        ttk.Label(row1, text="(1秒=1000ms，最小1)").pack(side="left")

        # 点击次数
        row2 = ttk.Frame(frm)
        row2.pack(fill="x", **pad)
        ttk.Label(row2, text="点击次数：", width=14).pack(side="left")
        self.var_count = tk.StringVar()
        ttk.Entry(row2, textvariable=self.var_count, width=12).pack(side="left", padx=5)
        ttk.Label(row2, text="(0=无限)").pack(side="left")

        # 长按时长
        row_hold = ttk.Frame(frm)
        row_hold.pack(fill="x", **pad)
        ttk.Label(row_hold, text="长按时长 (秒)：", width=14).pack(side="left")
        self.var_hold = tk.StringVar()
        ttk.Entry(row_hold, textvariable=self.var_hold, width=12).pack(side="left", padx=5)
        ttk.Label(row_hold, text="(0=普通点击)").pack(side="left")

        # 鼠标按钮
        row3 = ttk.Frame(frm)
        row3.pack(fill="x", **pad)
        ttk.Label(row3, text="点击按钮：", width=14).pack(side="left")
        self.var_button = tk.StringVar()
        cb_btn = ttk.Combobox(row3, textvariable=self.var_button,
                              values=list(self.BUTTONS.keys()), width=10, state="readonly")
        cb_btn.pack(side="left", padx=5)

        # 点击位置
        row4 = ttk.Frame(frm)
        row4.pack(fill="x", **pad)
        ttk.Label(row4, text="点击位置：", width=14).pack(side="left")
        self.var_mode = tk.StringVar()
        ttk.Radiobutton(row4, text="当前位置", variable=self.var_mode,
                        value="current", command=self._toggle_fixed).pack(side="left")
        ttk.Radiobutton(row4, text="固定坐标", variable=self.var_mode,
                        value="fixed", command=self._toggle_fixed).pack(side="left")

        # 固定坐标
        self.row5 = ttk.Frame(frm)
        self.row5.pack(fill="x", **pad)
        ttk.Label(self.row5, text="固定坐标 X,Y：", width=14).pack(side="left")
        self.var_fx = tk.StringVar()
        ttk.Entry(self.row5, textvariable=self.var_fx, width=6).pack(side="left", padx=2)
        ttk.Label(self.row5, text=",").pack(side="left")
        self.var_fy = tk.StringVar()
        ttk.Entry(self.row5, textvariable=self.var_fy, width=6).pack(side="left", padx=2)
        self._toggle_fixed()

        # 快捷键（开始/停止，可选 F1~F12 或自定义组合键）
        row6 = ttk.Frame(frm)
        row6.pack(fill="x", **pad)
        ttk.Label(row6, text="快捷键：", width=12).pack(side="left")
        self.var_hotkey = tk.StringVar()
        self.var_choice = tk.StringVar()
        self.cb_hotkey = ttk.Combobox(row6, textvariable=self.var_choice,
                                      values=self.HOTKEY_CHOICES, width=8,
                                      state="readonly")
        self.cb_hotkey.pack(side="left", padx=(5, 2))
        self.cb_hotkey.bind("<<ComboboxSelected>>", lambda e: self._on_hotkey_choice(
            self.var_choice, self.var_hotkey, self.entry_hotkey))
        self.entry_hotkey = ttk.Entry(row6, textvariable=self.var_hotkey,
                                      width=12, justify="center", state="readonly")
        self.entry_hotkey.pack(side="left", padx=2)
        self.entry_hotkey.bind("<KeyPress>", lambda e: self._capture_hotkey(
            e, self.var_hotkey, self.var_choice))
        self.entry_hotkey.bind("<FocusIn>", lambda e: self._unregister_hotkey())
        self.entry_hotkey.bind("<FocusOut>", lambda e: self._apply_hotkey())

        # 状态栏
        self.var_status = tk.StringVar(value="⏹ 已停止")
        ttk.Label(frm, textvariable=self.var_status, font=("Microsoft YaHei", 11),
                  foreground="red").pack(pady=8)

        # 按钮
        btns = ttk.Frame(frm)
        btns.pack(pady=5)
        self.btn_start = ttk.Button(btns, text="▶ 开始 (快捷键)", command=self.toggle, width=18)
        self.btn_start.pack(side="left", padx=5)

        # 提示
        ttk.Label(frm, text="设置修改后自动保存",
                  foreground="gray", font=("Microsoft YaHei", 9)).pack(pady=(8, 0))
        ttk.Label(frm, text="自定义快捷键：下拉选「自定义」后在右侧框内按组合键",
                  foreground="gray", font=("Microsoft YaHei", 9)).pack(pady=(2, 0))
        ttk.Label(frm, text="提示：按快捷键或点击按钮开始/停止",
                  foreground="gray", font=("Microsoft YaHei", 9)).pack(pady=(2, 0))

    def _toggle_fixed(self):
        state = "normal" if self.var_mode.get() == "fixed" else "disabled"
        for child in self.row5.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(state=state)

    def _load_to_ui(self):
        c = self.clicker.config
        self.var_interval.set(str(c["interval_ms"]))
        self.var_hold.set(self._fmt_sec(c.get("hold_sec", 0)))
        self.var_count.set(str(c["click_count"]))
        self.var_button.set(self._btn_key_to_name(c["button"]))
        self.var_mode.set(c.get("mode", "current"))
        self.var_fx.set(str(c.get("fixed_x", 500)))
        self.var_fy.set(str(c.get("fixed_y", 500)))
        hk = c.get("hotkey", "F6")
        self.var_hotkey.set(hk)
        self.var_choice.set(hk if hk in self.FKEYS else "自定义")

    @staticmethod
    def _fmt_sec(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return "0"
        return str(int(f)) if f.is_integer() else str(f)

    def _btn_key_to_name(self, key):
        for name, k in self.BUTTONS.items():
            if k == key:
                return name
        return "左键"

    def _ui_to_config(self):
        try:
            interval = max(1, int(float(self.var_interval.get())))
        except ValueError:
            interval = 100
        try:
            hold = max(0.0, float(self.var_hold.get()))
        except ValueError:
            hold = 0.0
        try:
            count = max(0, int(self.var_count.get()))
        except ValueError:
            count = 0
        btn_name = self.var_button.get()
        button = self.BUTTONS.get(btn_name, "left")
        mode = self.var_mode.get()
        try:
            fx = int(self.var_fx.get())
            fy = int(self.var_fy.get())
        except ValueError:
            fx, fy = 500, 500
        hk = self.var_hotkey.get().strip() or "F6"
        if parse_hotkey(hk)[1] is None:
            hk = self.clicker.config.get("hotkey", "F6")

        self.clicker.config.update({
            "interval_ms": interval,
            "hold_sec": hold,
            "click_count": count,
            "button": button,
            "mode": mode,
            "fixed_x": fx,
            "fixed_y": fy,
            "hotkey": hk,
        })

    # -------- 自动保存 --------
    def _bind_auto_save(self):
        for var in (self.var_interval, self.var_hold, self.var_count,
                    self.var_button, self.var_mode, self.var_fx, self.var_fy,
                    self.var_hotkey):
            var.trace_add("write", self._schedule_auto_save)

    def _schedule_auto_save(self, *_):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(400, self._auto_save_now)

    def _auto_save_now(self):
        self._save_job = None
        c = self.clicker.config
        old = c.get("hotkey")
        self._ui_to_config()
        self.clicker.save_config()
        if c.get("hotkey") != old:
            self._apply_hotkey()

    # -------- 快捷键 --------
    def _keysym_to_name(self, keysym):
        if keysym in self.KEY_NAMES:
            return self.KEY_NAMES[keysym]
        upper = keysym.upper()
        if upper in VK_MAP:
            return upper
        if len(keysym) == 1 and keysym.isalnum():
            return upper
        return None

    def _capture_hotkey(self, event, var, choice_var):
        if event.keysym in self.MODIFIER_KEYS:
            return "break"
        key = self._keysym_to_name(event.keysym)
        if key is None:
            return "break"
        mods = []
        if event.state & 0x4:
            mods.append("Ctrl")
        if event.state & 0x1:
            mods.append("Shift")
        if event.state & 0x8 or event.state & 0x20000:
            mods.append("Alt")
        text = "+".join(mods + [key])
        if parse_hotkey(text)[1] is None:
            return "break"
        var.set(text)
        choice_var.set(text if text in self.FKEYS else "自定义")
        self._apply_hotkey()
        return "break"

    def _on_hotkey_choice(self, choice_var, hotkey_var, entry):
        choice = choice_var.get()
        if choice == "自定义":
            # 切到自定义模式，等待用户按键录制
            entry.focus_set()
            return
        hotkey_var.set(choice)
        self._apply_hotkey()

    def _apply_hotkey(self):
        self._ui_to_config()
        self.clicker.save_config()
        c = self.clicker.config
        if self._registered_hotkey != c.get("hotkey"):
            self._restart_hotkey()

    def _register_hotkey(self):
        # 在独立线程中用 RegisterHotKey(hwnd=None) 注册，无需隐藏窗口
        self._hotkey_thread = threading.Thread(target=self._win_hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    def _win_hotkey_loop(self):
        c = self.clicker.config
        hk = c.get("hotkey", "F6")
        mods, vk = parse_hotkey(hk)
        self._hotkey_tid = kernel32.GetCurrentThreadId()

        if vk is None:
            self._hotkey_tid = None
            self._hotkey_error = f"⚠ 无法识别的快捷键：{hk}"
            return

        if not user32.RegisterHotKey(None, self.HOTKEY_ID_CLICK, mods, vk):
            self._hotkey_tid = None
            self._hotkey_error = f"⚠ 热键 {hk} 注册失败（可能被占用）"
            return

        self._registered_hotkey = hk
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam == self.HOTKEY_ID_CLICK:
                self._hotkey_event.set()

        user32.UnregisterHotKey(None, self.HOTKEY_ID_CLICK)
        self._hotkey_tid = None
        self._registered_hotkey = None

    def _unregister_hotkey(self):
        tid, self._hotkey_tid = self._hotkey_tid, None
        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        th, self._hotkey_thread = self._hotkey_thread, None
        if th and th.is_alive():
            th.join(timeout=0.5)
        self._registered_hotkey = None

    def _restart_hotkey(self):
        self._unregister_hotkey()
        self._register_hotkey()

    def toggle(self):
        self._ui_to_config()
        if self.clicker.running:
            self.clicker.stop()
        else:
            self.clicker.start()
        self._refresh_status()

    def _refresh_status(self):
        if self.clicker.running:
            c = self.clicker.config
            cnt_txt = "无限" if c["click_count"] == 0 else str(c["click_count"])
            kind = "长按" if self.clicker.run_hold_sec > 0 else "点击"
            self.var_status.set(f"▶ {kind}中…  已点击 {self.clicker.clicked_count} / {cnt_txt} 次")
            self.btn_start.configure(text="⏹ 停止 (快捷键)")
        else:
            self.var_status.set("⏹ 已停止")
            self.btn_start.configure(text="▶ 开始 (快捷键)")

    def _update_status_loop(self):
        # 主线程轮询热键事件，避免在子线程中直接操作 Tk
        if self._hotkey_error:
            err, self._hotkey_error = self._hotkey_error, None
            self.var_status.set(err)
        if self._hotkey_event.is_set():
            self._hotkey_event.clear()
            self.toggle()
        if self.clicker.running:
            self._refresh_status()
        elif self._was_running:
            # 点击次数达到后自然结束，刷新一次状态
            self._refresh_status()
        self._was_running = self.clicker.running
        self.root.after(100, self._update_status_loop)

    def _on_close(self):
        self.clicker.stop()
        self.clicker.save_config()
        self._unregister_hotkey()
        self.root.destroy()


def main():
    root = tk.Tk()
    # 设置中文字体
    style = ttk.Style()
    try:
        style.configure(".", font=("Microsoft YaHei", 10))
    except Exception:
        pass
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
