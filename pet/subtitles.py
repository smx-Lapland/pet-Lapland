"""Two lines per language, synchronized to one playback timeline."""
import sys
import time
import tkinter as tk

from PIL import ImageTk


class ScrollingCaptions(tk.Canvas):
    VISIBLE_LINES = 2

    def __init__(self, parent, width, fonts, colors):
        self.line_heights = [font.metrics("linespace") for font in fonts]
        self.track_heights = [self.VISIBLE_LINES * line for line in self.line_heights]
        self.offsets = (0, self.track_heights[0] + 6)
        self.height = self.offsets[1] + self.track_heights[1]
        super().__init__(parent, width=width, height=self.height, highlightthickness=0, bd=0)
        self.viewport = width
        self.backdrop = self.create_image(0, 0, anchor="nw")
        self.tracks = []
        self.rows = []
        for offset, height, font, color in zip(self.offsets, self.track_heights, fonts, colors):
            track = tk.Canvas(self, width=width, height=height, bd=0, highlightthickness=0)
            track.create_image(0, 0, anchor="nw", tags="background")
            self.rows.append(track.create_text(2, 0, anchor="nw", font=font, fill=color, width=width - 4))
            self.create_window(0, offset, anchor="nw", window=track)
            self.tracks.append(track)
            track.bind("<MouseWheel>", self._wheel)
        self.timer = None
        self.duration = None
        self.manual = False
        self.overflow = [0, 0]
        self.progress = 0.0
        self.bind("<MouseWheel>", self._wheel)

    def set_backdrop(self, skin, x, y):
        region = skin.crop((x, y, x + self.viewport, y + self.height))
        self.skin = ImageTk.PhotoImage(region, master=self)
        self.itemconfigure(self.backdrop, image=self.skin)
        self.backgrounds = []
        for track, offset, height in zip(self.tracks, self.offsets, self.track_heights):
            image = ImageTk.PhotoImage(region.crop((0, offset, self.viewport, offset + height)), master=track)
            self.backgrounds.append(image)
            track.itemconfigure("background", image=image)

    def resize_width(self, width):
        if width == self.viewport:
            return
        self.viewport = width
        self.configure(width=width)
        for i, (track, item) in enumerate(zip(self.tracks, self.rows)):
            track.configure(width=width)
            track.itemconfigure(item, width=width - 4)
            self._text(i, track.itemcget(item, "text"))
        self._position(self.progress)
        if self.duration and not self.manual and self.timer is None and any(self.overflow):
            self._tick()

    def _text(self, i, text):
        track, item = self.tracks[i], self.rows[i]
        track.itemconfigure(item, text=" ".join(text.split()))
        box = track.bbox(item)
        self.overflow[i] = max(0, box[3] - box[1] - self.track_heights[i]) if box else 0

    def show(self, spoken, translated, duration=None):
        self.stop()
        self._text(0, spoken)
        self._text(1, translated)
        self._position(0)
        self.duration = max(.1, duration) if duration else None
        self.started = time.monotonic()
        self.manual = False
        if self.duration and any(self.overflow):
            self._tick()

    def translate(self, translated):
        self._text(1, translated)
        self._position(self.progress)
        if self.duration and self.timer is None and not self.manual and any(self.overflow):
            self._tick()

    def _position(self, progress):
        self.progress = max(0., min(1., progress))
        for track, item, distance, height in zip(self.tracks, self.rows, self.overflow, self.line_heights):
            # Keep each line legible, then slide continuously into the next line.
            position = self.progress * distance / height
            line = int(position)
            blend = min(1., max(0., (position - line - .22) / .56))
            track.coords(item, 2, -min(distance, round((line + blend) * height)))

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
            steps = event.delta if sys.platform == "darwin" else event.delta / 120
            self._position(self.progress - steps * max(self.line_heights) / max(self.overflow))
        return "break"

    def stop(self):
        self.manual = True
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
