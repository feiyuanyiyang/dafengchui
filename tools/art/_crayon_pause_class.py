# --- 暂停遮罩（蜡笔版）布局：整屏合成，按钮是白描边镂空长条 ---
CRAYON_PAUSE_TITLE_Y = 252
CRAYON_PAUSE_HINT_Y = 736
CRAYON_PAUSE_BUTTON_SIZE = (420, 74)
CRAYON_PAUSE_BUTTON_X = (WINDOW_WIDTH - CRAYON_PAUSE_BUTTON_SIZE[0]) // 2
CRAYON_PAUSE_BUTTON_YS = (378, 468, 558, 648)
CRAYON_PAUSE_SINGLE_Y = 468
CRAYON_PAUSE_RADIUS = 16
CRAYON_PAUSE_FRAME_WIDTH = 3
CRAYON_PAUSE_FRAME = (255, 255, 255, 230)
CRAYON_PAUSE_FRAME_HOVER = (255, 255, 255, 255)
CRAYON_PAUSE_WASH = (255, 255, 255, 38)
CRAYON_PAUSE_VEIL = (16, 38, 28, 112)
CRAYON_PAUSE_BLUR = 16
CRAYON_PAUSE_ASSETS = (
    "pause_title", "settings_title", "pause_hint", "settings_hint",
    "btn_continue", "btn_restart", "btn_settings", "btn_quit", "btn_back",
)


class CrayonPauseView:
    """暂停/设置遮罩（蜡笔版）：抓一帧画面 → 模糊压暗 → 整屏合成一张图。

    为什么必须整屏合成：Canvas 图片的透明像素只透出画布底色，不透出下方
    item，"模糊背景上浮着镂空按钮"没法分层画。合成好之后 Canvas 只贴一张图，
    hover 时重画这一张（约 10ms，只在鼠标进出按钮时发生）。

    命中测试是纯坐标矩形，由应用层把 <Motion>/<Button-1> 转发进来。
    """

    def __init__(self, canvas, assets, on_continue, on_restart, on_settings,
                 on_quit, on_back):
        self.canvas = canvas
        self.assets = assets
        self._callbacks = {
            "continue": on_continue,
            "restart": on_restart,
            "settings": on_settings,
            "quit": on_quit,
            "back": on_back,
        }
        self.mode = None
        self.visible = False
        self._backdrop = None
        self._photo = None
        self._hit_rects = []
        self._hover = None
        self._menu_buttons = (
            ("continue", "btn_continue", CRAYON_PAUSE_BUTTON_YS[0]),
            ("restart", "btn_restart", CRAYON_PAUSE_BUTTON_YS[1]),
            ("settings", "btn_settings", CRAYON_PAUSE_BUTTON_YS[2]),
            ("quit", "btn_quit", CRAYON_PAUSE_BUTTON_YS[3]),
        )

        self.image_item = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.countdown_number = canvas.create_text(
            WINDOW_WIDTH // 2,
            PAUSE_COUNTDOWN_Y,
            text="",
            anchor="center",
            font=(FONT_FAMILY, FONT_COUNTDOWN),
            fill=COLOR_GOD,
            state="hidden",
        )
        self.countdown_suffix = canvas.create_text(
            WINDOW_WIDTH // 2,
            PAUSE_COUNTDOWN_SUFFIX_Y,
            text="秒后回到游戏",
            anchor="center",
            font=(FONT_FAMILY, FONT_NORMAL),
            fill=COLOR_TEXT,
            state="hidden",
        )

    # ------------------------------------------------------------ 对外接口
    def show_menu(self):
        self._ensure_backdrop()
        self.mode = "menu"
        self.visible = True
        self._hover = None
        self._hide_countdown()
        self._render()

    def show_settings(self):
        self._ensure_backdrop()
        self.mode = "settings"
        self.visible = True
        self._hover = None
        self._hide_countdown()
        self._render()

    def show_countdown(self, seconds):
        self._ensure_backdrop()
        self.mode = "countdown"
        self.visible = True
        self._hit_rects = []
        self._render()
        self.canvas.itemconfigure(self.countdown_number, text="%d" % max(1, seconds),
                                  state="normal")
        self.canvas.itemconfigure(self.countdown_suffix, state="normal")

    def hide(self):
        self.mode = None
        self.visible = False
        self._hover = None
        self._hit_rects = []
        self._backdrop = None
        self._hide_countdown()
        self.canvas.itemconfigure(self.image_item, state="hidden")

    def on_motion(self, x, y):
        """返回 True 表示这次移动被暂停界面消费掉了。"""
        if not self.visible or self.mode == "countdown":
            return False
        key = self._hit_test(x, y)
        if key != self._hover:
            self._hover = key
            self._render()
        return True

    def on_click(self, x, y):
        if not self.visible or self.mode == "countdown":
            return False
        key = self._hit_test(x, y)
        if key is None:
            return False
        callback = self._callbacks.get(key)
        if callback is not None:
            callback()
        return True

    # ------------------------------------------------------------ 内部
    def _hide_countdown(self):
        self.canvas.itemconfigure(self.countdown_number, state="hidden")
        self.canvas.itemconfigure(self.countdown_suffix, state="hidden")

    def _hit_test(self, x, y):
        for key, (x1, y1, x2, y2) in self._hit_rects:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return key
        return None

    def _ensure_backdrop(self):
        """一次暂停只抓一次图：设置页/倒计时都复用同一张底。"""
        if self._backdrop is not None:
            return
        self._backdrop = self._capture()
        if self._backdrop is None:
            from PIL import Image
            self._backdrop = Image.new(
                "RGBA", (WINDOW_WIDTH, WINDOW_HEIGHT), CRAYON_PAUSE_VEIL)

    def _capture(self):
        try:
            from PIL import Image, ImageFilter, ImageGrab
        except Exception:
            return None
        self.canvas.update_idletasks()
        left = self.canvas.winfo_rootx()
        top = self.canvas.winfo_rooty()
        try:
            shot = ImageGrab.grab(
                bbox=(left, top, left + WINDOW_WIDTH, top + WINDOW_HEIGHT))
        except Exception:
            return None
        if shot.size != (WINDOW_WIDTH, WINDOW_HEIGHT):
            try:
                shot = shot.resize((WINDOW_WIDTH, WINDOW_HEIGHT))
            except Exception:
                return None
        blurred = shot.convert("RGB").filter(
            ImageFilter.GaussianBlur(CRAYON_PAUSE_BLUR))
        veil = Image.new("RGBA", blurred.size, CRAYON_PAUSE_VEIL)
        return Image.alpha_composite(blurred.convert("RGBA"), veil)

    def _layout(self):
        if self.mode == "settings":
            return (
                "settings_title",
                "settings_hint",
                (("back", "btn_back", CRAYON_PAUSE_SINGLE_Y),),
            )
        return ("pause_title", "pause_hint", self._menu_buttons)

    def _render(self):
        from PIL import Image, ImageDraw, ImageTk

        base = self._backdrop
        if base is None:
            return
        if self.mode == "countdown":
            self._hit_rects = []
            self._photo = ImageTk.PhotoImage(base, master=self.canvas)
            self.canvas.itemconfigure(self.image_item, image=self._photo, state="normal")
            return

        title, hint, buttons = self._layout()
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        half_width = CRAYON_PAUSE_BUTTON_SIZE[0] // 2
        half_height = CRAYON_PAUSE_BUTTON_SIZE[1] // 2
        rects = []
        for key, _asset, center_y in buttons:
            box = (
                CRAYON_PAUSE_BUTTON_X,
                center_y - half_height,
                CRAYON_PAUSE_BUTTON_X + CRAYON_PAUSE_BUTTON_SIZE[0],
                center_y + half_height,
            )
            hover = key == self._hover
            if hover:
                draw.rounded_rectangle(
                    box, radius=CRAYON_PAUSE_RADIUS, fill=CRAYON_PAUSE_WASH)
            draw.rounded_rectangle(
                box,
                radius=CRAYON_PAUSE_RADIUS,
                outline=CRAYON_PAUSE_FRAME_HOVER if hover else CRAYON_PAUSE_FRAME,
                width=CRAYON_PAUSE_FRAME_WIDTH + (1 if hover else 0),
            )
            rects.append((key, box))

        composed = Image.alpha_composite(base.copy(), overlay)
        self._paste(composed, title, (WINDOW_WIDTH // 2, CRAYON_PAUSE_TITLE_Y))
        for _key, asset, center_y in buttons:
            self._paste(composed, asset, (WINDOW_WIDTH // 2, center_y))
        self._paste(composed, hint, (WINDOW_WIDTH // 2, CRAYON_PAUSE_HINT_Y))

        self._hit_rects = rects
        self._photo = ImageTk.PhotoImage(composed, master=self.canvas)
        self.canvas.itemconfigure(self.image_item, image=self._photo, state="normal")

    def _paste(self, base, name, center):
        if self.assets is None:
            return
        image = self.assets.ui_image(name)
        if image is None:
            return
        x = int(center[0] - image.width / 2)
        y = int(center[1] - image.height / 2)
        base.alpha_composite(image, (x, y))


