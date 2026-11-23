"""Two clipped caption lines synchronized to one playback timeline."""
import time
import tkinter as tk

from PIL import ImageTk


class ScrollingCaptions(tk.Canvas):
    HEIGHT = 82

    def __init__(self, parent, width, fonts, colors):
        super().__init__(parent, width=width, height=self.HEIGHT, highlightthickness=0, bd=0)
        self.viewport = width
        self.backdrop = self.create_image(0, 0, anchor="nw")
        self.rows = [self.create_text(2, y, anchor="nw", font=font, fill=color)
                     for y, font, color in zip((3, 45), fonts, colors)]
        self.timer = None
        self.overflow = [0, 0]
        self.progress = 0.0
        self.bind("<MouseWheel>", self._wheel)

    def set_backdrop(self, skin, x, y):
        region = skin.crop((x, y, x + self.viewport, y + self.HEIGHT))
        self.skin = ImageTk.PhotoImage(region, master=self)
        self.itemconfigure(self.backdrop, image=self.skin)

    def show(self, spoken, translated, duration=None):
        self.stop()
        for i, (item, text) in enumerate(zip(self.rows, (spoken, translated))):
            self.itemconfigure(item, text=" ".join(text.split()))
            bounds = self.bbox(item)
            self.overflow[i] = max(0, bounds[2] - bounds[0] + 4 - self.viewport) if bounds else 0
        self._position(0.0)
        if duration and any(self.overflow):
            self.duration = max(.1, duration)
            self.started = time.monotonic()
            self._tick()

    def _position(self, progress):
        self.progress = max(0.0, min(1.0, progress))
        for item, distance in zip(self.rows, self.overflow):
            self.coords(item, 2 - round(distance * self.progress), self.coords(item)[1])

    def _tick(self):
        self.timer = None
        elapsed = time.monotonic() - self.started
        hold = min(.55, self.duration * .12)
        self._position((elapsed - hold) / (self.duration - 2 * hold))
        if self.progress < 1:
            self.timer = self.after(25, self._tick)

    def _wheel(self, event):
        if any(self.overflow):
            self.stop()
            self._position(self.progress - event.delta / 120 * 75 / max(self.overflow))
        return "break"

    def stop(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
