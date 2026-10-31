# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Small, dependency-free Tk widgets shared by the editor and floating wave."""
import math
import time
import tkinter as tk
from windows_integration import NonActivatingWindow


def mix_color(first, second, amount):
    """Blend two hex colors without loading an image/graphics dependency."""
    a, b = first.lstrip("#"), second.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i+2], 16)*(1-amount)+int(b[i:i+2], 16)*amount):02x}"
                         for i in (0, 2, 4))

PALETTES = {
    "dark": dict(bg="#10151d", card="#18202b", editor="#141c26", line="#293442",
                 text="#f0f4f8", muted="#9aabbd", accent="#79e1c5", ink="#102c26",
                 soft="#223e3b", danger="#ff9098", button="#293849", hover="#374e64",
                 border="#526b82", disabled="#202a36"),
    "light": dict(bg="#e8edf1", card="#f6f8fa", editor="#ffffff", line="#ccd7df",
                  text="#1a2935", muted="#5c7080", accent="#176f60", ink="#ffffff",
                  soft="#dcefe9", danger="#b82e40", button="#e1e9f0", hover="#cddfe9",
                  border="#8399ab", disabled="#e7ecf0"),
}


class ActionButton(tk.Button):
    """Visible button surfaces, hover/pressed states and keyboard focus rings."""
    def __init__(self, parent, text, command, colors, primary=False):
        self.colors, self.primary = colors, primary
        self.hovered = False
        super().__init__(parent, text=text, command=command, relief="flat", bd=0,
                         highlightthickness=1, highlightbackground=colors["border"],
                         highlightcolor=colors["accent"], takefocus=True,
                         activebackground=colors["soft"], activeforeground=colors["text"],
                         disabledforeground=colors["muted"], padx=20 if primary else 13,
                         pady=12 if primary else 8, cursor="hand2",
                         font=("Segoe UI", 11 if primary else 10, "bold"))
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<Return>", lambda e: self.invoke())
        self._paint()

    def _hover(self, value):
        self.hovered = value
        self._paint()

    def _paint(self):
        c = self.colors
        disabled = str(self.cget("state")) == "disabled"
        background = c["disabled"] if disabled else c["accent"] if self.primary else c["hover"] if self.hovered else c["button"]
        super().configure(bg=background, fg=c["ink"] if self.primary and not disabled else c["text"],
                          cursor="arrow" if disabled else "hand2",
                          highlightbackground=c["line"] if disabled else c["accent"] if self.primary or self.hovered else c["border"])

    def configure(self, cnf=None, **kwargs):
        result = super().configure(cnf, **kwargs)
        if "state" in kwargs or (isinstance(cnf, dict) and "state" in cnf):
            self._paint()
        return result


class Tooltip:
    """Small delayed hint for icon-only controls; never takes keyboard focus."""
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.job = self.window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def set_text(self, text):
        self.text = text
        if self.window:
            self.window.winfo_children()[0].configure(text=text)

    def _schedule(self, event=None):
        self.hide()
        self.job = self.widget.after(450, self.show)

    def show(self):
        self.job = None
        if not self.widget.winfo_exists() or str(self.widget.cget("state")) == "disabled":
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        label = tk.Label(self.window, text=self.text, bg="#0b1220", fg="#f4fbf9",
                         font=("Segoe UI", 9), padx=9, pady=5, bd=1,
                         relief="solid", highlightthickness=0)
        label.pack()
        self.window.update_idletasks()
        x = self.widget.winfo_rootx() + (self.widget.winfo_width()-self.window.winfo_width())//2
        y = self.widget.winfo_rooty() - self.window.winfo_height() - 7
        self.window.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.window.deiconify()

    def hide(self, event=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.window:
            self.window.destroy()
            self.window = None


class IconButton(ActionButton):
    """Compact familiar-symbol button with an accessible hover explanation."""
    def __init__(self, parent, icon, command, colors, tooltip, danger=False):
        super().__init__(parent, icon, command, colors, primary=False)
        self.danger = danger
        self.configure(font=("Segoe UI Emoji", 13), width=3, padx=6, pady=8)
        self.tooltip = Tooltip(self, tooltip)

    def set_icon(self, icon, tooltip):
        self.configure(text=icon)
        self.tooltip.set_text(tooltip)

class Wave(tk.Canvas):
    def __init__(self, parent, background, color, width=160, height=44, outlined=False):
        super().__init__(parent, width=width, height=height, bg=background,
                         highlightthickness=0, bd=0)
        self.color = color
        self.phase = 0
        self.level = 0
        self.outlined = outlined
        self.hovered = self.pressed = False
        self.idle_size = None
        self.last_state = (0, False, False, 0)
        self.mode = None
        count = 19 if width >= 100 else 9
        self.shadows = [self.create_line(0, 0, 0, 0, fill="#204b48", width=5,
                                        capstyle=tk.ROUND, tags="wave") for _ in range(count)] if outlined else []
        self.bars = [self.create_line(0, 0, 0, 0, fill=color, width=3,
                                      capstyle=tk.ROUND, tags="wave") for _ in range(count)]
        self.track = self.create_line(0, 0, 0, 0, fill="#31564f", width=9, capstyle=tk.ROUND, state="hidden")
        self.fill = self.create_line(0, 0, 0, 0, fill=color, width=5, capstyle=tk.ROUND, state="hidden")
        self.percent = self.create_text(0, 0, text="", fill="#c0f8e9", font=("Segoe UI", 9, "bold"), state="hidden")
        self.set_interaction()
        self.bind("<Configure>", lambda e: self.draw(*self.last_state))

    def set_interaction(self, hovered=False, pressed=False):
        self.hovered, self.pressed = hovered, pressed
        for i, bar in enumerate(self.bars):
            center = math.sin(math.pi*i/(len(self.bars)-1))
            color = mix_color(self.color, "#ffffff", .25*center + (.2 if hovered else 0))
            self.itemconfigure(bar, fill=mix_color(color, "#164e44", .3) if pressed else color)
        self.itemconfigure(self.fill, fill=mix_color(self.color, "#ffffff", .3 if hovered else 0))

    def draw(self, level, active, busy=False, progress=0):
        self.last_state = (level, active, busy, progress)
        w, h = max(self.winfo_width(), 40), max(self.winfo_height(), 20)
        mode = "progress" if busy and not active else "wave"
        if mode != self.mode:
            self.itemconfigure("wave", state="normal" if mode == "wave" else "hidden")
            for item in (self.track, self.fill):
                self.itemconfigure(item, state="normal" if mode == "progress" else "hidden")
            self.mode = mode
            self.idle_size = None
            self.itemconfigure(self.percent, state="normal" if mode == "progress" and self.outlined else "hidden")
        if mode == "progress":
            # Fill always grows from the left, including for right-to-left text.
            y = h/2 + (9 if self.outlined else 0)
            self.coords(self.track, 7, y, w-7, y)
            self.coords(self.fill, 7, y, 7+(w-14)*max(0, min(1, progress)), y)
            self.coords(self.percent, w/2, h/2-11)
            self.itemconfigure(self.percent, text="100%" if progress >= 1 else f"≈ {int(progress*100)}%")
            return
        if not active and not busy and self.idle_size == (w, h):
            return
        self.idle_size = (w, h) if not active and not busy else None
        self.level = max(level, self.level * 0.72) if active else 0
        self.phase += 0.25
        for i, bar in enumerate(self.bars):
            # One symmetric bell: the center is always tallest, the two sides
            # taper smoothly. Audio changes its height, never its overall shape.
            envelope = math.sin(math.pi*i/(len(self.bars)-1)) ** 1.4
            idle = min(14, h*.22)
            energy = min(1, self.level*1.9) * (.95+.05*math.sin(self.phase))
            size = idle + (h-12-idle)*energy*envelope if active else idle
            x = (w - 8) * (i + 0.5) / len(self.bars) + 4
            self.coords(bar, x, (h-size)/2, x, (h+size)/2)
            if self.shadows:
                self.coords(self.shadows[i], x, (h-size)/2, x, (h+size)/2)


class FloatingWave(tk.Toplevel):
    """A compact transparent wave. Position is saved only after a completed drag."""
    WIDTH, HEIGHT = 156, 80
    TRANSPARENT = "#010203"
    def __init__(self, app, start, stop, show, quit_app, position=None, position_changed=None):
        super().__init__(app)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=self.TRANSPARENT)
        self.attributes("-transparentcolor", self.TRANSPARENT)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{self.winfo_screenwidth()-197}+{self.winfo_screenheight()-155}")
        self.body = tk.Frame(self, bg=self.TRANSPARENT)
        self.body.pack(fill="both", expand=True)
        self.dot = tk.Canvas(self.body, width=24, height=40, bg=self.TRANSPARENT, bd=0,
                             highlightthickness=0, cursor="hand2")
        self.dot_mark = self.dot.create_oval(6, 13, 19, 26, fill="#79e1c5", outline="#183c39", width=2)
        self.dot.pack(side="left", padx=(2, 4))
        self.wave = Wave(self.body, self.TRANSPARENT, "#79e1c5", width=120, height=68, outlined=True)
        self.wave.configure(cursor="hand2")
        self.wave.pack(side="left", pady=6)
        self.start, self.stop = start, stop
        self.position_changed = position_changed
        self.mode_color = "#79e1c5"
        self.hovered = False
        self.dragged = False
        self.last_action_at = 0.0
        self.native = NonActivatingWindow(self)
        self.saved_position = position
        self.bind("<Map>", self._mapped)
        self.menu = tk.Menu(self, tearoff=False, bg="#182832", fg="#eef9f6",
                            activebackground="#31584f", activeforeground="#ffffff",
                            font=("Segoe UI", 10), bd=1)
        self.menu.add_command(label="Open Voice Typing", command=show)
        self.menu.add_command(label="Start / stop dictation", command=lambda: app.toggle_recording(external=True))
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=quit_app)
        for widget in (self, self.body, self.wave, self.dot):
            # Bind only once per event path: child events also reach the Toplevel.
            if widget is self:
                continue
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._release)
            widget.bind("<Double-Button-1>", self._double)
            widget.bind("<Button-3>", self._menu)
            widget.bind("<Enter>", lambda e: self._hover(True))
            widget.bind("<Leave>", lambda e: self._hover(False))

    def _mapped(self, event):
        if event.widget is self and self.saved_position is not None:
            x, y = self.saved_position
            self.native.move_to(x, y, self.WIDTH, self.HEIGHT)

    def _hover(self, hovering):
        self.hovered = hovering
        self.wave.set_interaction(hovering)
        self.dot.itemconfigure(self.dot_mark, fill=mix_color(self.mode_color, "#ffffff", .35) if hovering else self.mode_color)

    def _press(self, event):
        self.origin = (event.x_root, event.y_root, self.winfo_x(), self.winfo_y())
        self.dragged = False
        self.wave.set_interaction(True, True)

    def _drag(self, event):
        x, y, left, top = self.origin
        dx, dy = event.x_root-x, event.y_root-y
        if abs(dx)+abs(dy) > 5:
            self.dragged = True
            self.native.move_to(left+dx, top+dy, self.WIDTH, self.HEIGHT)

    def _release(self, event):
        self.wave.set_interaction(self.hovered)
        if self.dragged:
            self.saved_position = self.native.position()
            if self.position_changed and self.saved_position:
                self.position_changed(self.saved_position)
        else:
            # Ignore the second release of an accidental double-click. Without
            # this guard a fast stop could immediately start a new recording.
            now = time.monotonic()
            if now - self.last_action_at < .35:
                return
            self.last_action_at = now
            self.start()

    def _double(self, event):
        self.dragged = True
        self.stop()
        return "break"

    def _menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def update_wave(self, level, active, busy=False, progress=0, muted=False):
        self.mode_color = "#8d9aaa" if muted else "#ff9098" if active else "#f4c875" if busy else "#79e1c5"
        self.dot.itemconfigure(self.dot_mark, fill=mix_color(self.mode_color, "#ffffff", .35) if self.hovered else self.mode_color)
        self.wave.draw(level, active and not muted, busy, progress)

    def destroy(self):
        self.native.close()
        super().destroy()
