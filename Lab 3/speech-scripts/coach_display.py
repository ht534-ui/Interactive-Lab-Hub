"""Coach mood and high-contrast activity labels have separate visual roles."""
import math
import time


STATE_COLORS = {
    "idle": "#a0b4cc", "connecting": "#ffb84a", "listening": "#37d7fa",
    "thinking": "#ffb84a", "speaking": "#ba9cff", "finalizing": "#ffb84a",
    "synthesizing": "#ba9cff", "summary": "#ba9cff", "draining": "#ba9cff",
    "closing": "#ffb84a", "done": "#64dc94", "error": "#ff7676",
}


def render(state, now=None):
    from PIL import Image, ImageDraw, ImageFont
    now = time.monotonic() if now is None else now
    image = Image.new("RGB", (240, 135), "#11131a")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except OSError:
        font = ImageFont.load_default(size=14)
        small = ImageFont.load_default(size=11)
    mood, phase = state.get("mood", "neutral"), state.get("phase", "idle")
    color = {"smile": "#64dc94", "wry": "#f3ca62", "glare": "#ef736c"}.get(mood, "#a0b4cc")
    offset = round(math.sin(now * 5) * 3) if mood == "smile" else 0
    cx, cy = 56, 58 + offset
    draw.ellipse((cx-39, cy-39, cx+39, cy+39), outline=color, width=3)
    for ex in (cx-14, cx+14):
        draw.ellipse((ex-3, cy-8, ex+3, cy-2), fill=color)
    if mood == "smile":
        draw.arc((cx-18, cy-7, cx+18, cy+22), 10, 170, fill=color, width=3)
    elif mood == "glare":
        draw.line((cx-23, cy-19, cx-6, cy-12), fill=color, width=3)
        draw.line((cx+6, cy-12, cx+23, cy-19), fill=color, width=3)
        draw.line((cx-13, cy+15, cx+13, cy+15), fill=color, width=3)
    else:
        draw.line((cx-15, cy+15, cx+15, cy+10), fill=color, width=3)
        if mood == "wry":
            draw.line((cx+7, cy-19, cx+24, cy-24), fill=color, width=3)
    labels = {"idle": "READY", "connecting": "CONNECTING", "listening": "LISTENING", "thinking": "SAVING",
              "speaking": "SPEAKING", "finalizing": "CHECKING", "synthesizing": "RECAP", "summary": "RECAP",
              "draining": "RECAP", "closing": "CLOSING", "done": "SAVED", "error": "CHECK LOG"}
    activity_color = STATE_COLORS.get(phase, "#a0b4cc")
    draw.rounded_rectangle((101, 17, 237, 43), radius=5, fill=activity_color)
    draw.text((106, 22), labels.get(phase, phase.upper()), font=font, fill="#11131a")
    busy = bool(state.get("backend_busy"))
    elapsed = state.get("backend_elapsed", 0) if busy else state.get("phase_elapsed", 0)
    if busy:
        draw.rounded_rectangle((101, 47, 237, 66), radius=4, fill=STATE_COLORS["thinking"])
        draw.text((106, 49), "SAVING  " + str(elapsed) + "s", font=small, fill="#11131a")
        if phase in ("listening", "speaking", "thinking"):
            draw.text((106, 94), "You can keep talking", font=small, fill="#dbe4ef")
    elif phase in ("connecting", "thinking", "finalizing", "synthesizing", "closing"):
        draw.text((106, 49), "Please wait  " + str(elapsed) + "s", font=small, fill=activity_color)
    # This cue is an activity indicator, never a health/score color.
    for i in range(5):
        height = 5 + int(5 * abs(math.sin(now * 4 + i))) if phase in ("speaking", "summary", "draining") else (7 if (busy or phase in ("connecting", "thinking", "finalizing", "synthesizing", "closing")) and i == int(now * 3) % 5 else 3)
        draw.rectangle((109+i*13, 79-height, 114+i*13, 79+height), fill=activity_color)
    if phase in ("summary", "draining", "closing", "done", "error"):
        score = state.get("score")
        draw.text((106, 85), "Game: " + (str(score) if score is not None else "--"), font=font, fill="white")
    draw.text((10, 115), "A: start" if phase in ("idle", "done", "error") else "A: recap & finish", font=font, fill="#a0a8b7")
    return image


class Display:
    def __init__(self):
        import board
        import digitalio
        from adafruit_rgb_display import st7789
        self.pins = [digitalio.DigitalInOut(board.D5), digitalio.DigitalInOut(board.D25), digitalio.DigitalInOut(board.D22)]
        self.pins[2].switch_to_output(value=True)
        self.spi = board.SPI()
        self.device = st7789.ST7789(self.spi, cs=self.pins[0], dc=self.pins[1], rst=None,
                                  baudrate=64000000, width=135, height=240, x_offset=53, y_offset=40)

    def show(self, state):
        self.device.image(render(state), rotation=90)

    def close(self):
        for pin in self.pins:
            pin.deinit()
        self.spi.deinit()
