# -*- coding: utf-8 -*-
"""
桌宠 —— 让一张图片里的角色在你的桌面上爬来爬去，可以用鼠标随意拖拽。

用法:
    python desktop_pet.py        (或双击 启动桌宠.bat)

功能:
    * 桌宠沿桌面底部 (任务栏上方) 来回爬行，途中会随机停下、张望
    * 拖到半空中松手 -> "啪叽"掉落到地面
    * 拖到屏幕左/右边缘松手 -> 沿边缘向上爬
    * 快速点一下桌宠 -> 它会蹦起来扑腾
    * 滚轮调节大小，双击放大
    * 右键菜单：暂停/继续、回到地面、调整大小、退出
    * 单实例运行：已有一个桌宠时，再次启动会提示并自动退出

依赖：Python 3 + Pillow

更换形象:
    把新图片放进 pets/ 文件夹，运行 python make_assets.py,
    从列表中选择编号即可。详见 README.md
"""
import math
import os
import sys
import random
import time
import tkinter as tk

try:
    from PIL import Image, ImageTk
except ImportError:
    print("缺少 Pillow, 请先执行：pip install pillow")
    sys.exit(1)

# Windows: 让 DPI 感知生效，逻辑坐标与物理像素一致，避免透明色错位
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET = os.path.join(SCRIPT_DIR, "assets", "pet.png")   # 透明背景素材
TRANS_KEY = "#fe00fe"          # 透明魔术色
TRANS_RGB = (0xFE, 0x00, 0xFE)
BASE_WIDTH = 120               # 默认显示宽度 (px)
MIN_W, MAX_W = 60, 320
RESIZE_WHEEL_STEP = 10           # 滚轮每次缩放幅度 (px)
RESIZE_MENU_STEP = 20            # 右键菜单每次缩放幅度 (px)
RESIZE_DOUBLE_STEP = 20          # 双击放大每次幅度 (px)
TICK_MS = 33                   # 约 30fps
EDGE_SNAP = 70                 # 距屏幕左右边缘多近算"贴边" -> 可爬墙
FALL_GRAVITY = 0.55            # 下落/跳跃重力 px/帧^2
WALL_SPEED = 2.2               # 爬墙速度 px/帧
MAX_TILT = 18                  # 最大摇摆角 (度)


# 模块级变量：保留以兼容旧引用（现已只使用主显示器）
_bounds = [None, None, None, None]


def get_work_area():
    """返回主显示器的工作区 (不含扩展屏)。"""
    global _bounds
    _bounds = [None, None, None, None]

    if sys.platform == "win32":
        try:
            import ctypes.wintypes as wt
            user32 = ctypes.windll.user32

            # SPI_GETWORKAREA 返回主显示器的工作区
            # (扣除任务栏等停靠窗口，且不包含扩展屏)
            SPI_GETWORKAREA = 0x0030
            rect = wt.RECT()
            user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            return (rect.left, rect.top,
                    rect.right - rect.left, rect.bottom - rect.top)
        except Exception:
            pass

    return None


class DesktopPet:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-alpha", 0.99)  # 修复个别 Win 版本透明色渲染
        except tk.TclError:
            pass
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANS_KEY)
        self.root.wm_attributes("-transparentcolor", TRANS_KEY)

        if not os.path.isfile(ASSET):
            print("找不到素材 assets/pet.png，请先运行：python make_assets.py")
            sys.exit(1)
        src = Image.open(ASSET)
        self.aspect = src.height / src.width
        src.close()

        # 主显示器工作区（不含扩展屏、任务栏）
        wa = get_work_area()
        if wa is None:
            self.wa_x, self.wa_y = 0, 0
            self.wa_w = self.root.winfo_screenwidth()
            self.wa_h = self.root.winfo_screenheight()
        else:
            self.wa_x, self.wa_y, self.wa_w, self.wa_h = wa
        self.right = self.wa_x + self.wa_w
        self.bottom = self.wa_y + self.wa_h

        # 状态
        self.state = "WALK"
        self.angle = 0.0
        self.flip = False
        self.dir = random.choice((-1, 1))
        self.vy = 0.0
        self.walk_ticks = 0
        self.pause_ticks = 0
        self.look_ticks = 0
        self.dragging = False
        self.pressed = False
        self.moved = False
        self.press_xy = None
        self.press_t = 0.0
        self.paused = False
        self.x = self.y = 0
        self.pet_w = BASE_WIDTH
        self.frames = {}                    # (w, angle, flip) -> PhotoImage

        # 计算最大窗口尺寸 (按 MAX_W + MAX_TILT 预留)
        self.max_frame_w, self.max_frame_h = self._calc_frame_size(MAX_W)
        self.cur_frame_w = self.max_frame_w
        self.cur_frame_h = self.max_frame_h

        # 创建固定尺寸的画布和窗口
        self.canvas = tk.Canvas(self.root, bg=TRANS_KEY,
                                width=self.max_frame_w, height=self.max_frame_h,
                                highlightthickness=0, bd=0, takefocus=0)
        self.canvas.pack()
        self.item = self.canvas.create_image(
            self.max_frame_w // 2, self.max_frame_h, anchor="s")
        self._bind_events()

        # 初始位置（在主屏工作区内随机）
        half = self.pet_w // 2
        lo, hi = self.wa_x + half + 50, self.right - half - 50
        x = random.randint(lo, max(lo + 1, hi))
        self._place(x, self.ground_y())

        self.root.deiconify()
        self.root.after(80, self._tick)

    def _calc_frame_size(self, w):
        """按最大摇摆角预留画布尺寸。"""
        h = int(round(w * self.aspect))
        a = math.radians(MAX_TILT)
        fw = int(w * math.cos(a) + h * math.sin(a)) + 4
        fh = int(w * math.sin(a) + h * math.cos(a)) + 4
        return fw, fh

    def _render_frame(self, w, angle, flip):
        key = (w, int(angle), flip)
        img = self.frames.get(key)
        if img is not None:
            return img
        h = int(round(w * self.aspect))
        src = Image.open(ASSET).convert("RGBA").resize((w, h), Image.LANCZOS)
        if flip:
            src = src.transpose(Image.FLIP_LEFT_RIGHT)
        if angle:
            src = src.rotate(angle, resample=Image.BICUBIC, expand=True)
        # 透明色键不支持半透明，把边缘二值化，避免品红魔术色渗出成描边
        src.putalpha(src.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
        # 图片居中底部绘制到最大尺寸画布
        bg = Image.new("RGBA", (self.max_frame_w, self.max_frame_h), TRANS_KEY)
        px = (self.max_frame_w - src.width) // 2
        py = self.max_frame_h - src.height
        bg.alpha_composite(src, (px, py))
        img = ImageTk.PhotoImage(bg.convert("RGB"))
        self.frames[key] = img
        return img

    def _quant_angle(self, a):
        return int(round(a / 2.0) * 2)

    def _show(self, angle, flip):
        img = self._render_frame(self.pet_w, self._quant_angle(angle), flip)
        self.canvas.itemconfigure(self.item, image=img)

    def _place(self, x, y):
        """x, y 是图片底部中心的屏幕坐标。"""
        half = self.pet_w // 2
        x = max(self.wa_x + half, min(self.right - half, x))
        y = max(self.wa_y, min(self.bottom, y))
        self.x, self.y = int(x), int(y)
        # 窗口左上角坐标
        win_x = self.x - self.max_frame_w // 2
        win_y = self.y - self.max_frame_h
        self.root.geometry("+%d+%d" % (win_x, win_y))

    def ground_y(self):
        return self.bottom

    # ---------- 行为循环 ----------
    def _tick(self):
        if self.paused or self.dragging:
            self.root.after(TICK_MS, self._tick)
            return

        s = self.state
        if s == "WALK":
            self._do_walk()
        elif s == "PAUSE":
            self.pause_ticks -= 1
            if self.pause_ticks <= 0:
                self.state = "WALK"
                self.dir = random.choice((-1, 1))
            self._show(0, self.flip)
        elif s == "LOOK":
            self.look_ticks -= 1
            self.angle = 10 * math.sin(self.look_ticks * 0.15)
            self._show(self.angle, self.flip)
            if self.look_ticks <= 0:
                self.state = "WALK"
        elif s == "FALL":
            self._do_fall()
        elif s == "CLIMB":
            self._do_climb()
        elif s == "POKE":
            self.vy += FALL_GRAVITY
            ny = self.y + self.vy
            self.angle = random.uniform(-16, 16)
            if ny >= self.ground_y():
                self._place(self.x, self.ground_y())
                self.state = "WALK"
                self.angle = 0
            else:
                self._place(self.x, ny)
            self._show(self.angle, self.flip)

        self.root.after(TICK_MS, self._tick)

    def _do_walk(self):
        self.walk_ticks += 1
        bob = abs(math.sin(self.walk_ticks * 0.35)) * 3
        self.angle = math.sin(self.walk_ticks * 0.35) * 6
        nx = self.x + self.dir * 2.0
        half = self.pet_w // 2
        if nx <= self.wa_x + half:
            nx, self.dir = self.wa_x + half, 1
        elif nx + half >= self.right:
            nx, self.dir = self.right - half, -1
        self.flip = self.dir < 0
        self._place(nx, self.ground_y() - bob)
        self._show(self.angle, self.flip)
        r = random.random()
        if r < 0.004:
            self.state = "PAUSE"
            self.pause_ticks = random.randint(30, 90)
        elif r < 0.008:
            self.state = "LOOK"
            self.look_ticks = random.randint(25, 50)

    def _do_fall(self):
        self.vy += FALL_GRAVITY
        ny = self.y + self.vy
        if ny >= self.ground_y():
            self._place(self.x, self.ground_y())
            self.state = "PAUSE"
            self.pause_ticks = 12
            self.angle = 0
        else:
            self._place(self.x, ny)
            self.angle = max(-MAX_TILT, min(MAX_TILT, self.angle + 1.2))
        self._show(self.angle, self.flip)

    def _do_climb(self):
        ny = self.y - WALL_SPEED
        if ny <= self.wa_y:
            self.state = "FALL"
            self.vy = 0
            ny = self.y
        self._place(self.x, ny)
        on_left = self.x - self.wa_x < self.wa_w / 2
        self.angle = -6 if on_left else 6
        self._show(self.angle, self.flip)
        if random.random() < 0.008:
            self.state = "FALL"
            self.vy = 0

    # ---------- 鼠标交互 ----------
    def _bind_events(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-3>", self._on_right)

    def _canvas_to_pet(self, cx, cy):
        """画布坐标转成图片本体坐标 (以图片底部中心为原点，向上为正)。"""
        return cx - self.max_frame_w // 2, self.max_frame_h - cy

    def _opaque_at(self, px, py):
        """点击处是否是桌宠本体。"""
        try:
            img = self._render_frame(self.pet_w,
                                     self._quant_angle(self.angle), self.flip)
            rel_x, rel_y = self._canvas_to_pet(px, py)
            # 检查是否在图片范围内
            h = int(round(self.pet_w * self.aspect))
            if not (-self.pet_w // 2 <= rel_x <= self.pet_w // 2 and 0 <= rel_y <= h):
                return False
            # 转换到图片像素坐标
            img_x = rel_x + self.pet_w // 2
            img_y = h - rel_y
            if img.get(img_x, img_y) != TRANS_RGB:
                return True
            # 检查周围像素
            for dx in range(-6, 7, 3):
                for dy in range(-6, 7, 3):
                    x2, y2 = img_x + dx, img_y + dy
                    if 0 <= x2 < img.width() and 0 <= y2 < img.height():
                        if img.get(x2, y2) != TRANS_RGB:
                            return True
            return False
        except Exception:
            return True

    def _on_press(self, ev):
        if not self._opaque_at(ev.x, ev.y):
            return
        self.pressed = True
        self.press_xy = (ev.x_root, ev.y_root)
        self.press_t = time.time()
        self.moved = False
        self._grab_dx = ev.x_root - self.x
        self._grab_dy = ev.y_root - self.y

    def _on_drag(self, ev):
        if not self.pressed:
            return
        if not self.moved:
            if abs(ev.x_root - self.press_xy[0]) + \
               abs(ev.y_root - self.press_xy[1]) < 4:
                return
            self.moved = True
            self.dragging = True
            self.canvas.configure(cursor="fleur")
        nx = ev.x_root - self._grab_dx
        ny = ev.y_root - self._grab_dy
        self._place(nx, max(self.wa_y + 50, ny))
        self._show(0, self.flip)

    def _on_release(self, ev):
        if not self.pressed:
            return
        self.pressed = False
        self.canvas.configure(cursor="")
        if not self.moved:
            if time.time() - self.press_t < 0.25:
                self.state = "POKE"
                self.vy = -7
            self.dragging = False
            return
        self.dragging = False
        half = self.pet_w // 2
        if self.y < self.ground_y() - 25:
            self.state = "FALL"
            self.vy = 0
        elif self.x - half - self.wa_x < EDGE_SNAP:
            self.state = "CLIMB"
            self.flip = False
            self._place(self.wa_x + half, self.y)
        elif self.right - (self.x + half) < EDGE_SNAP:
            self.state = "CLIMB"
            self.flip = True
            self._place(self.right - half, self.y)
        else:
            self.state = "WALK"
            self.dir = -1 if self.x > (self.wa_x + self.right) / 2 else 1
            self._place(self.x, self.ground_y())
            self.angle = 0

    def _on_wheel(self, ev):
        self._resize(self.pet_w + (RESIZE_WHEEL_STEP if ev.delta > 0 else -RESIZE_WHEEL_STEP))

    def _on_double(self, ev):
        if self._opaque_at(ev.x, ev.y):
            self._resize(self.pet_w + RESIZE_DOUBLE_STEP)

    def _resize(self, w):
        w = max(MIN_W, min(MAX_W, int(w)))
        if w == self.pet_w:
            return
        # 保持底部中心不动，只改图片宽度
        self.pet_w = w
        self._place(self.x, self.y)
        self._show(self.angle, self.flip)

    def _on_right(self, ev):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label=("继续" if self.paused else "暂停"),
                      command=self._toggle_pause)
        m.add_command(label="回到地面", command=self._reset_pos)
        m.add_separator()
        m.add_command(label="变小", command=lambda: self._resize(self.pet_w - RESIZE_MENU_STEP))
        m.add_command(label="变大", command=lambda: self._resize(self.pet_w + RESIZE_MENU_STEP))
        m.add_separator()
        m.add_command(label="退出", command=self._quit)
        m.tk_popup(ev.x_root, ev.y_root)

    def _toggle_pause(self):
        self.paused = not self.paused

    def _reset_pos(self):
        self.state = "WALK"
        self.angle = 0
        half = self.pet_w // 2
        self._place(random.randint(self.wa_x + half, self.right - half),
                    self.ground_y())
        self._show(0, self.flip)

    def _quit(self):
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


# ---------- 单实例锁 ----------
_LOCK_HOLDER = None


def acquire_lock():
    global _LOCK_HOLDER
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, "Local\\DesktopPetApp")
        if ctypes.get_last_error() == 183:
            print("桌宠已经在运行了。想重新开始：先右键桌宠 -> 退出，或结束对应 python 进程。")
            sys.exit(0)
        _LOCK_HOLDER = handle
    else:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", 47653))
        except OSError:
            print("桌宠已经在运行了。")
            sys.exit(0)
        _LOCK_HOLDER = s


if __name__ == "__main__":
    acquire_lock()
    DesktopPet().run()
