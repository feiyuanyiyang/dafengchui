# --- HUD 蜡笔贴图（tools/art/bake_crayon_ui.py 生成，尺寸固定，运行时不再缩放） ---
WIND_UI_CODES = ("e", "se", "s", "sw", "w", "nw", "n", "ne")
CRAYON_UI_ASSETS = (
    ("pause", "pause_hover", "heart_full", "heart_empty", "timer_label",
     "digit_dot", "digit_s", "compass_base", "god_badge", "hint")
    + tuple("digit_%d" % index for index in range(10))
    + tuple("compass_arrow_%s" % code for code in WIND_UI_CODES)
    + tuple("wind_%s" % code for code in WIND_UI_CODES)
)

HUD_CRAYON_PAUSE_X = (HUD_PAUSE_X1 + HUD_PAUSE_X2) // 2
HUD_CRAYON_PAUSE_Y = (HUD_PAUSE_Y1 + HUD_PAUSE_Y2) // 2
HUD_CRAYON_HEART_X = HUD_PAUSE_X2 + HUD_PAUSE_GAP + 21
HUD_CRAYON_HEART_Y = HUD_MARGIN + 20
HUD_CRAYON_HEART_STEP = 42
HUD_CRAYON_TIMER_Y = 49
HUD_CRAYON_TIMER_GAP_LABEL = 12
HUD_CRAYON_TIMER_GAP_UNIT = 9
HUD_CRAYON_TIMER_SLOTS = 5          # 够放 999.9
HUD_CRAYON_GOD_X = HUD_MARGIN
HUD_CRAYON_GOD_Y = 104
HUD_CRAYON_COMPASS_X = WINDOW_WIDTH - HUD_MARGIN - 48
HUD_CRAYON_COMPASS_Y = 176
HUD_CRAYON_WIND_NAME_Y = 240


class HudRenderer:
    """生命、计时、风向、提示、调试 HUD、结束遮罩；文本按 10 Hz 节流。

    HUD 有两套实现，接口一致：
        crayon —— 贴 assets/sprites/ui/crayon/ 里预烘好的图（默认）
        legacy —— 缺贴图或没装 Pillow 时回退 Canvas 文字/图形
    渲染层只调 update()/set_pause_hover()/reset_view()，不关心用的是哪套。
    """

    def __init__(self, canvas, assets=None):
        self.canvas = canvas
        self.assets = assets if (assets is not None and assets.ready) else None
        self.font_small = (FONT_FAMILY, FONT_SMALL)
        self.font_normal = (FONT_FAMILY, FONT_NORMAL)
        self.font_big = (FONT_FAMILY, FONT_BIG)
        self.font_heart = (FONT_FAMILY, HEART_FONT_SIZE)

        self._crayon = bool(
            self.assets is not None and self.assets.has_ui(CRAYON_UI_ASSETS)
        )
        if self._crayon:
            self._build_crayon()
        else:
            self._build_legacy()
        self._build_overlay()

        self._last_update = 0.0
        self._hearts = None
        self._wind_index = None
        self._wind_speed = None
        self._god_active = None
        self._overlay_visible = None
        self._overlay_reason_text = None
        self._overlay_score_text = None
        self._timer_key = None
        self._pause_hover = False

    # ------------------------------------------------------------ 蜡笔贴图版
    def _ui(self, name):
        return self.assets.ui_photo(name, self.canvas)

    def _ui_size(self, name):
        return self.assets.ui_size(name)

    def _build_crayon(self):
        c = self.canvas
        # 暂停按钮：既是可点区域，也是所有游戏层的深度锚点（必须最先生成）
        self.pause_photo = self._ui("pause")
        self.pause_hover_photo = self._ui("pause_hover")
        self.pause_bg = c.create_image(
            HUD_CRAYON_PAUSE_X,
            HUD_CRAYON_PAUSE_Y,
            anchor="center",
            image=self.pause_photo,
        )

        self.heart_items = []
        self._heart_sprites = True
        heart_photo = self._ui("heart_full")
        for index in range(PLAYER_MAX_HEARTS):
            self.heart_items.append(
                c.create_image(
                    HUD_CRAYON_HEART_X + index * HUD_CRAYON_HEART_STEP,
                    HUD_CRAYON_HEART_Y,
                    anchor="center",
                    image=heart_photo,
                )
            )

        # 计时：中文标签 + 逐位数字（固定格子保证等宽）+ 单位，整体右对齐
        self.timer_label = c.create_image(
            0, HUD_CRAYON_TIMER_Y, anchor="center", image=self._ui("timer_label"))
        self.timer_slots = [
            c.create_image(0, HUD_CRAYON_TIMER_Y, anchor="center", state="hidden")
            for _ in range(HUD_CRAYON_TIMER_SLOTS)
        ]
        self.timer_unit = c.create_image(
            0, HUD_CRAYON_TIMER_Y, anchor="center", image=self._ui("digit_s"))

        # 风向：右上角罗盘，方向只体现在指针上
        self.compass_base = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_COMPASS_Y,
            anchor="center", image=self._ui("compass_base"))
        self.compass_arrow = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_COMPASS_Y,
            anchor="center", image=self._ui("compass_arrow_e"))
        self.wind_name = c.create_image(
            HUD_CRAYON_COMPASS_X, HUD_CRAYON_WIND_NAME_Y,
            anchor="center", image=self._ui("wind_e"))

        # 上帝模式徽标：属于“玩家状态”，放在生命值下面
        self.god_badge = c.create_image(
            HUD_CRAYON_GOD_X, HUD_CRAYON_GOD_Y,
            anchor="w", image=self._ui("god_badge"), state="hidden")

        self.hint = c.create_image(
            WINDOW_WIDTH // 2, WINDOW_HEIGHT - HINT_Y,
            anchor="center", image=self._ui("hint"))

        self.dev_hud = c.create_text(
            DEV_HUD_MARGIN,
            WINDOW_HEIGHT - DEV_HUD_MARGIN,
            text="",
            anchor="sw",
            justify="left",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )

    # ------------------------------------------------------------ 旧版回退
    def _build_legacy(self):
        canvas = self.canvas
        # 暂停按钮：画成“两条竖杠”，避免依赖字体里的 ⏸ 字形。
        self.pause_bg = canvas.create_rectangle(
            HUD_PAUSE_X1,
            HUD_PAUSE_Y1,
            HUD_PAUSE_X2,
            HUD_PAUSE_Y2,
            fill=COLOR_BUTTON,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
        )
        self.pause_bar_left = canvas.create_rectangle(
            HUD_PAUSE_BAR_X,
            HUD_PAUSE_BAR_Y1,
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_WIDTH,
            HUD_PAUSE_BAR_Y2,
            fill=COLOR_TEXT,
            outline="",
        )
        self.pause_bar_right = canvas.create_rectangle(
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_WIDTH + HUD_PAUSE_BAR_GAP,
            HUD_PAUSE_BAR_Y1,
            HUD_PAUSE_BAR_X + HUD_PAUSE_BAR_TOTAL,
            HUD_PAUSE_BAR_Y2,
            fill=COLOR_TEXT,
            outline="",
        )

        self.heart_items = []
        self._heart_sprites = False
        heart_y = HUD_MARGIN + max(0, (HEART_FONT_SIZE - HEART_SPRITE_SIZE) // 2)
        for index in range(PLAYER_MAX_HEARTS):
            x = HUD_HEART_X + index * (HEART_FONT_SIZE + HEART_GAP)
            photo = self.assets.heart_photo(True, canvas) if self.assets is not None else None
            if photo is not None:
                self.heart_items.append(
                    canvas.create_image(x, heart_y, anchor="nw", image=photo)
                )
                self._heart_sprites = True
                continue
            self.heart_items.append(
                canvas.create_text(
                    x,
                    HUD_MARGIN,
                    text="♥",
                    anchor="nw",
                    font=self.font_heart,
                    fill=COLOR_HEART_FULL,
                )
            )
        heart_slot = HEART_SPRITE_SIZE if self._heart_sprites else HEART_FONT_SIZE
        self.heart_count = canvas.create_text(
            HUD_HEART_X
            + PLAYER_MAX_HEARTS * (heart_slot + HEART_GAP)
            + HEART_COUNT_GAP,
            HUD_MARGIN,
            text="%d/%d" % (PLAYER_START_HEARTS, PLAYER_MAX_HEARTS),
            anchor="nw",
            font=self.font_normal,
            fill=COLOR_TEXT,
        )
        self.timer = canvas.create_text(
            WINDOW_WIDTH - HUD_MARGIN,
            TIMER_Y,
            text="存活 0.0 s",
            anchor="ne",
            font=self.font_normal,
            fill=COLOR_TEXT,
        )
        # 上帝模式徽标：只在开启时显示，给玩家明确的开关反馈。
        self.god_badge = canvas.create_text(
            WINDOW_WIDTH - HUD_MARGIN,
            GOD_BADGE_Y,
            text="★ 上帝模式 · 按 G 关闭",
            anchor="ne",
            font=self.font_small,
            fill=COLOR_GOD,
            state="hidden",
        )
        self.wind_arrow = canvas.create_line(
            0, 0, 0, 0, arrow=tk.LAST, fill=COLOR_TEXT, width=WIND_ARROW_WIDTH
        )
        self.wind_text = canvas.create_text(
            WINDOW_WIDTH // 2,
            WIND_TEXT_Y,
            text="风（吹向）：东",
            anchor="n",
            font=self.font_small,
            fill=COLOR_TEXT,
        )
        self.hint = canvas.create_text(
            WINDOW_WIDTH // 2,
            WINDOW_HEIGHT - HINT_Y,
            text="WASD / 方向键移动 · 顶住风 · G 上帝模式 · M 静音 · R 重新开始",
            anchor="s",
            font=self.font_small,
            fill=COLOR_DIM,
        )
        self.dev_hud = canvas.create_text(
            DEV_HUD_MARGIN,
            WINDOW_HEIGHT - DEV_HUD_MARGIN,
            text="",
            anchor="sw",
            justify="left",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )

    # ------------------------------------------------------------ 结束遮罩
    def _build_overlay(self):
        canvas = self.canvas
        # 结束遮罩：stipple 模拟半透明（Canvas 图形没有真正的 alpha）
        self.overlay_items = []
        self.overlay_rect = canvas.create_rectangle(
            0,
            0,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
            fill=COLOR_OVERLAY,
            outline="",
            stipple=OVERLAY_STIPPLE,
            state="hidden",
        )
        self.overlay_items.append(self.overlay_rect)
        self.overlay_title = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_TITLE_Y,
            text="游戏结束",
            anchor="center",
            font=self.font_big,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_reason = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_REASON_Y,
            text="",
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_score = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_SCORE_Y,
            text="",
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            state="hidden",
        )
        self.overlay_hint = canvas.create_text(
            WINDOW_WIDTH // 2,
            OVERLAY_HINT_Y,
            text="按 R 也可重新开始",
            anchor="center",
            font=self.font_small,
            fill=COLOR_DIM,
            state="hidden",
        )
        self.overlay_items.extend(
            [self.overlay_title, self.overlay_reason, self.overlay_score, self.overlay_hint]
        )

        # 结束界面两个按钮：行为由应用层注入（HUD 只负责界面与标签绑定）。
        # 竖排：重新开始在上，退出游戏在下；标签绑定只在该层可见时生效。
        self._overlay_button_rects = {}
        self._overlay_callbacks = {}
        self.overlay_buttons = []
        center_x = WINDOW_WIDTH // 2
        for index, (tag, label, action) in enumerate(
            (
                ("overlay_btn_restart", "重新开始", "restart"),
                ("overlay_btn_quit", "退出游戏", "quit"),
            )
        ):
            y = OVERLAY_BUTTON_Y_START + index * (
                OVERLAY_BUTTON_HEIGHT + OVERLAY_BUTTON_Y_GAP
            )
            self.overlay_buttons.extend(
                self._create_overlay_button(tag, center_x, y, label, action)
            )
        self.overlay_items.extend(self.overlay_buttons)

    # ------------------------------------------------------------ 刷新
    def update(self, model, fps, input_speed, dev_visible, item_count, force=False):
        now = time.perf_counter()
        if force or (now - self._last_update) >= HUD_REFRESH_INTERVAL:
            self._last_update = now
            self._sync_timer(model.elapsed)
            self._sync_hearts(model.player.hearts)
            self._sync_wind(model.wind, model.wind_speed)
            self._sync_god(model.god_mode)
            self._sync_dev_hud(model, fps, input_speed, dev_visible, item_count)
        self._sync_overlay(model)

    def set_pause_hover(self, hovering):
        """HUD 暂停按钮悬停反馈；只换图，不接管光标（光标由暂停菜单管理）。"""
        if hovering == self._pause_hover:
            return
        self._pause_hover = hovering
        if self._crayon:
            self.canvas.itemconfigure(
                self.pause_bg,
                image=self.pause_hover_photo if hovering else self.pause_photo,
            )
            return
        self.canvas.itemconfigure(
            self.pause_bg, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON
        )

    # ------------------------------------------------------------ 计时
    def _sync_timer(self, elapsed):
        text = "%.1f" % max(0.0, elapsed)
        if text == self._timer_key:
            return
        self._timer_key = text
        if not self._crayon:
            self.canvas.itemconfigure(self.timer, text="存活 %.1f s" % elapsed)
            return
        self._layout_timer(text)

    def _layout_timer(self, text):
        """右对齐排布：标签 + 逐位数字 + 单位；数字格子等宽，秒数跳动时不抖。"""
        label_w, _label_h = self._ui_size("timer_label")
        unit_w, _unit_h = self._ui_size("digit_s")
        glyphs = []
        for char in text:
            name = "digit_dot" if char == "." else "digit_%d" % int(char)
            glyphs.append((name, self._ui_size(name)[0]))

        total = label_w + HUD_CRAYON_TIMER_GAP_LABEL
        total += sum(width for _name, width in glyphs)
        total += HUD_CRAYON_TIMER_GAP_UNIT + unit_w

        x = WINDOW_WIDTH - HUD_MARGIN - total
        y = HUD_CRAYON_TIMER_Y
        self.canvas.coords(self.timer_label, x + label_w / 2.0, y)
        x += label_w + HUD_CRAYON_TIMER_GAP_LABEL
        for index, item in enumerate(self.timer_slots):
            if index < len(glyphs):
                name, width = glyphs[index]
                self.canvas.itemconfigure(
                    item, image=self._ui(name), state="normal")
                self.canvas.coords(item, x + width / 2.0, y)
                x += width
            else:
                self.canvas.itemconfigure(item, state="hidden")
        x += HUD_CRAYON_TIMER_GAP_UNIT
        self.canvas.coords(self.timer_unit, x + unit_w / 2.0, y)

    # ------------------------------------------------------------ 生命
    def _sync_hearts(self, hearts):
        if hearts == self._hearts:
            return
        self._hearts = hearts
        for index, item in enumerate(self.heart_items):
            full = index < hearts
            if self._crayon:
                self.canvas.itemconfigure(
                    item, image=self._ui("heart_full" if full else "heart_empty"))
                continue
            if self._heart_sprites and self.assets is not None:
                photo = self.assets.heart_photo(full, self.canvas)
                if photo is not None:
                    self.canvas.itemconfigure(item, image=photo)
                    continue
            color = COLOR_HEART_FULL if full else COLOR_HEART_EMPTY
            self.canvas.itemconfigure(item, fill=color)
        if not self._crayon:
            self.canvas.itemconfigure(
                self.heart_count, text="%d/%d" % (hearts, PLAYER_MAX_HEARTS)
            )

    # ------------------------------------------------------------ 风向
    def _sync_wind(self, wind, speed):
        if wind.index == self._wind_index and speed == self._wind_speed:
            return
        self._wind_index = wind.index
        self._wind_speed = speed
        if self._crayon:
            code = WIND_UI_CODES[wind.index % len(WIND_UI_CODES)]
            self.canvas.itemconfigure(
                self.compass_arrow, image=self._ui("compass_arrow_%s" % code))
            self.canvas.itemconfigure(
                self.wind_name, image=self._ui("wind_%s" % code))
            return
        center_x = WINDOW_WIDTH // 2
        center_y = WIND_ARROW_Y
        half = WIND_ARROW_LEN * 0.5
        self.canvas.coords(
            self.wind_arrow,
            center_x - wind.ux * half,
            center_y - wind.uy * half,
            center_x + wind.ux * half,
            center_y + wind.uy * half,
        )
        self.canvas.itemconfigure(
            self.wind_text,
            text="风（吹向）：%s  风速 %.0f" % (wind.name(), speed),
        )

    # ------------------------------------------------------------ 上帝模式
    def _sync_god(self, active):
        if active == self._god_active:
            return
        self._god_active = active
        self.canvas.itemconfigure(self.god_badge, state="normal" if active else "hidden")

    def _sync_dev_hud(self, model, fps, input_speed, dev_visible, item_count):
        state = "normal" if dev_visible else "hidden"
        self.canvas.itemconfigure(self.dev_hud, state=state)
        if not dev_visible:
            return
        history = ", ".join("%.1f" % value for value in model.wind_history) or "-"
        text = "\n".join(
            (
                "FPS %.0f  输入 %.0f px/s  items %d  障碍 %d"
                % (fps, input_speed, item_count, len(model.obstacles)),
                "生成间隔 %.2fs  风 %s (%.2f, %.2f)  风速 %.0f（第 %d 档）  剩余 %.1fs"
                % (
                    spawn_interval(model.elapsed),
                    model.wind.name(),
                    model.wind.ux,
                    model.wind.uy,
                    model.wind_speed,
                    model.wind_speed_tier,
                    model.wind.remaining,
                ),
                "最近时长 [%s]  换风 %d" % (history, model.wind.change_count),
            )
        )
        self.canvas.itemconfigure(self.dev_hud, text=text)

    # ------------------------------------------------------------ 结束遮罩
    def set_game_over_callbacks(self, on_restart, on_quit):
        """注入结束界面按钮行为；回调由应用层决定重开/退出。"""
        self._overlay_callbacks = {"restart": on_restart, "quit": on_quit}

    def _create_overlay_button(self, tag, center_x, center_y, label, action):
        half_width = OVERLAY_BUTTON_WIDTH // 2
        half_height = OVERLAY_BUTTON_HEIGHT // 2
        rect = self.canvas.create_rectangle(
            center_x - half_width,
            center_y - half_height,
            center_x + half_width,
            center_y + half_height,
            fill=COLOR_BUTTON,
            outline=COLOR_BUTTON_BORDER,
            width=OUTLINE_WIDTH,
            tags=(tag,),
            state="hidden",
        )
        text = self.canvas.create_text(
            center_x,
            center_y,
            text=label,
            anchor="center",
            font=self.font_normal,
            fill=COLOR_TEXT,
            tags=(tag,),
            state="hidden",
        )
        self._overlay_button_rects[tag] = rect
        self.canvas.tag_bind(
            tag, "<Button-1>", lambda event, a=action: self._activate_overlay_button(a)
        )
        self.canvas.tag_bind(
            tag, "<Enter>", lambda event, t=tag: self._set_overlay_hover(t, True)
        )
        self.canvas.tag_bind(
            tag, "<Leave>", lambda event, t=tag: self._set_overlay_hover(t, False)
        )
        return [rect, text]

    def _activate_overlay_button(self, action):
        if not self._overlay_visible:
            return
        callback = self._overlay_callbacks.get(action)
        if callback is not None:
            callback()

    def _set_overlay_hover(self, tag, hovering):
        rect = self._overlay_button_rects.get(tag)
        if rect is None:
            return
        self.canvas.itemconfigure(
            rect, fill=COLOR_BUTTON_HOVER if hovering else COLOR_BUTTON
        )
        try:
            self.canvas.configure(cursor="hand2" if hovering else "")
        except Exception:
            pass

    def _hide_overlay(self):
        for item in self.overlay_items:
            self.canvas.itemconfigure(item, state="hidden")
        for rect in self._overlay_button_rects.values():
            self.canvas.itemconfigure(rect, fill=COLOR_BUTTON)
        try:
            self.canvas.configure(cursor="")
        except Exception:
            pass

    def _sync_overlay(self, model):
        visible = model.state == STATE_GAME_OVER
        if visible != self._overlay_visible:
            self._overlay_visible = visible
            if visible:
                for item in self.overlay_items:
                    self.canvas.itemconfigure(item, state="normal")
            else:
                self._hide_overlay()
        if not visible:
            return
        reason_text = REASON_TEXT.get(model.reason, "")
        score_text = "本局得分：%.1f 秒" % model.elapsed
        if reason_text != self._overlay_reason_text:
            self._overlay_reason_text = reason_text
            self.canvas.itemconfigure(self.overlay_reason, text=reason_text)
        if score_text != self._overlay_score_text:
            self._overlay_score_text = score_text
            self.canvas.itemconfigure(self.overlay_score, text=score_text)

    def reset_view(self):
        self._last_update = 0.0
        self._hearts = None
        self._wind_index = None
        self._wind_speed = None
        self._god_active = None
        self._overlay_visible = False
        self._overlay_reason_text = None
        self._overlay_score_text = None
        self._timer_key = None
        self._hide_overlay()
        self.canvas.itemconfigure(self.dev_hud, state="hidden")
        self.canvas.itemconfigure(self.god_badge, state="hidden")
        if self._crayon:
            for item in self.timer_slots:
                self.canvas.itemconfigure(item, state="hidden")


