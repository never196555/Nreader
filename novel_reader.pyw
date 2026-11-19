#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nreader 轻量小说阅读器（无边框版，双击 .pyw 启动，不弹 CMD 窗口）
- 只有文本显示区，无窗口标题栏
- 显示行数按窗口高度自动计算；拖动窗口边缘/四角调整大小，按住拖动移动窗口
- 可配置：字体类型、字号、字体颜色、整体透明度、窗口置顶
- 背景无底色（全透明）时由全局鼠标钩子接管：框内任意位置可拖动/缩放/翻页
- 小说导入 novels 目录，可切换阅读不同文件
  （支持 TXT/EPUB/MOBI/AZW3/PRC/PDF/DOCX/HTML，导入时自动转纯文本）
- 阅读位置逐页即时落盘，闪退/关机后下次打开自动定位
- 翻页：窗口内鼠标左键/右键/滚轮；方向键/PageUp/PageDown/空格（本窗口选中时）
- 退出：可自定义快捷键（默认 Ctrl+Shift+Q，在设置中修改，本窗口选中时生效）
"""

import bisect
import base64
import ctypes
import hashlib
import hmac
import json
import os
import posixpath
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from tkinter import (ttk, filedialog, messagebox, colorchooser, simpledialog,
                     font as tkfont)

if getattr(sys, 'frozen', False):
    # 打包成 exe 后：配置与 novels 目录放在 exe 旁边，而不是临时解压目录
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
NOVEL_DIR = os.path.join(BASE, 'novels')
CONFIG_PATH = os.path.join(BASE, 'reader_config.json')
DONATE_DIR = os.path.join(BASE, '打赏码')      # 旧版明文目录（仅迁移用）
DONATE_DAT = os.path.join(BASE, 'sys_cache.dat')  # 收款码隐藏存储（混淆）
_XOR_KEY = b'nr-donate-2026'
# 签名密钥 + 管理密码：防替换 / 防他人导入自己的码（普通用户无从得知）
_HMAC_KEY = b'nr-donate-hmac-k2026'
MASTER_PW = 'shoukuan-2026'


def _donate_sig(blob):
    return hmac.new(_HMAC_KEY, blob, hashlib.sha256).digest()


def load_donate_items():
    """读取隐藏存储 [(name,b64), ...]；'__invalid__' 表示签名不符（被替换）"""
    try:
        with open(DONATE_DAT, 'rb') as f:
            raw = f.read()
        if len(raw) < 32:
            return '__invalid__'
        blob, sig = raw[:-32], raw[-32:]
        if not hmac.compare_digest(_donate_sig(blob), sig):
            return '__invalid__'
        data = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)]
                     for i, b in enumerate(blob))
        obj = json.loads(data.decode('utf-8'))
        items = obj.get('items', [])
        return [it for it in items if it.get('b64')]
    except FileNotFoundError:
        return []
    except Exception:
        return '__invalid__'


def save_donate_items(items):
    """写入隐藏存储（XOR 混淆 + HMAC 签名 + 隐藏属性 + 原子写）"""
    data = json.dumps({'items': items}, ensure_ascii=False).encode('utf-8')
    blob = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))
    tmp = DONATE_DAT + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(blob + _donate_sig(blob))
    os.replace(tmp, DONATE_DAT)
    if sys.platform == 'win32':
        try:
            ctypes.windll.kernel32.SetFileAttributesW(DONATE_DAT, 0x02)
        except Exception:
            pass


def migrate_legacy_donate():
    """把旧版 打赏码 目录里的图片搬进隐藏存储（仅首次）"""
    if load_donate_items() or not os.path.isdir(DONATE_DIR):
        return
    items = []
    for name in sorted(os.listdir(DONATE_DIR)):
        if not name.lower().endswith(('.png', '.gif')):
            continue
        try:
            with open(os.path.join(DONATE_DIR, name), 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
            items.append({'name': os.path.splitext(name)[0], 'b64': b64})
        except OSError:
            continue
    if items:
        save_donate_items(items)
DEFAULT_EXIT_HOTKEY = 'Ctrl+Shift+Q'
DEFAULT_MIN_HOTKEY = 'Ctrl+Shift+M'

DEFAULTS = {
    'width': 225,
    'height': 65,
    'x': None,
    'y': None,
    'font_size': 10,
    'font_family': '宋体',
    'fg': '#ffffff',
    'transparent_bg': True,
    'opacity': 0.81,
    'topmost': True,
    'exit_hotkey': DEFAULT_EXIT_HOTKEY,
    # 最小化/唤回（全局热键，老板键；格式同关闭快捷键）
    'min_hotkey': DEFAULT_MIN_HOTKEY,
    # 翻页按键（可多选 + 自定义）：默认 上一页=↑、下一页=↓
    'page_keys': {
        'prev': [{'vk': 0x26, 'name': '↑'}],
        'next': [{'vk': 0x28, 'name': '↓'}],
    },
    # 鼠标单击翻页（默认开启；左=下一页，右=上一页）
    'click_page': {'left': True, 'right': True},
    # 图标入口（托盘默认开启，任务栏默认关闭；均可在设置中调整）
    'show_taskbar': False,
    'show_tray': True,
    # 打赏转账链接（可选，如支付宝收款链接；显示在打赏窗口中）
    'donate_url': '',
    'file': '',
    'positions': {},
}

HIT_MARGIN = 6         # 缩放命中范围（仅窗口最外圈细条；文字区留白=6px，其余都可拖动）
DRAG_THRESHOLD = 5     # 超过该距离视为拖动窗口，否则视为点击翻页
MIN_W, MIN_H = 140, 40
TEXT_BG = '#262626'    # 正常背景色
TRANSPARENT_KEY = '#010303'  # 全透明背景的色键（Windows transparentcolor）
# 透明模式的边缘条颜色：贴近色键 → 视觉上几乎不可见，但不是色键本身，
# 因此可以命中鼠标（用于边缘缩放光标与抓取）
RING_BG = '#020303'
EDGE_HILIGHT = '#5c7fa8'   # 鼠标移到边缘时边框显形的颜色

CURSORS = {
    'n': 'sizing', 's': 'sizing',
    'e': 'sb_h_double_arrow', 'w': 'sb_h_double_arrow',
    'ne': 'top_right_corner', 'sw': 'top_right_corner',
    'nw': 'top_left_corner', 'se': 'top_left_corner',
}


def parse_hotkey(text):
    """把 'Ctrl+Shift+Q' / 'Control-Shift-Q' 之类解析为钩子与绑定所需结构。

    返回 {'ctrl','alt','shift','vk','tkseq'}；无法识别返回 None。
    至少需要一个修饰键，避免普通按键被误触发。
    """
    if not text or not str(text).strip():
        return None
    parts = [p for p in re.split(r'[+\-]', str(text).strip()) if p]
    if len(parts) < 2:
        return None
    ctrl = alt = shift = False
    key = None
    for i, p in enumerate(parts):
        pl = p.lower()
        last = i == len(parts) - 1
        if pl in ('ctrl', 'control'):
            ctrl = True
        elif pl == 'alt':
            alt = True
        elif pl == 'shift':
            shift = True
        elif last:
            key = p
        else:
            return None  # 未知修饰键
    if key is None or not (ctrl or alt or shift):
        return None
    mods = []
    if ctrl:
        mods.append('Control')
    if alt:
        mods.append('Alt')
    if shift:
        mods.append('Shift')
    if len(key) == 1 and key.isalpha():
        keysym, vk = key.upper(), ord(key.upper())
    elif len(key) == 1 and key.isdigit():
        keysym, vk = key, ord(key.upper())
    elif re.fullmatch(r'[fF]([1-9]|1[0-2])', key):
        n = int(key[1:])
        keysym, vk = f'F{n}', 0x70 + n - 1
    elif key.lower() == 'space':
        keysym, vk = 'space', 0x20
    else:
        return None
    return {'ctrl': ctrl, 'alt': alt, 'shift': shift, 'vk': vk,
            'tkseq': '-'.join(mods + [keysym])}


# ---------------------------------------------------------------- 无底色鼠标接管

class OverlayMouse:
    """背景无底色时的鼠标接管：框内任意位置可拖动/缩放/翻页。

    色键透明区域的鼠标事件会穿透到下层窗口，控件收不到任何事件，
    因此由低级鼠标钩子按窗口矩形判定并接管。纯逻辑实现便于测试；
    apply_rect(x, y, w, h) 由钩子层提供（SetWindowPos）。
    """

    def __init__(self, apply_rect):
        self.apply_rect = apply_rect
        self.rect = None          # (left, top, w, h)
        self.state = None         # 进行中的按下/拖动

    def set_rect(self, left, top, w, h):
        self.rect = (left, top, w, h)

    def _inside(self, x, y):
        if self.rect is None:
            return False
        l, t, w, h = self.rect
        return l <= x < l + w and t <= y < t + h

    def edge_at(self, x, y):
        if self.rect is None:
            return ''
        l, t, w, h = self.rect
        m = HIT_MARGIN
        e = ''
        if y - t < m:
            e += 'n'
        elif y >= t + h - m:
            e += 's'
        if x - l < m:
            e += 'w'
        elif x >= l + w - m:
            e += 'e'
        return e

    def event(self, kind, x, y, delta=0):
        """处理一个鼠标事件，返回 (是否吞掉, 主线程动作或 None)。

        kind: 'ldown'/'lup'/'rdown'/'rup'/'move'/'wheel'
        动作: ('page','next'|'prev') / ('geom',(left,top,w,h)) / ('focus',None)
        """
        if kind == 'wheel':
            if self._inside(x, y):
                return True, ('page', 'prev' if delta > 0 else 'next')
            return False, None

        if kind in ('ldown', 'rdown'):
            if not self._inside(x, y):
                return False, None
            edge = self.edge_at(x, y) if kind == 'ldown' else ''
            self.state = {
                'btn': 'l' if kind == 'ldown' else 'r',
                'mode': 'resize' if edge else 'pending',
                'edge': edge,
                'x0': x, 'y0': y,
                'rect': self.rect,
            }
            # 被吞的点击不会到达窗口，需主动索取键盘焦点，
            # 否则 F1 等 Tk 快捷键永远不生效
            return True, ('focus', None)

        if kind in ('lup', 'rup'):
            st = self.state
            self.state = None
            if st is None:
                return False, None
            want = 'l' if kind == 'lup' else 'r'
            if st['btn'] != want:
                return True, None   # 属于我们吞掉的按下，继续吞
            if st['mode'] == 'pending':
                act = ('page', 'next' if st['btn'] == 'l' else 'prev')
            else:
                act = ('geom', self.rect)
            return True, act

        if kind == 'move':
            st = self.state
            if st is None:
                return False, None
            dx = x - st['x0']
            dy = y - st['y0']
            l, t, w, h = st['rect']
            if st['mode'] == 'pending':
                if st['btn'] == 'l' and abs(dx) + abs(dy) > DRAG_THRESHOLD:
                    st['mode'] = 'move'
                else:
                    return True, None  # 吞配套移动，与被吞的按下保持一致
            if st['mode'] == 'move':
                self.rect = (l + dx, t + dy, w, h)
                self.apply_rect(l + dx, t + dy, w, h)
            else:  # resize
                edge = st['edge']
                nw, nh = w, h
                nl, nt = l, t
                if 'e' in edge:
                    nw = max(MIN_W, w + dx)
                if 's' in edge:
                    nh = max(MIN_H, h + dy)
                if 'w' in edge:
                    nw = max(MIN_W, w - dx)
                    nl = l + (w - nw)
                if 'n' in edge:
                    nh = max(MIN_H, h - dy)
                    nt = t + (h - nh)
                self.rect = (nl, nt, nw, nh)
                self.apply_rect(nl, nt, nw, nh)
            return True, None

        return False, None


# ---------------------------------------------------------------- 配置持久化

def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    if not isinstance(cfg.get('positions'), dict):
        cfg['positions'] = {}
    cfg.pop('font_alpha', None)   # 已废弃：字体透明度改为仅用整体透明度
    return cfg


def save_cfg_file(cfg):
    """原子写入，避免写一半闪退损坏配置"""
    tmp = CONFIG_PATH + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------- 鼠标接管

def _fg_is_ours():
    """前台窗口是否属于本进程"""
    if sys.platform != 'win32':
        return True
    try:
        from ctypes import wintypes
        u = ctypes.windll.user32
        # 句柄类 API 必须声明返回类型，否则 64 位下句柄被截断判断失真
        u.GetForegroundWindow.restype = wintypes.HWND
        u.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = u.GetForegroundWindow()
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()
    except Exception:
        return True


# 翻页按键预设（可在设置中多选；另支持自定义任意按键）
PAGE_PRESETS = {
    'prev': [(0x26, '↑'), (0x21, 'PgUp'), (0x57, 'W'), (0x4B, 'K')],
    'next': [(0x28, '↓'), (0x22, 'PgDn'), (0x53, 'S'), (0x4A, 'J'),
             (0x20, '空格')],
}
DIR_TEXT = {'prev': '上一页', 'next': '下一页'}


class GlobalKeys:
    """鼠标低级钩子：仅用于无底色模式接管框内交互（键盘不做全局拦截）"""

    def __init__(self, out_queue):
        self.q = out_queue
        self._thread = None
        self.overlay = None    # OverlayMouse：无底色模式的鼠标接管
        self._ov = None
        self.ov_hwnd = None
        self.kb_hook = None    # 键盘钩子句柄（仅用于“选中后才响应按键”）
        self.ms_hook = None
        self.last_mouse_err = None
        self.hook_hits = 0     # 鼠标钩子收到的事件总数（诊断用）
        # 钩子线程只读的命中矩形（主线程维护）：低级钩子回调里禁止
        # 做任何耗时操作（Win32 调用/抢 GIL），否则系统会静默卸载钩子
        self._rect = None      # (left, top, w, h) 屏幕坐标
        self._pressed = False  # 按下期间吞掉配套的 up
        self.selected = False  # 鼠标点击选中阅读窗口后键盘才生效
        self.exit_key = None   # 自定义关闭快捷键（parse_hotkey 的结果）
        self.min_key = None    # 自定义最小化快捷键（全局老板键）
        self.vks = {}          # 翻页按键映射 {vk: 'prev'/'next'}（可配置）
        self.capture = False   # 无底色模式：框内交互由接管处理（吞事件）
        self._edge = ''        # 当前悬停的边缘（用于边框显形）
        self.settings_hwnd = None   # 设置窗口句柄（前台=可能正在输入）
        self._swallowed = set()     # 被吞按键的 vk（抬起也一并吞掉）

    def set_exit_key(self, parsed):
        self.exit_key = parsed

    def set_min_key(self, parsed):
        self.min_key = parsed

    def set_page_keys(self, page_keys):
        """重建翻页按键映射（设置界面应用时调用）"""
        vks = {}
        for e in (page_keys or {}).get('prev', []):
            try:
                vks[int(e['vk'])] = 'prev'
            except (KeyError, TypeError, ValueError):
                pass
        for e in (page_keys or {}).get('next', []):
            try:
                vks[int(e['vk'])] = 'next'
            except (KeyError, TypeError, ValueError):
                pass
        self.vks = vks

    def set_rect(self, rect):
        self._rect = tuple(rect) if rect is not None else None

    def _hit(self, x, y):
        r = self._rect
        return bool(r) and r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]

    def _edge_at(self, x, y):
        r = self._rect
        if not r:
            return ''
        m = HIT_MARGIN
        e = ''
        if y - r[1] < m:
            e += 'n'
        elif y >= r[1] + r[3] - m:
            e += 's'
        if x - r[0] < m:
            e += 'w'
        elif x >= r[0] + r[2] - m:
            e += 'e'
        return e

    def set_overlay(self, ov):
        self.overlay = ov

    def ensure_overlay(self, hwnd):
        """创建（或复用）接管对象，并登记窗口句柄"""
        self.ov_hwnd = hwnd
        if self._ov is None:
            def apply_rect(x, y, w, h):
                try:
                    from ctypes import wintypes
                    user32 = ctypes.windll.user32
                    user32.SetWindowPos.argtypes = (
                        wintypes.HWND, wintypes.HWND, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        ctypes.c_uint)
                    user32.SetWindowPos.restype = wintypes.BOOL
                    user32.SetWindowPos(
                        self.ov_hwnd, 0,
                        int(x), int(y), int(w), int(h), 0x4 | 0x10)
                except Exception:
                    pass
            self._ov = OverlayMouse(apply_rect)
        return self._ov

    def start(self):
        if sys.platform == 'win32' and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def _run(self):
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        CMPFUNC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_void_p)

        # ---- 键盘钩子：不做拦截；“鼠标选中阅读窗口”后才把按键交给程序 ----
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('vkCode', ctypes.c_uint32),
                ('scanCode', ctypes.c_uint32),
                ('flags', ctypes.c_uint32),
                ('time', ctypes.c_uint32),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        VKS_F1 = 0x70   # F1=设置（不可配置）
        self._down = set()

        def proc(n_code, w_param, l_param):
            try:
                if n_code >= 0 and w_param in (0x101, 0x105):
                    kb = ctypes.cast(l_param,
                                     ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    self._down.discard(kb.vkCode)
                    if kb.vkCode in self._swallowed:
                        # 被吞键的“抬起”也不放出去，其它软件看不到残缺按键
                        self._swallowed.discard(kb.vkCode)
                        return 1
                elif n_code >= 0 and w_param in (0x100, 0x104):
                    kb = ctypes.cast(l_param,
                                     ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = kb.vkCode
                    repeated = vk in self._down
                    self._down.add(vk)
                    action = None
                    ek = self.exit_key
                    if ek and vk == ek['vk']:
                        state = (
                            bool(user32.GetAsyncKeyState(0x11) & 0x8000),
                            bool(user32.GetAsyncKeyState(0x12) & 0x8000),
                            bool(user32.GetAsyncKeyState(0x10) & 0x8000),
                        )
                        if state == (ek['ctrl'], ek['alt'], ek['shift']):
                            action = 'exit'
                    if action is None and vk == VKS_F1:
                        action = 'settings'
                    if action is None:
                        # 最小化快捷键（全局老板键）：允许与关闭键同 vk，
                        # 靠修饰键组合区分
                        mk = self.min_key
                        if mk and vk == mk['vk'] and not repeated:
                            state = (
                                bool(user32.GetAsyncKeyState(0x11) & 0x8000),
                                bool(user32.GetAsyncKeyState(0x12) & 0x8000),
                                bool(user32.GetAsyncKeyState(0x10) & 0x8000),
                            )
                            if state == (mk['ctrl'], mk['alt'], mk['shift']):
                                action = 'minimize'
                    if action is None:
                        action = self.vks.get(vk)
                    if action == 'minimize':
                        # 全局生效（老板键）：窗口隐藏时也能一键唤回
                        self.q.put(action)
                        self._swallowed.add(vk)
                        return 1
                    if action and not (action == 'settings' and repeated):
                        fg = user32.GetForegroundWindow()
                        fg = (user32.GetAncestor(fg, 2) or fg) if fg else 0
                        ours = bool(self.ov_hwnd) and fg == self.ov_hwnd
                        in_ui = bool(self.settings_hwnd) and \
                            fg == self.settings_hwnd
                        if in_ui and action not in ('settings', 'exit'):
                            pass   # 设置窗口内可能正在输入：不拦截、不吞
                        elif self.selected or ours or in_ui:
                            self.q.put(action)
                            self._swallowed.add(vk)
                            # 吞掉按键：只作用于本工具，绝不漏给其它软件
                            return 1
            except Exception:
                pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        # 句柄类 API 必须声明 64 位返回类型，否则 HMODULE 被截断导致钩子安装失败
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        # lParam 是指针，必须按指针宽度声明，否则回调返回时报溢出
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t,
            ctypes.c_ssize_t)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.GetForegroundWindow.restype = wintypes.HWND

        callback = CMPFUNC(proc)
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, CMPFUNC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        kb_hook = user32.SetWindowsHookExW(
            13, callback, kernel32.GetModuleHandleW(None), 0)  # WH_KEYBOARD_LL
        self.kb_hook = kb_hook

        # ---- 鼠标钩子：背景无底色时接管框内拖动/缩放/翻页 ----
        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('pt_x', ctypes.c_long),
                ('pt_y', ctypes.c_long),
                ('mouseData', ctypes.c_uint32),
                ('flags', ctypes.c_uint32),
                ('time', ctypes.c_uint32),
                ('dwExtraInfo', ctypes.c_size_t),
            ]

        MOUSE_KINDS = {0x0200: 'move', 0x0201: 'ldown', 0x0202: 'lup',
                       0x0204: 'rdown', 0x0205: 'rup', 0x020A: 'wheel'}

        # ---- 遮挡检测：被其它窗口盖住的区域不接管（非置顶时的关键修正）----
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetWindow.argtypes = (wintypes.HWND, ctypes.c_uint)
        user32.GetWindow.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, ctypes.c_uint)
        user32.GetAncestor.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = (wintypes.HWND,)
        user32.IsWindowVisible.restype = wintypes.BOOL

        def covered(x, y):
            """该点是否被其它窗口盖住（只检查我们上方的窗口，与下方无关）"""
            try:
                if not self.ov_hwnd:
                    return False
                pt = wintypes.POINT(int(x), int(y))
                found = user32.WindowFromPoint(pt)
                if found and (user32.GetAncestor(found, 2) or found) \
                        == self.ov_hwnd:
                    return False      # 命中我们自己的不透明像素，必未被盖
                rc = wintypes.RECT()
                w = user32.GetWindow(self.ov_hwnd, 3)   # GW_HWNDPREV 向上
                while w:
                    if user32.IsWindowVisible(w) and \
                            user32.GetWindowRect(w, ctypes.byref(rc)):
                        if rc.left <= x < rc.right and rc.top <= y < rc.bottom:
                            return True   # 上方有窗口盖住该点
                    w = user32.GetWindow(w, 3)
                return False
            except Exception:
                return False

        def mproc(n_code, w_param, l_param):
            # 低级钩子回调必须极轻量：只做命中判定和入队。
            # 以前在这里面调 SetWindowPos/GetWindowRect（跨线程+抢 GIL），
            # 拖动时回调变慢会被系统静默卸载钩子——导致"拖不动/按键失效"。
            # 现在一切窗口操作都在主线程 _handle_mouse_evt 里做。
            try:
                if n_code < 0:
                    return user32.CallNextHookEx(
                        None, n_code, w_param, l_param)
                kind = MOUSE_KINDS.get(w_param)
                if kind is None:
                    return user32.CallNextHookEx(
                        None, n_code, w_param, l_param)
                self.hook_hits += 1
                ms = ctypes.cast(
                    l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                x, y = ms.pt_x, ms.pt_y
                if kind == 'move' and not self._pressed:
                    # 悬停边缘 → 通知主线程让边框显形（两种模式通用）
                    edge = self._edge_at(x, y) if self._hit(x, y) else ''
                    if edge != self._edge:
                        self._edge = edge
                        self.q.put(('edge', edge))
                elif kind == 'wheel':
                    if self.capture and self._hit(x, y) and not covered(x, y):
                        delta = ctypes.c_short(
                            (ms.mouseData >> 16) & 0xFFFF).value
                        self.q.put(('mouse', 'wheel', x, y, delta))
                        return 1
                elif kind in ('ldown', 'rdown'):
                    inside = self._hit(x, y) and not covered(x, y)
                    self.selected = inside   # 点内=选中；点外/被盖住=取消
                    if inside:
                        self._pressed = True
                        if self.capture:
                            self.q.put(('mouse', kind, x, y, 0))
                            return 1
                elif kind == 'move' and self._pressed:
                    if self.capture:
                        self.q.put(('mouse', 'move', x, y, 0))
                    # 注意：move 永远不能吞掉！被吞的 move 不会更新系统
                    # 光标，后续移动全都相对冻结点计算，拖动就会
                    # “走一步卡一步”并弹回原位（实测复现的根因）
                elif kind in ('lup', 'rup'):
                    if self._pressed:
                        self._pressed = False
                        if self.capture:
                            self.q.put(('mouse', kind, x, y, 0))
                            return 1
            except Exception:
                pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        mouse_cb = CMPFUNC(mproc)
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, CMPFUNC, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        mouse_hook = user32.SetWindowsHookExW(
            14, mouse_cb, kernel32.GetModuleHandleW(None), 0)  # WH_MOUSE_LL
        self.ms_hook = mouse_hook

        if not mouse_hook:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ---------------------------------------------------------------- 托盘图标

class TrayIcon:
    """系统托盘图标（可选）：窗口被遮挡时从托盘唤出。

    消息窗口在独立线程（Shell_NotifyIcon 的回调必须有消息循环），
    点击动作统一放进队列由主线程处理，不在钩子线程做任何窗口操作。
    """

    CB_MSG = 0x8001            # WM_APP+1：托盘回调消息
    IDM_SHOW, IDM_MINIMIZE, IDM_SETTINGS, IDM_EXIT = 101, 102, 103, 104

    def __init__(self, out_queue):
        self.q = out_queue
        self._thread = None
        self._ready = threading.Event()
        self.hwnd = 0
        self._up_timer = None   # 单击延迟定时器（双击时取消）

    def start(self):
        if sys.platform != 'win32' or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3)

    def stop(self):
        """移除托盘图标并退出消息线程（幂等）"""
        self._cancel_up()
        if self.hwnd:
            try:
                ctypes.windll.user32.PostMessageW(
                    self.hwnd, 0x0010, 0, 0)   # WM_CLOSE
            except Exception:
                pass
        self.hwnd = 0

    def _cancel_up(self):
        """取消挂起的单击动作（双击时先到的单击不执行）"""
        t = self._up_timer
        self._up_timer = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass

    def _run(self):
        from ctypes import wintypes
        u32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        k32 = ctypes.windll.kernel32

        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
            wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ('style', wintypes.UINT), ('lpfnWndProc', WNDPROC),
                ('cbClsExtra', ctypes.c_int), ('cbWndExtra', ctypes.c_int),
                ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE),
                ('hbrBackground', wintypes.HANDLE),
                ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD), ('hWnd', wintypes.HWND),
                ('uID', wintypes.UINT), ('uFlags', wintypes.UINT),
                ('uCallbackMessage', wintypes.UINT),
                ('hIcon', wintypes.HICON),
                ('szTip', wintypes.WCHAR * 128),
                ('dwState', wintypes.DWORD), ('dwStateMask', wintypes.DWORD),
                ('szInfo', wintypes.WCHAR * 256),
                ('uTimeout', wintypes.UINT),
                ('szInfoTitle', wintypes.WCHAR * 64),
                ('dwInfoFlags', wintypes.DWORD),
                ('guidItem', ctypes.c_byte * 16),
                ('hBalloonIcon', wintypes.HICON),
            ]

        # 句柄类 API 声明参数/返回类型，防 64 位截断或参数溢出
        k32.GetModuleHandleW.restype = wintypes.HINSTANCE
        u32.CreateWindowExW.restype = wintypes.HWND
        u32.CreateWindowExW.argtypes = (
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
            wintypes.LPVOID)
        u32.LoadIconW.restype = wintypes.HICON
        u32.DefWindowProcW.restype = ctypes.c_ssize_t
        u32.DefWindowProcW.argtypes = (
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        u32.TrackPopupMenu.restype = ctypes.c_uint
        u32.TrackPopupMenu.argtypes = (
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p)
        shell32.ExtractIconW.restype = wintypes.HICON
        shell32.Shell_NotifyIconW.argtypes = (
            wintypes.DWORD, ctypes.c_void_p)

        nid = NOTIFYICONDATAW()

        def wnd_proc(hwnd, msg, wparam, lparam):
            try:
                if msg == self.CB_MSG and lparam == 0x0203:
                    # WM_LBUTTONDBLCLK：双击 = 最小化/唤回 切换
                    self._cancel_up()
                    self.q.put(('tray', 'toggle'))
                elif msg == self.CB_MSG and lparam == 0x0202:
                    # WM_LBUTTONUP：单击延迟执行（等一下看是不是双击）
                    self._cancel_up()
                    self._up_timer = threading.Timer(
                        0.25, lambda: self.q.put(('tray', 'show')))
                    self._up_timer.daemon = True
                    self._up_timer.start()
                elif msg == self.CB_MSG and lparam in (0x0205, 0x0206):
                    # WM_RBUTTONUP：弹出小菜单
                    cmd = self._popup_menu(u32, hwnd)
                    if cmd:
                        act = {self.IDM_SHOW: 'show',
                               self.IDM_MINIMIZE: 'minimize',
                               self.IDM_SETTINGS: 'settings',
                               self.IDM_EXIT: 'exit'}.get(cmd)
                        if act:
                            self.q.put(('tray', act))
                elif msg == 0x0010:      # WM_CLOSE：摘图标并退出线程
                    shell32.Shell_NotifyIconW(2, ctypes.byref(nid))
                    u32.DestroyWindow(hwnd)
                    return 0
                elif msg == 0x0002:      # WM_DESTROY
                    u32.PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        cb = WNDPROC(wnd_proc)
        cls = 'NRTrayMsgWnd'
        hinst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cb
        wc.hInstance = hinst
        wc.lpszClassName = cls
        if not u32.RegisterClassW(ctypes.byref(wc)):
            self._ready.set()
            return
        # HWND_MESSAGE(-3)：纯消息窗口，不显示、不进任务栏
        hwnd = u32.CreateWindowExW(
            0, cls, 'nr-tray', 0, 0, 0, 0, 0,
            ctypes.c_void_p(-3), 0, hinst, None)
        if not hwnd:
            self._ready.set()
            return
        self.hwnd = hwnd

        icon = shell32.ExtractIconW(0, sys.executable, 0)
        if not icon or icon == 1:    # 1 = 文件内无图标
            icon = u32.LoadIconW(None, ctypes.c_void_p(32512))  # IDI_APPLICATION
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = 0x1 | 0x2 | 0x4      # NIF_MESSAGE|NIF_ICON|NIF_TIP
        nid.uCallbackMessage = self.CB_MSG
        nid.hIcon = icon
        nid.szTip = 'Nreader'
        shell32.Shell_NotifyIconW(0, ctypes.byref(nid))   # NIM_ADD
        self._ready.set()

        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    @staticmethod
    def _popup_menu(u32, hwnd):
        """右键菜单；TPM_RETURNCMD 直接返回命令 id"""
        from ctypes import wintypes
        u32.CreatePopupMenu.restype = wintypes.HMENU
        hmenu = u32.CreatePopupMenu()
        if not hmenu:
            return 0
        u32.AppendMenuW(hmenu, 0, TrayIcon.IDM_SHOW, '显示窗口')
        u32.AppendMenuW(hmenu, 0, TrayIcon.IDM_MINIMIZE, '最小化（隐藏）')
        u32.AppendMenuW(hmenu, 0, TrayIcon.IDM_SETTINGS, '打开设置')
        u32.AppendMenuW(hmenu, 0x800, 0, None)          # MF_SEPARATOR
        u32.AppendMenuW(hmenu, 0, TrayIcon.IDM_EXIT, '退出')
        pt = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(pt))
        u32.SetForegroundWindow(hwnd)   # 菜单不先置前会不自动消失
        cmd = u32.TrackPopupMenu(
            hmenu, 0x100 | 0x1, pt.x, pt.y, 0, hwnd, None)  # TPM_RETURNCMD|RIGHTBUTTON
        u32.DestroyMenu(hmenu)
        u32.PostMessageW(hwnd, 0, 0, 0)  # WM_NULL 收尾，保证菜单正确关闭
        return int(cmd or 0)


# ---------------------------------------------------------------- 文本读取

def decode_text(raw):
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16', errors='replace')
    for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'big5', 'shift_jis'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('gb18030', errors='replace')


# ---------------------------------------------------------------- 多格式小说解析

# 支持导入的小说格式（导入时统一提取正文转为纯文本 .txt）
NOVEL_EXTS = ('.txt', '.epub', '.mobi', '.azw3', '.prc', '.pdf',
              '.docx', '.html', '.htm')

_BLOCK_TAGS = frozenset((
    'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr',
    'section', 'article', 'blockquote', 'pre', 'title', 'body'))
_SKIP_TAGS = frozenset(('script', 'style'))


class _HTMLText(HTMLParser):
    """HTML/XHTML → 纯文本：块级标签换行，跳过脚本样式"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif not self.skip and tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in _BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def get_text(self):
        out, blank = [], True
        for line in ''.join(self.parts).split('\n'):
            line = re.sub(r'[ \t\u00a0]+', ' ', line).strip()
            if line:
                out.append(line)
                blank = False
            elif not blank:
                out.append('')
                blank = True
        return '\n'.join(out).strip()


def _html_to_text(html):
    p = _HTMLText()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass  # 容忍不规范 HTML，取已解析部分
    return p.get_text()


def _zip_text(z, name):
    return decode_text(z.read(name))


def _extract_epub(path):
    with zipfile.ZipFile(path) as z:
        names = {n.lower(): n for n in z.namelist()}
        # 容器描述 → OPF 包描述文件
        opf_path = None
        if 'meta-inf/container.xml' in names:
            try:
                root = ET.fromstring(
                    _zip_text(z, names['meta-inf/container.xml']))
                for el in root.iter():
                    if el.tag.rsplit('}', 1)[-1] == 'rootfile':
                        opf_path = el.get('full-path')
                        break
            except ET.ParseError:
                pass
        chapters = []
        if opf_path and opf_path.lower() in names:
            try:
                opf = ET.fromstring(_zip_text(z, names[opf_path.lower()]))
                manifest = {}
                spine = []
                for el in opf.iter():
                    tag = el.tag.rsplit('}', 1)[-1]
                    if tag == 'item' and el.get('id') and el.get('href'):
                        manifest[el.get('id')] = el.get('href')
                    elif tag == 'itemref' and el.get('idref'):
                        spine.append(el.get('idref'))
                base = posixpath.dirname(opf_path)
                for idref in spine:
                    href = manifest.get(idref)
                    if not href:
                        continue
                    full = posixpath.normpath(
                        posixpath.join(base, href.split('#')[0]))
                    key = full.replace('\\', '/').lower()
                    if key in names:
                        chapters.append(names[key])
            except ET.ParseError:
                chapters = []
        if not chapters:  # 无 OPF/解析失败：按文件名顺序取全部正文
            chapters = [names[k] for k in sorted(names)
                        if k.endswith(('.xhtml', '.html', '.htm'))]
        texts = []
        for name in chapters:
            t = _html_to_text(_zip_text(z, name))
            if t:
                texts.append(t)
    return '\n\n'.join(texts)


def _extract_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(_zip_text(z, 'word/document.xml'))
    w = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    lines = []
    for para in root.iter(w + 'p'):
        buf = []
        for node in para.iter():
            tag = node.tag
            if tag == w + 't' and node.text:
                buf.append(node.text)
            elif tag == w + 'tab':
                buf.append('\t')
            elif tag in (w + 'br', w + 'cr'):
                buf.append('\n')
        lines.append(''.join(buf).strip())
    out, blank = [], True
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append('')
            blank = True
    return '\n'.join(out).strip()


def _extract_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError('缺少 PDF 解析组件（pypdf）。exe 版自带；'
                         'Python 版请先执行 pip install pypdf') from None
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt('')
        except Exception:
            raise ValueError('PDF 已加密，无法解析') from None
    texts = []
    for page in reader.pages:
        t = (page.extract_text() or '').strip()
        if t:
            texts.append(t)
    return '\n\n'.join(texts)


def _extract_mobi(path):
    try:
        import mobi  # 第三方：解开 MOBI/AZW3 容器得到 HTML 或 EPUB
    except ImportError:
        raise ValueError('缺少 MOBI 解析组件（mobi）。exe 版自带；'
                         'Python 版请先执行 pip install mobi') from None
    tmpdir, extracted = mobi.extract(path)
    try:
        low = extracted.lower()
        if low.endswith('.epub'):
            return _extract_epub(extracted)
        if low.endswith('.pdf'):
            return _extract_pdf(extracted)
        with open(extracted, 'rb') as f:
            return _html_to_text(decode_text(f.read()))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def extract_novel_text(path):
    """把各格式小说解析为纯文本；不支持的格式抛 ValueError"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.txt':
        with open(path, 'rb') as f:
            text = decode_text(f.read())
    elif ext == '.epub':
        text = _extract_epub(path)
    elif ext in ('.mobi', '.azw3', '.prc'):
        text = _extract_mobi(path)
    elif ext == '.pdf':
        text = _extract_pdf(path)
    elif ext == '.docx':
        text = _extract_docx(path)
    elif ext in ('.html', '.htm'):
        with open(path, 'rb') as f:
            text = _html_to_text(decode_text(f.read()))
    else:
        raise ValueError(f'暂不支持该格式：{ext or "（无扩展名）"}')
    return text.replace('\r\n', '\n').replace('\r', '\n')


def list_novels():
    if not os.path.isdir(NOVEL_DIR):
        return []
    return sorted(f for f in os.listdir(NOVEL_DIR)
                  if f.lower().endswith('.txt'))


def font_families():
    try:
        root = tk._default_root
        fams = tkfont.families(root) if root else None
        if fams:
            return sorted({f for f in fams if not f.startswith('@')})
    except Exception:
        pass
    try:
        tmp = tk.Tk()
        tmp.withdraw()
        fams = sorted({f for f in tkfont.families(tmp)
                       if not f.startswith('@')})
        tmp.destroy()
        return fams
    except Exception:
        return []


# ---------------------------------------------------------------- 阅读主窗口

class Reader(tk.Tk):
    def __init__(self):
        super().__init__()
        self.report_callback_exception = self._log_callback_exception
        os.makedirs(NOVEL_DIR, exist_ok=True)
        self.cfg = load_cfg()

        # 段落数据
        self.paras = []
        self.offsets = []
        self.total_chars = 0
        self.wrap_cache = {}
        self.wrap_px = 100
        self.state = (0, 0)          # (起始段落下标, 段落内起始行)
        self.cur = None              # 当前文件名
        self.n_lines = 1             # 每页行数：按窗口高度自动计算
        self._drag = None            # 鼠标拖动/缩放状态
        self._closing = False        # 关闭幂等标记
        self._last_toggle = 0.0      # 设置窗口开关时间（速率限制）
        self._poll_tick = 0
        self.tray = None             # 托盘图标（可选，默认不创建）
        self._last_tb = 0.0          # 任务栏按钮命令时间（双击防抖）
        self._old_wndproc = None     # 原窗口过程（任务栏命令拦截）
        self._new_wndproc = None     # 保持回调引用防 GC

        # 全局钩子：键盘按“选中”状态放行；鼠标仅无底色模式接管
        self.q = queue.Queue()
        self.gk = GlobalKeys(self.q)
        self.gk.set_page_keys(self.cfg.get('page_keys'))
        self.gk.start()

        # 外框条：透明模式下完全透明（色键）—— 视觉上不存在；
        # 拖动/缩放由全局鼠标钩子按窗口矩形接管，不依赖边缘可见
        self.font = self._make_font()
        ring = TRANSPARENT_KEY if self.cfg.get('transparent_bg') else TEXT_BG
        self.border = tk.Frame(self, bg=ring, cursor='arrow')
        self.border.pack(fill='both', expand=True)
        self.text = tk.Text(
            self.border, wrap='none', state='disabled', font=self.font,
            bg=TRANSPARENT_KEY if self.cfg.get('transparent_bg')
            else TEXT_BG,
            fg=self.cfg.get('fg') or '#e4e4e4',
            insertwidth=0,
            relief='flat', padx=8, pady=6, highlightthickness=0,
            selectbackground='#4a6a8a', cursor='arrow')
        self.text.pack(fill='both', expand=True,
                       padx=HIT_MARGIN, pady=HIT_MARGIN)

        # 键盘不做全局拦截：鼠标点击“选中”本窗口后按键才生效
        # （由全局键盘钩子按选中状态放行，见 _dispatch_key）
        self.apply_exit_hotkey()  # 可自定义的关闭快捷键
        self.apply_min_hotkey()   # 可自定义的最小化快捷键（全局）

        # 鼠标：点击翻页 / 拖动移动 / 拖动边缘缩放（文本区与边缘条都绑定）
        for wdg in (self.text, self.border):
            wdg.bind('<ButtonPress-1>', self._on_press)
            wdg.bind('<ButtonPress-3>', self._on_press)
            wdg.bind('<ButtonRelease-1>', self._on_release)
            wdg.bind('<ButtonRelease-3>', self._on_release)
            wdg.bind('<Motion>', self._on_motion)
            wdg.bind('<B1-Motion>', self._on_motion)
            wdg.bind('<MouseWheel>', self._on_wheel)
        self.text.bind('<Configure>', self._on_configure)

        # 无标题栏窗口
        self.title('Nreader')   # 任务栏按钮/Alt-Tab 显示名
        self.overrideredirect(True)
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self._settings = None
        self.apply_geometry()
        self.attributes('-alpha', float(self.cfg['opacity']))
        self.attributes('-topmost', bool(self.cfg['topmost']))
        # 色键透明：仅在“背景无底色”开启时启用，避免影响普通模式的鼠标命中
        try:
            if self.cfg.get('transparent_bg'):
                self.attributes('-transparentcolor', TRANSPARENT_KEY)
        except tk.TclError:
            pass
        # 窗口底层背景与文本区一致，避免边缘露出底色发丝线
        self.configure(
            bg=TRANSPARENT_KEY if self.cfg.get('transparent_bg')
            else TEXT_BG)

        self.after(60, self._poll_global)
        self._sync_overlay()
        self.after(300, self._post_map)  # 窗口映射后刷新矩形并取得键盘焦点

        name = self.cfg.get('file') or ''
        if name and os.path.isfile(os.path.join(NOVEL_DIR, name)):
            self.open_file(name)
        else:
            # 首次使用（还没选过小说）：窗口先显示使用说明，同时弹出设置页
            novels = list_novels()
            if novels:
                pick = '使用说明.txt' if '使用说明.txt' in novels else novels[0]
                self.open_file(pick)
            else:
                self.show_empty()
            self.after(400, self.open_settings)

    def _make_font(self):
        fam = self.cfg.get('font_family') or ''
        if not fam:
            families = set()
            try:
                families = set(tkfont.families(self))
            except Exception:
                pass
            for cand in ('宋体', 'SimSun', 'Microsoft YaHei UI',
                         'Microsoft YaHei', 'Arial'):
                if cand in families:
                    fam = cand
                    break
        kwargs = {'size': int(self.cfg['font_size'])}
        if fam:
            kwargs['family'] = fam
        try:
            return tkfont.Font(**kwargs)
        except tk.TclError:
            return tkfont.Font(size=int(self.cfg['font_size']))

    # ------------------------------------------------ 事件处理

    def _make_key(self, act):
        def handler(event):
            self.do(act)
            return 'break'
        return handler

    def do(self, act):
        if act == 'next':
            self.next_page()
        elif act == 'prev':
            self.prev_page()
        elif act == 'settings':
            self.toggle_settings()
        elif act == 'minimize':
            self.toggle_minimize()
        elif act == 'exit':
            self.on_close()

    def toggle_minimize(self):
        """最小化（隐藏）/ 唤回阅读窗口。

        用 SW_MINIMIZE 最小化（保留任务栏按钮，可再点唤回）；
        Ctrl+Shift+M（全局）、托盘菜单、任务栏/托盘图标双击均可切换。
        隐藏期间停用鼠标接管（否则旧窗口区域的点击会被误拦截）。
        """
        try:
            # 注意 self.state 是阅读进度元组（覆盖了 Tk 方法），
            # 判断可见性只能用 winfo_ismapped()
            if not self.winfo_ismapped():
                self._raise_self()        # 内部会恢复鼠标接管
            else:
                if self._settings is not None and self._settings.winfo_exists():
                    self._settings.destroy()
                    self._settings = None
                self.gk.set_rect(None)    # 隐藏期间不接管任何区域
                self.gk.capture = False
                self._hide_self()
        except tk.TclError:
            pass

    def _hide_self(self):
        """最小化窗口：SW_MINIMIZE 保留任务栏按钮（withdraw 会让按钮消失）"""
        if sys.platform == 'win32':
            try:
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                user32.GetParent.restype = wintypes.HWND
                user32.GetAncestor.restype = wintypes.HWND
                w = user32.GetParent(self.winfo_id()) or self.winfo_id()
                hwnd = user32.GetAncestor(w, 2) or w
                user32.ShowWindow(hwnd, 6)   # SW_MINIMIZE（不发 WM_SYSCOMMAND）
                return
            except Exception:
                pass
        self.withdraw()

    def toggle_settings(self):
        """开关设置窗口；带速率限制（防按住 F1 连发/双路径重复触发）"""
        now = time.monotonic()
        if now - self._last_toggle < 0.45:
            return
        self._last_toggle = now
        st = self._settings
        if st is not None and st.winfo_exists():
            st.destroy()
            self._settings = None
        else:
            self.open_settings()

    def _poll_global(self):
        try:
            while True:
                item = self.q.get_nowait()
                try:
                    if isinstance(item, tuple) and item and item[0] == 'edge':
                        self._on_edge_hover(item[1])
                    elif isinstance(item, tuple) and item[0] == 'tray':
                        self._handle_tray(item[1])
                    elif isinstance(item, tuple):
                        self._handle_mouse_evt(*item[1:])
                    else:
                        self._dispatch_key(item)
                except Exception:
                    # 单个事件出错不能弄死整个轮询循环
                    self._log_callback_exception(*sys.exc_info())
        except queue.Empty:
            pass
        self._poll_tick += 1
        if self._poll_tick % 10 == 0:     # 每 ~300ms 同步一次命中矩形
            self._refresh_overlay_rect()
        self.after(30, self._poll_global)

    def _dispatch_key(self, act):
        """键盘动作：仅在鼠标选中阅读窗口（或本窗口在前台）时生效。
        最小化是全局热键（老板键），不受此限制——窗口隐藏时也要能唤回。"""
        if act == 'minimize' or self.gk.selected or _fg_is_ours():
            self.do(act)

    def _on_edge_hover(self, edge):
        """鼠标悬停边缘时边框显形（透明区看不到边界，靠它提示抓取位置）"""
        if edge:
            self.border.configure(bg=EDGE_HILIGHT)
        else:
            self.border.configure(
                bg=TRANSPARENT_KEY if self.cfg.get('transparent_bg')
                else TEXT_BG)

    def _handle_mouse_evt(self, kind, x, y, delta):
        """主线程处理钩子送来的鼠标事件（窗口操作都在这里做）"""
        ov = self.gk._ov
        if ov is None:
            return
        if kind in ('ldown', 'rdown', 'wheel') or ov.rect is None:
            self._refresh_overlay_rect()
        _sw, act = ov.event(kind, x, y, delta)
        self.gk.set_rect(ov.rect)           # 同步钩子侧命中矩形
        if act is not None and act[0] == 'page' and kind in ('lup', 'rup'):
            cp = self.cfg.get('click_page') or {}
            if not cp.get('left' if kind == 'lup' else 'right', True):
                act = None                  # 单击翻页已在设置中关闭
        if act is not None:
            self._handle_overlay(act)

    def _force_foreground(self):
        """把本窗口带到前台（透明模式点击被吞，窗口不会自动激活）"""
        if sys.platform != 'win32':
            return
        try:
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetParent.argtypes = (wintypes.HWND,)
            u.GetParent.restype = wintypes.HWND
            u.GetAncestor.argtypes = (wintypes.HWND, ctypes.c_uint)
            u.GetAncestor.restype = wintypes.HWND
            u.GetForegroundWindow.restype = wintypes.HWND
            u.SetForegroundWindow.argtypes = (wintypes.HWND,)
            u.GetWindowThreadProcessId.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
            u.GetWindowThreadProcessId.restype = wintypes.DWORD
            u.GetCurrentThreadId.restype = wintypes.DWORD
            u.AttachThreadInput.argtypes = (
                wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
            w = u.GetParent(self.winfo_id()) or self.winfo_id()
            hwnd = u.GetAncestor(w, 2) or w
            fg = u.GetForegroundWindow()
            pid = wintypes.DWORD()
            fg_tid = u.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            our_tid = u.GetCurrentThreadId()
            if fg_tid:
                u.AttachThreadInput(our_tid, fg_tid, True)
            u.SetForegroundWindow(hwnd)
            if fg_tid:
                u.AttachThreadInput(our_tid, fg_tid, False)
        except Exception:
            pass

    def _refresh_overlay_rect(self):
        try:
            if not self.winfo_ismapped():  # 最小化/隐藏期间不恢复接管矩形
                return
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetWindowRect.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.RECT))
            user32.GetWindowRect.restype = wintypes.BOOL
            rc = wintypes.RECT()
            hwnd = self.gk.ov_hwnd
            if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                rect = (rc.left, rc.top,
                        rc.right - rc.left, rc.bottom - rc.top)
                if self.gk._ov is not None:
                    self.gk._ov.set_rect(*rect)
                self.gk.set_rect(rect)
        except Exception:
            pass

    def _handle_overlay(self, item):
        """处理鼠标钩子发来的动作"""
        if item[0] == 'focus':
            # 被吞的点击不会给窗口键盘焦点；补一次焦点方便文本选择
            try:
                self.focus_force()
            except tk.TclError:
                pass
        elif item[0] == 'page':
            if item[1] == 'next':
                self.next_page()
            else:
                self.prev_page()
        elif item[0] == 'geom':
            left, top, w, h = item[1]
            try:
                # 钩子给的是屏幕坐标，按需换算成 Tk 坐标后写入配置
                phys = ctypes.windll.user32.GetSystemMetrics(0)
                ratio = self.winfo_screenwidth() / phys if phys else 1.0
                self.cfg['x'] = int(left * ratio)
                self.cfg['y'] = int(top * ratio)
                self.cfg['width'] = int(w * ratio)
                self.cfg['height'] = int(h * ratio)
                save_cfg_file(self.cfg)
            except Exception:
                pass

    def _sync_overlay(self):
        """同步窗口矩形（选中/边缘显形通用）；无底色模式额外注册接管"""
        if sys.platform != 'win32':
            return
        self.gk.start()
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.restype = wintypes.HWND
            user32.GetParent.argtypes = (wintypes.HWND,)
            user32.GetAncestor.restype = wintypes.HWND
            user32.GetAncestor.argtypes = (wintypes.HWND, ctypes.c_uint)
            user32.GetWindowRect.argtypes = (
                wintypes.HWND, ctypes.POINTER(wintypes.RECT))
            user32.GetWindowRect.restype = wintypes.BOOL
            w = user32.GetParent(self.winfo_id()) or self.winfo_id()
            hwnd = user32.GetAncestor(w, 2) or w   # GA_ROOT
            rc = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                return
            rect = (rc.left, rc.top,
                    rc.right - rc.left, rc.bottom - rc.top)
            self.gk.set_rect(rect)      # 两种模式都要
            self.gk.ov_hwnd = hwnd      # 遮挡检测需要（两种模式）
            if not self.cfg.get('transparent_bg'):
                self.gk.capture = False
                self.gk.set_overlay(None)
                return
            self.gk.capture = True
            ov = self.gk.ensure_overlay(hwnd)
            ov.set_rect(*rect)
            self.gk.set_overlay(ov)
        except Exception:
            pass

    def _post_map(self):
        """窗口映射后收尾：刷新接管矩形 + 取得键盘焦点（F1 等立即可用）"""
        self._sync_overlay()
        self.apply_icon_settings()   # 窗口就绪后再挂任务栏/托盘图标
        self._subclass_wndproc()     # 拦截任务栏按钮命令（双击切换）
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _subclass_wndproc(self):
        """子类化窗口过程：任务栏按钮点击/双击 → 最小化/唤回 切换。

        任务栏按钮会发 WM_SYSCOMMAND(SC_MINIMIZE/SC_RESTORE)，
        拦截后走自己的 toggle；双击的第二条命令 350ms 内防抖吞掉，
        保证双击只切换一次。
        """
        if sys.platform != 'win32' or self._old_wndproc is not None:
            return
        try:
            from ctypes import wintypes
            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM)
            u = ctypes.windll.user32
            u.GetParent.restype = wintypes.HWND
            u.GetAncestor.restype = wintypes.HWND
            u.CallWindowProcW.restype = ctypes.c_ssize_t
            u.CallWindowProcW.argtypes = (
                ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM)
            u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            u.SetWindowLongPtrW.argtypes = (
                wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
            w = u.GetParent(self.winfo_id()) or self.winfo_id()
            hwnd = u.GetAncestor(w, 2) or w
            WM_SYSCOMMAND = 0x0112
            SC_MINIMIZE, SC_RESTORE = 0xF020, 0xF120

            def new_proc(h, msg, wparam, lparam):
                try:
                    if msg == WM_SYSCOMMAND and \
                            (wparam & 0xFFF0) in (SC_MINIMIZE, SC_RESTORE):
                        # 实测 ShowWindow/deiconify 不发 WM_SYSCOMMAND，
                        # 此处只可能是任务栏按钮/系统菜单的用户操作
                        now = time.monotonic()
                        if now - self._last_tb < 0.35:
                            return 0      # 双击第二下吞掉，只切换一次
                        self._last_tb = now
                        self.q.put(('tray', 'toggle'))
                        return 0
                except Exception:
                    pass
                return u.CallWindowProcW(
                    self._old_wndproc, h, msg, wparam, lparam)

            self._new_wndproc = WNDPROC(new_proc)
            self._old_wndproc = u.SetWindowLongPtrW(
                hwnd, -4,                       # GWLP_WNDPROC
                ctypes.cast(self._new_wndproc, ctypes.c_void_p).value)
        except Exception:
            self._old_wndproc = None

    def _log_callback_exception(self, exc, val, tb):
        """pythonw 无控制台，回调异常会静默丢失；落盘便于排查"""
        try:
            import traceback
            with open(os.path.join(BASE, 'reader_error.log'), 'a',
                      encoding='utf-8') as f:
                traceback.print_exception(exc, val, tb, file=f)
                f.write('\n')
        except Exception:
            pass

    def _on_wheel(self, event):
        self.do('prev' if event.delta > 0 else 'next')
        return 'break'

    # ------------------------------------------------ 窗口移动与边缘缩放

    def _edge_at(self, wx, wy):
        """按窗口内坐标判断是否处于可缩放的边缘带（含边框圈+文字区边缘）"""
        w = self.winfo_width()
        h = self.winfo_height()
        m = HIT_MARGIN
        e = ''
        if wy < m:
            e += 'n'
        elif wy > h - m:
            e += 's'
        if wx < m:
            e += 'w'
        elif wx > w - m:
            e += 'e'
        return e

    def _evt_xy(self, event):
        return (event.x_root - self.winfo_rootx(),
                event.y_root - self.winfo_rooty())

    def _on_press(self, event):
        self.focus_force()  # 无边框窗口点击后确保能接收键盘
        wx, wy = self._evt_xy(event)
        edge = self._edge_at(wx, wy) if event.num == 1 else ''
        self._drag = {
            'mode': 'resize' if edge else 'pending',
            'edge': edge,
            'btn': event.num,
            'x0': event.x_root, 'y0': event.y_root,
            'gx': self.winfo_x(), 'gy': self.winfo_y(),
            'w': self.winfo_width(), 'h': self.winfo_height(),
        }
        return 'break'

    def _on_motion(self, event):
        d = self._drag
        if d is None:
            wx, wy = self._evt_xy(event)
            edge = self._edge_at(wx, wy)
            event.widget.configure(
                cursor=CURSORS.get(edge, 'arrow'))
            return 'break'
        dx = event.x_root - d['x0']
        dy = event.y_root - d['y0']
        if d['mode'] == 'pending':
            if d['btn'] == 1 and (abs(dx) + abs(dy)) > DRAG_THRESHOLD:
                d['mode'] = 'move'
            else:
                return 'break'
        if d['mode'] == 'move':
            self.geometry(f"+{d['gx'] + dx}+{d['gy'] + dy}")
        elif d['mode'] == 'resize':
            self._resize(d, dx, dy)
        return 'break'

    def _resize(self, d, dx, dy):
        x, y = d['gx'], d['gy']
        w, h = d['w'], d['h']
        edge = d['edge']
        if 'e' in edge:
            w = max(MIN_W, d['w'] + dx)
        if 's' in edge:
            h = max(MIN_H, d['h'] + dy)
        if 'w' in edge:
            w = max(MIN_W, d['w'] - dx)
            x = d['gx'] + (d['w'] - w)
        if 'n' in edge:
            h = max(MIN_H, d['h'] - dy)
            y = d['gy'] + (d['h'] - h)
        self.geometry(f'{w}x{h}+{x}+{y}')

    def _on_release(self, event):
        d = self._drag
        self._drag = None
        if d is None:
            return 'break'
        if d['mode'] == 'pending':
            # 未发生拖动：视为点击翻页（可在设置中关闭）
            cp = self.cfg.get('click_page') or {}
            if d['btn'] == 1 and cp.get('left', True):
                self.next_page()
            elif d['btn'] == 3 and cp.get('right', True):
                self.prev_page()
        else:
            self._save_geometry()
        wx, wy = self._evt_xy(event)
        edge = self._edge_at(wx, wy)
        event.widget.configure(
            cursor=CURSORS.get(edge, 'arrow') if event.num == 1
            else 'arrow')
        return 'break'

    def _save_geometry(self):
        self.cfg['x'] = self.winfo_x()
        self.cfg['y'] = self.winfo_y()
        self.cfg['width'] = self.winfo_width()
        self.cfg['height'] = self.winfo_height()
        save_cfg_file(self.cfg)

    # ------------------------------------------------ 窗口尺寸自适应行数

    def _recalc_lines(self):
        """按窗口高度自动计算每页显示行数"""
        try:
            inner = self.text.winfo_height() - 12   # 上下 pady 各 6
            linespace = self.font.metrics('linespace')
            n = max(1, int(inner // linespace))
        except Exception:
            n = 1
        if n != self.n_lines:
            self.n_lines = n
            return True
        return False

    def _on_configure(self, event):
        changed = False
        px = event.width - 18
        if px > 40 and abs(px - self.wrap_px) >= 4:
            self.wrap_px = px
            self.wrap_cache.clear()
            changed = True
        if self._recalc_lines():
            changed = True
        if changed and self.paras:
            self.render()

    # ------------------------------------------------ 分行与翻页

    def wrapped(self, i):
        seg = self.wrap_cache.get(i)
        if seg is None:
            seg = self._wrap(self.paras[i])
            self.wrap_cache[i] = seg
        return seg

    def _wrap(self, s):
        if s == '':
            return ['']
        lines, cur = [], ''
        width = self.wrap_px
        measure = self.font.measure
        for ch in s:
            trial = cur + ch
            if cur and measure(trial) > width:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        lines.append(cur)
        return lines

    def advance(self, state, n):
        """从 state 向后走 n 行；越界返回 None"""
        p, l = state
        first = True
        while p < len(self.paras):
            seg = self.wrapped(p)
            start = l if first else 0
            first = False
            avail = len(seg) - start
            if n < avail:
                return (p, start + n)
            n -= avail
            p += 1
        return None

    def retreat(self, state, n):
        """从 state 向前走 n 行"""
        p, l = state
        while n > 0:
            if l > 0:
                take = min(l, n)
                l -= take
                n -= take
                if n == 0:
                    return (p, l)
            if p == 0:
                return (0, 0)
            p -= 1
            l = len(self.wrapped(p))
        return (p, l)

    def next_page(self):
        if not self.paras:
            return
        ns = self.advance(self.state, self.n_lines)
        if ns is not None:
            self.state = ns
            self.render()
            self.save_pos()

    def prev_page(self):
        if not self.paras:
            return
        ns = self.retreat(self.state, self.n_lines)
        if ns != self.state:
            self.state = ns
            self.render()
            self.save_pos()

    # ------------------------------------------------ 渲染

    def collect_lines(self, p, l, n):
        out = []
        first = True
        while p < len(self.paras) and len(out) < n:
            seg = self.wrapped(p)
            if first:
                seg = seg[l:]
                first = False
            for s in seg:
                out.append(s)
                if len(out) >= n:
                    break
            p += 1
        return out

    def render(self):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        if self.paras:
            lines = self.collect_lines(self.state[0], self.state[1],
                                       self.n_lines)
            self.text.insert('1.0', '\n'.join(lines))
        self.text.configure(state='disabled')

    def show_empty(self):
        self.paras = []
        self.offsets = []
        self.total_chars = 0
        self.cur = None
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', '\n'.join([
            '还没有可阅读的文件。',
            '',
            '方式一：按 F1 打开设置，点击“导入小说”'
            '（TXT/EPUB/MOBI/AZW3/PDF/DOCX/HTML）',
            '方式二：把 .txt 文件放到目录：',
            f'  {NOVEL_DIR}',
            '',
            '操作：',
            '  鼠标左键 / 滚轮向下  → 下一页',
            '  鼠标右键 / 滚轮向上  → 上一页',
            '  按住拖动              → 移动窗口',
            '  拖动窗口边缘 / 四角   → 调整大小（显示行数自动填满）',
            '  方向键、PageUp/PageDown、空格 → 翻页（先单击选中本窗口）',
            '  F1 → 设置（字体、字号、颜色、透明背景、置顶、切换文件）',
            '  Ctrl+Shift+Q → 退出',
        ]))
        self.text.configure(state='disabled')

    # ------------------------------------------------ 文件与进度

    def open_file(self, name):
        path = os.path.join(NOVEL_DIR, name)
        if not os.path.isfile(path):
            messagebox.showerror('打开失败', f'文件不存在：{name}',
                                 parent=self)
            return
        try:
            raw = open(path, 'rb').read()
        except OSError as e:
            messagebox.showerror('打开失败', str(e), parent=self)
            return
        text = decode_text(raw).replace('\r\n', '\n').replace('\r', '\n')

        self.save_pos()  # 先记住上一个文件的位置
        self.cur = name
        self.cfg['file'] = name
        self.wrap_cache.clear()

        self.paras = []
        self.offsets = []
        off = 0
        for line in text.split('\n'):
            self.offsets.append(off)
            self.paras.append(line)
            off += len(line) + 1
        self.total_chars = len(text)

        self.state = self.state_from_pos(self.cfg['positions'].get(name)) \
            if self.paras else (0, 0)
        self.render()
        save_cfg_file(self.cfg)

    def state_from_pos(self, pos):
        """由保存的字符偏移恢复 (段落, 行)，容忍文件被替换/修改"""
        if not pos or not self.paras:
            return (0, 0)
        char = int(pos.get('char', 0))
        line = int(pos.get('line', 0))
        p = bisect.bisect_right(self.offsets, char) - 1
        if p < 0:
            p = 0
        if p >= len(self.paras):
            p = len(self.paras) - 1
        max_line = max(0, len(self.wrapped(p)) - 1)
        return (p, min(max(line, 0), max_line))

    def save_pos(self):
        """位置即时落盘：每次翻页都写盘，闪退/断电不丢进度"""
        if not self.cur or not self.paras:
            return
        p, l = self.state
        p = min(p, len(self.offsets) - 1)
        self.cfg['positions'][self.cur] = {
            'char': self.offsets[p],
            'line': l,
        }
        save_cfg_file(self.cfg)

    # ------------------------------------------------ 窗口属性

    def apply_geometry(self):
        w = int(self.cfg['width'])
        h = int(self.cfg['height'])
        x, y = self.cfg.get('x'), self.cfg.get('y')
        if x is None or y is None:
            x = max(0, (self.winfo_screenwidth() - w) // 2)
            y = max(0, (self.winfo_screenheight() - h) // 3)
        self.geometry(f'{w}x{h}+{int(x)}+{int(y)}')

    def apply_settings(self):
        fam = self.cfg.get('font_family') or ''
        try:
            if fam and fam in tkfont.families(self):
                self.font.configure(family=fam)
        except Exception:
            pass
        self.font.configure(size=int(self.cfg['font_size']))
        try:
            self.text.configure(fg=self.cfg.get('fg') or '#e4e4e4')
            # 背景无底色：文本背景改为色键颜色 → 完全透明，只显示文字
            ring = TRANSPARENT_KEY if self.cfg.get('transparent_bg') \
                else TEXT_BG
            self.text.configure(
                bg=TRANSPARENT_KEY if self.cfg.get('transparent_bg')
                else TEXT_BG)
            self.border.configure(bg=ring)   # 边缘条同步
            # 窗口底层背景同步，避免边缘露出底色发丝线
            self.configure(
                bg=TRANSPARENT_KEY if self.cfg.get('transparent_bg')
                else TEXT_BG)
        except tk.TclError:
            pass
        self.wrap_cache.clear()
        self.apply_geometry()
        self.attributes('-alpha', float(self.cfg['opacity']))
        self.attributes('-topmost', bool(self.cfg['topmost']))
        # 色键透明：开启时启用（文字区完全透明），关闭时移除（不影响命中）
        try:
            if self.cfg.get('transparent_bg'):
                self.attributes('-transparentcolor', TRANSPARENT_KEY)
            else:
                self.attributes('-transparentcolor', '')
        except tk.TclError:
            pass
        if self.cfg.get('transparent_bg'):
            self.gk.start()
        self._sync_overlay()   # 启用/刷新无底色鼠标接管
        self.apply_icon_settings()
        self._recalc_lines()
        self.render()

    # ------------------------------------------------ 任务栏 / 托盘图标

    def apply_icon_settings(self):
        """按配置开关任务栏按钮与托盘图标（两者独立，可同时存在）"""
        self._apply_taskbar_icon(bool(self.cfg.get('show_taskbar')))
        if self.cfg.get('show_tray'):
            if self.tray is None:
                self.tray = TrayIcon(self.q)
                self.tray.start()
        elif self.tray is not None:
            self.tray.stop()
            self.tray = None

    def _apply_taskbar_icon(self, show):
        """任务栏按钮：overrideredirect 窗口默认不进任务栏（WS_EX_TOOLWINDOW），
        勾选后加 WS_EX_APPWINDOW 并摘掉 WS_EX_TOOLWINDOW，让按钮出现"""
        if sys.platform != 'win32':
            return
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.restype = wintypes.HWND
            user32.GetAncestor.restype = wintypes.HWND
            w = user32.GetParent(self.winfo_id()) or self.winfo_id()
            hwnd = user32.GetAncestor(w, 2) or w    # GA_ROOT
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            user32.GetWindowLongW.restype = ctypes.c_long
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if show:
                style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            else:
                style = (style & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            # SWP_FRAMECHANGED：让任务栏立即按新样式刷新按钮
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0020 | 0x0040)  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
        except Exception:
            pass

    def _handle_tray(self, act):
        """托盘点击动作（主线程执行）"""
        if act == 'show':
            self._raise_self()
        elif act == 'toggle':
            self.toggle_minimize()
        elif act == 'minimize':
            self.toggle_minimize()
        elif act == 'settings':
            self._raise_self()
            self.open_settings()
        elif act == 'exit':
            self.on_close()

    def _raise_self(self):
        """把阅读窗口提到最前（被其它窗口遮挡/最小化后从图标唤出）"""
        try:
            self.deiconify()
            self._sync_overlay()   # 唤回后恢复鼠标接管（隐藏期间被停用）
            self.lift()
            self.focus_force()
            if sys.platform == 'win32':
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                user32.GetParent.restype = wintypes.HWND
                user32.GetAncestor.restype = wintypes.HWND
                w = user32.GetParent(self.winfo_id()) or self.winfo_id()
                hwnd = user32.GetAncestor(w, 2) or w
                user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def apply_exit_hotkey(self):
        """应用可自定义的关闭快捷键（选中窗口后生效）"""
        parsed = parse_hotkey(self.cfg.get('exit_hotkey'))
        if not parsed:
            parsed = parse_hotkey(DEFAULT_EXIT_HOTKEY)
            self.cfg['exit_hotkey'] = DEFAULT_EXIT_HOTKEY
        seq = '<' + parsed['tkseq'] + '>'
        self.exit_seq = seq
        self.gk.set_exit_key(parsed)

    def apply_min_hotkey(self):
        """应用可自定义的最小化快捷键（全局生效，老板键）"""
        parsed = parse_hotkey(self.cfg.get('min_hotkey'))
        if not parsed:
            parsed = parse_hotkey(DEFAULT_MIN_HOTKEY)
            self.cfg['min_hotkey'] = DEFAULT_MIN_HOTKEY
        self.gk.set_min_key(parsed)

    def on_close(self):
        if self._closing:
            return                      # 幂等：钩子与 Tk 双路径触发只关一次
        self._closing = True
        if self.tray is not None:
            self.tray.stop()            # 摘掉托盘图标，避免残留
            self.tray = None
        try:
            self._save_geometry()
            self.save_pos()
            save_cfg_file(self.cfg)
        finally:
            self.destroy()

    # ------------------------------------------------ 设置窗口

    def open_settings(self):
        if self._settings is not None and self._settings.winfo_exists():
            self._settings.lift()
            self._settings.focus_force()
            return
        self._settings = Settings(self)
        self.after(50, self._sync_settings_hwnd)

    def _sync_settings_hwnd(self):
        """把设置窗口句柄交给钩子（前台=可能正在输入，不拦截按键）"""
        try:
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetParent.argtypes = (wintypes.HWND,)
            u.GetParent.restype = wintypes.HWND
            u.GetAncestor.argtypes = (wintypes.HWND, ctypes.c_uint)
            u.GetAncestor.restype = wintypes.HWND
            st = self._settings
            if st is not None and st.winfo_exists():
                w = u.GetParent(st.winfo_id()) or st.winfo_id()
                self.gk.settings_hwnd = u.GetAncestor(w, 2) or w
        except Exception:
            pass


# ---------------------------------------------------------------- 设置窗口

class Settings(tk.Toplevel):
    def __init__(self, reader):
        super().__init__(reader)
        self.r = reader
        self.title('Nreader 设置')
        self.transient(reader)
        self.resizable(False, False)
        cfg = reader.cfg

        families = font_families()
        cur_fam = cfg.get('font_family') or reader.font.cget('family')
        self.v_family = tk.StringVar(value=cur_fam)
        self.v_font = tk.IntVar(value=int(cfg['font_size']))
        self.v_fg = tk.StringVar(value=cfg.get('fg') or '#e4e4e4')
        self.v_tbg = tk.BooleanVar(value=bool(cfg.get('transparent_bg')))
        self.v_alpha = tk.DoubleVar(value=float(cfg['opacity']))
        self.v_top = tk.BooleanVar(value=bool(cfg['topmost']))
        self.v_exit = tk.StringVar(value=cfg.get('exit_hotkey')
                                   or DEFAULT_EXIT_HOTKEY)
        self.v_min = tk.StringVar(value=cfg.get('min_hotkey')
                                  or DEFAULT_MIN_HOTKEY)
        self.v_file = tk.StringVar(value=cfg.get('file') or '')
        # 翻页按键（预设多选 + 自定义）
        self.key_vars = {}
        self.custom_var = {}
        self.custom_vk = {}
        self.custom_btn = {}
        self._capture = None
        cp = cfg.get('click_page') or {}
        self.v_click_l = tk.BooleanVar(value=bool(cp.get('left', True)))
        self.v_click_r = tk.BooleanVar(value=bool(cp.get('right', True)))
        self.v_taskbar = tk.BooleanVar(value=bool(cfg.get('show_taskbar')))
        self.v_tray = tk.BooleanVar(value=bool(cfg.get('show_tray')))

        pad = {'padx': 6, 'pady': 4}
        self.btn_fg = tk.Button(
            self, textvariable=self.v_fg, width=10, relief='groove',
            bg=self.v_fg.get(), command=self._pick_color)
        self.v_fg.trace_add(
            'write', lambda *_: self.btn_fg.configure(
                bg=self.v_fg.get() or '#e4e4e4'))
        rows = [
            ('字体', ttk.Combobox(self, textvariable=self.v_family,
                                  values=families, width=24)),
            ('字号', ttk.Spinbox(self, from_=8, to=60,
                                 textvariable=self.v_font, width=8)),
            ('字体颜色', self.btn_fg),
            ('背景无底色（全透明，只显示文字）',
             ttk.Checkbutton(self, variable=self.v_tbg)),
            ('整体透明度', ttk.Scale(self, from_=0.2, to=1.0,
                                     variable=self.v_alpha, length=200)),
            ('窗口置顶', ttk.Checkbutton(self, variable=self.v_top)),
            ('关闭快捷键', ttk.Entry(self, textvariable=self.v_exit,
                                     width=16)),
            ('最小化快捷键', ttk.Entry(self, textvariable=self.v_min,
                                       width=16)),
            ('上一页按键', self._build_key_row('prev')),
            ('下一页按键', self._build_key_row('next')),
            ('鼠标单击翻页', self._build_click_row()),
            ('任务栏图标', ttk.Checkbutton(self, variable=self.v_taskbar)),
            ('托盘图标（后台图标）',
             ttk.Checkbutton(self, variable=self.v_tray)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(self, text=label).grid(row=i, column=0,
                                             sticky='w', **pad)
            widget.grid(row=i, column=1, sticky='w', **pad)

        ttk.Label(self, foreground='#666',
                  text='窗口大小：直接拖动阅读窗口的边缘或四角即可，'
                       '显示行数自动填满').grid(
            row=len(rows), column=0, columnspan=2, sticky='w', **pad)
        ttk.Label(self, foreground='#666', wraplength=340,
                  text='关闭/最小化快捷键格式如 Ctrl+Shift+Q、Alt+F2，'
                       '需含 Ctrl/Alt/Shift 之一；最小化快捷键全局生效'
                       '（默认 Ctrl+Shift+M，隐藏/唤回窗口）').grid(
            row=len(rows) + 1, column=0, columnspan=2, sticky='w', **pad)

        # 文件选择 + 导入
        row = len(rows) + 2
        ttk.Label(self, text='当前文件').grid(row=row, column=0,
                                              sticky='w', **pad)
        self.combo = ttk.Combobox(self, textvariable=self.v_file,
                                  values=self._files(), state='readonly',
                                  width=30)
        self.combo.grid(row=row, column=1, sticky='w', **pad)
        self.combo.bind('<<ComboboxSelected>>', self._on_file)

        row += 1
        ttk.Button(self, text='导入小说到 novels 目录',
                   command=self._import).grid(row=row, column=0,
                                              sticky='w', **pad)
        ttk.Label(self, text=NOVEL_DIR, foreground='#888',
                  wraplength=320).grid(row=row, column=1, sticky='w', **pad)

        row += 1
        ttk.Label(self, foreground='#888',
                  text='退出：Ctrl+Shift+Q').grid(row=row, column=0,
                                                  sticky='w', **pad)

        # 免责声明
        row += 1
        ttk.Label(
            self, foreground='#999', wraplength=340, justify='left',
            text='本内容仅供学习、分享、交流使用，禁止商用，'
                 '版权归原作者所有。如有bug或问题可发送邮件至'
                 '1965552741@qq.com').grid(
            row=row, column=0, columnspan=2, sticky='w', **pad)

        row += 1
        bar = ttk.Frame(self)
        bar.grid(row=row, column=0, columnspan=2, sticky='e', **pad)
        ttk.Button(bar, text='♥ 打赏支持',
                   command=self._open_donate).pack(side='left', padx=4)
        ttk.Button(bar, text='应用', command=self._apply).pack(
            side='left', padx=4)
        ttk.Button(bar, text='关闭', command=self.destroy).pack(
            side='left', padx=4)

        # 设置窗口内快捷键：F1 走统一的开关逻辑（带速率限制，
        # 防止连发或重复触发导致刚打开就被关掉）
        self.bind('<Escape>', self._close_self)
        self.bind('<F1>', self._toggle_reader)
        self.bind('<KeyPress>', self._on_key_capture)   # 自定义按键录制
        self._load_key_state()

        self.update_idletasks()
        rx, ry = reader.winfo_rootx(), reader.winfo_rooty()
        self.geometry(
            f'+{max(0, rx + reader.winfo_width() // 4)}'
            f'+{max(0, ry + 60)}')
        self.lift()      # 防止被其它置顶窗口盖住
        self.focus_force()

    def _exit_app(self, _event=None):
        self.r.on_close()
        return 'break'

    def _open_donate(self):
        DonateWindow(self)

    def _toggle_reader(self, _event=None):
        self.r.toggle_settings()
        return 'break'

    # ---------------- 翻页按键配置 ----------------

    def _build_click_row(self):
        frame = ttk.Frame(self)
        ttk.Checkbutton(frame, text='左键=下一页',
                        variable=self.v_click_l).grid(
            row=0, column=0, sticky='w')
        ttk.Checkbutton(frame, text='右键=上一页',
                        variable=self.v_click_r).grid(
            row=0, column=1, sticky='w', padx=(8, 0))
        return frame

    def _build_key_row(self, d):
        frame = ttk.Frame(self)
        for col, (vk, name) in enumerate(PAGE_PRESETS[d]):
            var = tk.BooleanVar(value=False)
            self.key_vars[(d, vk)] = var
            ttk.Checkbutton(frame, text=name, variable=var,
                            width=5).grid(row=0, column=col, sticky='w')
        n = len(PAGE_PRESETS[d])
        self.custom_var[d] = tk.BooleanVar(value=False)
        self.custom_vk[d] = None
        cb = ttk.Checkbutton(frame, text='自定义',
                             variable=self.custom_var[d], width=10)
        cb.grid(row=0, column=n, sticky='w')
        self.custom_btn[d] = cb
        ttk.Button(frame, text='录制', width=5,
                   command=lambda dd=d: self._start_capture(dd)).grid(
            row=0, column=n + 1, sticky='w')
        ttk.Button(frame, text='×', width=2,
                   command=lambda dd=d: self._clear_custom(dd)).grid(
            row=0, column=n + 2, sticky='w')
        return frame

    def _start_capture(self, d):
        self._capture = d
        self.custom_btn[d].configure(text='请按任意键…')

    def _clear_custom(self, d):
        self._capture = None
        self.custom_vk[d] = None
        self.custom_var[d].set(False)
        self.custom_btn[d].configure(text='自定义')

    def _on_key_capture(self, event):
        if not self._capture:
            return
        if event.keysym in ('Shift_L', 'Shift_R', 'Control_L', 'Control_R',
                            'Alt_L', 'Alt_R', 'Caps_Lock'):
            return                      # 纯修饰键不算，继续等待
        d = self._capture
        self._capture = None
        vk = int(event.keycode)
        name = event.keysym.upper() if len(event.keysym) == 1 \
            else event.keysym
        self.custom_vk[d] = (vk, name)
        self.custom_var[d].set(True)
        self.custom_btn[d].configure(text=f'自定义[{name}]')

    def _load_key_state(self):
        active = self.r.cfg.get('page_keys') or {}
        for d in ('prev', 'next'):
            preset_vks = {p[0] for p in PAGE_PRESETS[d]}
            for e in active.get(d, []) or []:
                try:
                    vk = int(e['vk'])
                    name = str(e.get('name', '')) or f'键{vk}'
                except (KeyError, TypeError, ValueError):
                    continue
                if vk in preset_vks:
                    self.key_vars[(d, vk)].set(True)
                else:
                    self.custom_vk[d] = (vk, name)
                    self.custom_var[d].set(True)
                    self.custom_btn[d].configure(text=f'自定义[{name}]')

    def _collect_page_keys(self):
        pk = {'prev': [], 'next': []}
        for d in ('prev', 'next'):
            for vk, name in PAGE_PRESETS[d]:
                if self.key_vars[(d, vk)].get():
                    pk[d].append({'vk': vk, 'name': name})
            if self.custom_var[d].get() and self.custom_vk.get(d):
                vk, name = self.custom_vk[d]
                if not any(e['vk'] == vk for e in pk[d]):
                    pk[d].append({'vk': vk, 'name': name})
        return pk

    def _close_self(self, _event=None):
        self.destroy()
        return 'break'

    def _files(self):
        return list_novels()

    def _on_file(self, _event=None):
        name = self.v_file.get()
        if name:
            self.r.open_file(name)

    def _import(self):
        path = filedialog.askopenfilename(
            parent=self,
            title='选择要导入的小说文件',
            filetypes=[('小说文件',
                        '*.txt *.epub *.mobi *.azw3 *.prc *.pdf '
                        '*.docx *.html *.htm'),
                       ('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            text = extract_novel_text(path)
        except Exception as e:
            messagebox.showerror('导入失败', f'解析文件失败：\n{e}',
                                 parent=self)
            return
        text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not text:
            messagebox.showwarning('导入失败', '未能从文件中提取到文字',
                                   parent=self)
            return
        # 统一转成纯文本 .txt 存入 novels 目录
        name = os.path.splitext(os.path.basename(path))[0] + '.txt'
        dst = os.path.join(NOVEL_DIR, name)
        base, ext = os.path.splitext(name)
        n = 2
        while os.path.exists(dst) and \
                os.path.abspath(path) != os.path.abspath(dst):
            dst = os.path.join(NOVEL_DIR, f'{base}({n}){ext}')
            n += 1
        name = os.path.basename(dst)
        if os.path.abspath(path) != os.path.abspath(dst):
            try:
                with open(dst, 'w', encoding='utf-8') as f:
                    f.write(text + '\n')
            except OSError as e:
                messagebox.showerror('导入失败', str(e), parent=self)
                return
        self.combo['values'] = self._files()
        self.v_file.set(name)
        self.r.open_file(name)

    def _pick_color(self):
        result = colorchooser.askcolor(
            color=self.v_fg.get(), parent=self, title='选择字体颜色')
        if result and result[1]:
            self.v_fg.set(result[1])

    def _apply(self):
        cfg = self.r.cfg
        try:
            cfg['font_size'] = max(8, min(60, int(self.v_font.get())))
            cfg['opacity'] = max(0.2, min(1.0, float(self.v_alpha.get())))
        except (tk.TclError, ValueError):
            messagebox.showwarning('设置', '请输入有效数值', parent=self)
            return
        cfg['font_family'] = self.v_family.get().strip()
        fg = self.v_fg.get().strip()
        if fg:
            cfg['fg'] = fg
        cfg['transparent_bg'] = bool(self.v_tbg.get())
        hot = self.v_exit.get().strip()
        if not parse_hotkey(hot):
            messagebox.showwarning(
                '设置',
                '关闭快捷键无法识别：\n'
                '请使用类似 Ctrl+Shift+Q、Alt+F2 的格式，\n'
                '且至少包含一个 Ctrl / Alt / Shift',
                parent=self)
            return
        cfg['exit_hotkey'] = hot
        self.r.apply_exit_hotkey()
        mhot = self.v_min.get().strip()
        if not parse_hotkey(mhot):
            messagebox.showwarning(
                '设置',
                '最小化快捷键无法识别：\n'
                '请使用类似 Ctrl+Shift+M、Alt+F2 的格式，\n'
                '且至少包含一个 Ctrl / Alt / Shift',
                parent=self)
            return
        if parse_hotkey(mhot)['vk'] == parse_hotkey(hot)['vk'] and \
                parse_hotkey(mhot)['tkseq'] == parse_hotkey(hot)['tkseq']:
            messagebox.showwarning(
                '设置', '最小化快捷键与关闭快捷键相同，请换一个。', parent=self)
            return
        cfg['min_hotkey'] = mhot
        self.r.apply_min_hotkey()
        cfg['page_keys'] = self._collect_page_keys()
        self.r.gk.set_page_keys(cfg['page_keys'])
        cfg['click_page'] = {'left': bool(self.v_click_l.get()),
                             'right': bool(self.v_click_r.get())}
        cfg['topmost'] = bool(self.v_top.get())
        cfg['show_taskbar'] = bool(self.v_taskbar.get())
        cfg['show_tray'] = bool(self.v_tray.get())
        self.r.apply_settings()
        save_cfg_file(cfg)


class DonateWindow(tk.Toplevel):
    """打赏窗口：显示隐藏存储中的收款码（导入/清除均在窗口内完成）"""

    def __init__(self, master):
        super().__init__(master)
        self.title('打赏支持')
        self.resizable(False, False)
        try:
            self.attributes('-topmost', True)
        except tk.TclError:
            pass
        migrate_legacy_donate()

        ttk.Label(self, text='感谢支持！扫码请作者喝杯茶',
                  font=('Microsoft YaHei UI', 11, 'bold')).pack(
            pady=(12, 6), padx=16)

        self._photos = []
        self._items = load_donate_items()
        self._box = ttk.Frame(self)
        self._box.pack(padx=12, pady=4)
        self._hint = ttk.Label(self, foreground='#666',
                               text='请使用对应的 App 扫码')
        self._draw()

        # 管理栏默认隐藏：Ctrl+Shift+D + 管理密码 才显示（防他人换码转卖）
        self._bar = ttk.Frame(self)
        ttk.Button(self._bar, text='导入支付宝码…',
                   command=lambda: self._import('alipay')).pack(
            side='left', padx=4)
        ttk.Button(self._bar, text='导入微信码…',
                   command=lambda: self._import('wechat')).pack(
            side='left', padx=4)
        ttk.Button(self._bar, text='清空', command=self._clear).pack(
            side='left', padx=4)
        self.bind('<Control-Shift-D>', self._unlock)

        url = (master.r.cfg.get('donate_url') or '').strip() \
            if hasattr(master, 'r') else ''
        if url:
            ttk.Button(self, text='打开转账链接',
                       command=lambda: webbrowser.open(url)).pack(pady=(4, 0))

        ttk.Button(self, text='关闭', command=self.destroy).pack(pady=(8, 12))
        self.update_idletasks()
        rx, ry = master.winfo_rootx(), master.winfo_rooty()
        self.geometry(f'+{max(0, rx + 40)}+{max(0, ry + 40)}')
        self.focus_force()

    KINDS = (('alipay', '支付宝打赏'), ('wechat', '微信打赏'))

    @staticmethod
    def _kind_of(it):
        """条目类型：显式 kind 优先；旧数据按文件名猜测，猜不出归支付宝"""
        k = it.get('kind')
        if k in ('alipay', 'wechat'):
            return k
        name = (it.get('name') or '').lower()
        if any(s in name for s in ('wechat', 'weixin', '微信')):
            return 'wechat'
        return 'alipay'

    def _draw(self):
        for w in self._box.winfo_children():
            w.destroy()
        self._hint.pack_forget()
        self._photos = []
        if self._items == '__invalid__':
            ttk.Label(
                self._box, foreground='#c60', wraplength=380, justify='left',
                text='收款数据无效（可能已被替换），请联系作者。').pack(
                padx=4, pady=8)
            return
        by_kind = {}
        for it in (self._items or []):
            by_kind.setdefault(self._kind_of(it), it)
        shown = 0
        for kind, title in self.KINDS:
            it = by_kind.get(kind)
            if not it:
                continue
            try:
                ph = tk.PhotoImage(data=it['b64'])
            except tk.TclError:
                continue
            w = int(ph.width())
            if w > 220:
                ph = ph.subsample((w + 219) // 220)
            self._photos.append(ph)
            col = ttk.Frame(self._box)
            col.pack(side='left', padx=10)
            # 文字说明：标明是哪个平台的码，避免扫错
            ttk.Label(col, text=title,
                      font=('Microsoft YaHei UI', 10, 'bold'),
                      anchor='center').pack(pady=(2, 4))
            ttk.Label(col, image=ph).pack()
            shown += 1
        if not shown:
            ttk.Label(
                self._box, foreground='#666', wraplength=380, justify='left',
                text='暂未设置收款码。').pack(padx=4, pady=8)
        else:
            self._hint.pack(pady=(6, 0))

    def _unlock(self, _event=None):
        """隐藏入口：管理密码验证后才显示导入/清空（防他人换码转卖）"""
        pw = simpledialog.askstring('管理验证', '请输入管理密码：',
                                    show='*', parent=self)
        if pw is None:
            return 'break'
        if pw != MASTER_PW:
            messagebox.showerror('验证失败', '密码不正确。', parent=self)
            return 'break'
        if self._items == '__invalid__':
            self._items = []
        self._bar.pack(pady=(6, 0))
        self._draw()
        return 'break'

    def _import(self, kind):
        title = '支付宝' if kind == 'alipay' else '微信'
        # 仅 PNG/GIF：tk.PhotoImage 不支持 JPG，选 JPG 会显示失败
        path = filedialog.askopenfilename(
            parent=self, title=f'选择{title}收款码图片',
            filetypes=[('图片', '*.png *.gif')])
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('ascii')
        except OSError as e:
            messagebox.showerror('导入失败', str(e), parent=self)
            return
        # 同类型只保留最新导入的一张；显示名固定为类型名（不泄露文件名）
        items = [it for it in self._items if self._kind_of(it) != kind]
        items.append({'name': title, 'kind': kind, 'b64': b64})
        self._items = items
        save_donate_items(items)
        self._draw()

    def _clear(self):
        if not self._items:
            return
        if not messagebox.askyesno('清空', '确定移除全部收款码？',
                                   parent=self):
            return
        self._items = []
        save_donate_items([])
        self._draw()


def main():
    if sys.platform == 'win32':
        try:
            # 声明 DPI 感知：保证鼠标钩子坐标与 GetWindowRect/SetWindowPos
            # 处于同一坐标系（系统缩放≠100% 时否则命中判定会错位）
            ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    app = Reader()
    app.mainloop()


if __name__ == '__main__':
    main()
