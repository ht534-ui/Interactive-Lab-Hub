import time
import math
import random
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.st7789 as st7789


# =========================================================
# DEMO SETTINGS
# =========================================================

DEMO_MODE = True

# Demo mode:
# 60 real seconds = 24 simulated hours
DEMO_DAY_DURATION = 60.0

demo_start_time = time.time()


# =========================================================
# DISPLAY SETUP
# =========================================================

cs_pin = digitalio.DigitalInOut(board.D5)
dc_pin = digitalio.DigitalInOut(board.D25)

spi = board.SPI()

disp = st7789.ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=None,
    baudrate=64000000,
    width=135,
    height=240,
    x_offset=53,
    y_offset=40,
)

# Landscape
height = disp.width
width = disp.height
rotation = 90

image = Image.new("RGB", (width, height))
draw = ImageDraw.Draw(image)

font = ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    14
)


# =========================================================
# BACKLIGHT
# =========================================================

backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True


# =========================================================
# STARS
# =========================================================

stars = []

for _ in range(20):
    stars.append(
        (
            random.randint(5, width - 5),
            random.randint(20, 65)
        )
    )


# =========================================================
# CURRENT TIME
# =========================================================

def get_current_hour():

    if DEMO_MODE:

        elapsed = time.time() - demo_start_time

        progress = (
            elapsed % DEMO_DAY_DURATION
        ) / DEMO_DAY_DURATION

        return progress * 24.0

    else:

        now = time.localtime()

        return (
            now.tm_hour
            + now.tm_min / 60.0
            + now.tm_sec / 3600.0
        )


# =========================================================
# SKY COLOR
# =========================================================

def get_sky_color(hour):

    # Sunrise
    if 5 <= hour < 7:
        return (255, 155, 115)

    # Morning
    elif 7 <= hour < 10:
        return (130, 190, 245)

    # Day
    elif 10 <= hour < 16:
        return (90, 175, 245)

    # Sunset
    elif 16 <= hour < 19:
        return (255, 120, 90)

    # Night
    else:
        return (15, 25, 60)


# =========================================================
# SUN
# =========================================================

def draw_sun(hour, horizon_y):

    sunrise = 6
    sunset = 18

    if hour < sunrise or hour > sunset:
        return

    progress = (
        hour - sunrise
    ) / (
        sunset - sunrise
    )

    sun_x = int(
        progress * width
    )

    sun_y = int(
        horizon_y
        - 45 * math.sin(progress * math.pi)
    )

    radius = 10

    # Glow
    draw.ellipse(
        (
            sun_x - radius - 3,
            sun_y - radius - 3,
            sun_x + radius + 3,
            sun_y + radius + 3
        ),
        fill=(255, 190, 90)
    )

    # Sun
    draw.ellipse(
        (
            sun_x - radius,
            sun_y - radius,
            sun_x + radius,
            sun_y + radius
        ),
        fill=(255, 225, 80)
    )


# =========================================================
# MOON
# =========================================================

def draw_moon(hour, horizon_y, sky_color):

    adjusted_hour = hour

    if hour < 6:
        adjusted_hour += 24

    if adjusted_hour < 18 or adjusted_hour > 30:
        return

    progress = (
        adjusted_hour - 18
    ) / 12.0

    moon_x = int(
        progress * width
    )

    moon_y = int(
        horizon_y
        - 40 * math.sin(progress * math.pi)
    )

    radius = 8

    draw.ellipse(
        (
            moon_x - radius,
            moon_y - radius,
            moon_x + radius,
            moon_y + radius
        ),
        fill=(245, 240, 205)
    )

    # Crescent cutout
    draw.ellipse(
        (
            moon_x,
            moon_y - radius - 2,
            moon_x + radius + 6,
            moon_y + radius
        ),
        fill=sky_color
    )


# =========================================================
# STARS
# =========================================================

def draw_stars():

    for x, y in stars:

        draw.point(
            (x, y),
            fill=(245, 245, 220)
        )


# =========================================================
# OCEAN
# =========================================================

def draw_ocean(horizon_y, beach_y):

    draw.rectangle(
        (
            0,
            horizon_y,
            width,
            beach_y
        ),
        fill=(25, 110, 170)
    )

    # Slowly moving waves
    wave_offset = int(
        time.time() * 5
    ) % 24

    for y in range(
        horizon_y + 7,
        beach_y,
        9
    ):

        for x in range(
            -24,
            width + 24,
            24
        ):

            x += wave_offset

            draw.line(
                [
                    (x, y),
                    (x + 6, y - 2),
                    (x + 12, y)
                ],
                fill=(190, 225, 235),
                width=1
            )


# =========================================================
# BEACH
# =========================================================

def draw_beach(beach_y):

    draw.rectangle(
        (
            0,
            beach_y,
            width,
            height
        ),
        fill=(225, 195, 135)
    )

    draw.line(
        (
            0,
            beach_y,
            width,
            beach_y
        ),
        fill=(245, 240, 220),
        width=2
    )


# =========================================================
# TIME TEXT
# =========================================================

def get_time_text(hour):

    if DEMO_MODE:

        display_hour = int(hour)

        display_minute = int(
            (hour - display_hour) * 60
        )

        return (
            f"{display_hour:02d}:"
            f"{display_minute:02d}"
        )

    else:

        return time.strftime("%H:%M")


# =========================================================
# MAIN LOOP
# =========================================================

while True:

    hour = get_current_hour()

    horizon_y = 82
    beach_y = 110

    sky_color = get_sky_color(hour)


    # SKY
    draw.rectangle(
        (
            0,
            0,
            width,
            horizon_y
        ),
        fill=sky_color
    )


    # SUN / MOON
    if 6 <= hour <= 18:

        draw_sun(
            hour,
            horizon_y
        )

    else:

        draw_stars()

        draw_moon(
            hour,
            horizon_y,
            sky_color
        )


    # OCEAN
    draw_ocean(
        horizon_y,
        beach_y
    )


    # BEACH
    draw_beach(
        beach_y
    )


    # TIME
    time_text = get_time_text(hour)

    bbox = draw.textbbox(
        (0, 0),
        time_text,
        font=font
    )

    text_width = bbox[2] - bbox[0]

    draw.text(
        (
            (width - text_width) // 2,
            5
        ),
        time_text,
        font=font,
        fill="white"
    )


    # MODE LABEL
    mode_text = (
        "DEMO"
        if DEMO_MODE
        else "REAL"
    )

    draw.text(
        (
            5,
            5
        ),
        mode_text,
        font=ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            8
        ),
        fill="white"
    )


    # DISPLAY
    disp.image(
        image,
        rotation
    )

    time.sleep(0.1)