import time
import math
import random
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.st7789 as st7789


# =========================================================
# DISPLAY SETUP
# =========================================================

cs_pin = digitalio.DigitalInOut(board.D5)
dc_pin = digitalio.DigitalInOut(board.D25)

BAUDRATE = 64000000

spi = board.SPI()

disp = st7789.ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=None,
    baudrate=BAUDRATE,
    width=135,
    height=240,
    x_offset=53,
    y_offset=40,
)

height = disp.width
width = disp.height
rotation = 90

image = Image.new("RGB", (width, height))
draw = ImageDraw.Draw(image)

font = ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    10
)

small_font = ImageFont.truetype(
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    8
)


# =========================================================
# BACKLIGHT
# =========================================================

backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True


# =========================================================
# BUTTONS
# =========================================================

button_a = digitalio.DigitalInOut(board.D23)
button_a.direction = digitalio.Direction.INPUT
button_a.pull = digitalio.Pull.UP

button_b = digitalio.DigitalInOut(board.D24)
button_b.direction = digitalio.Direction.INPUT
button_b.pull = digitalio.Pull.UP


# =========================================================
# MODE / FOCUS
# =========================================================

demo_mode = False

# 60 real seconds = 24 simulated hours
DEMO_DAY_DURATION = 60.0

demo_start_time = time.time()

focus_active = False

total_focus_seconds = 0.0
demo_focus_minutes = 0.0

last_loop_time = time.time()

last_button_a = True
last_button_b = True

combo_active = False


# =========================================================
# STARS
# =========================================================

stars = []

for _ in range(18):
    stars.append(
        {
            "x": random.randint(5, width - 5),
            "y": random.randint(28, 65),
            "phase": random.uniform(0, math.pi * 2)
        }
    )


# =========================================================
# TIME
# =========================================================

def get_current_hour():

    if demo_mode:

        elapsed = time.time() - demo_start_time

        progress = (
            elapsed % DEMO_DAY_DURATION
        ) / DEMO_DAY_DURATION

        return progress * 24.0

    now = time.localtime()

    return (
        now.tm_hour
        + now.tm_min / 60.0
        + now.tm_sec / 3600.0
    )


# =========================================================
# FOCUS / ATTENTION
# =========================================================

def get_focus_minutes():

    if demo_mode:
        return demo_focus_minutes

    return total_focus_seconds / 60.0


def get_attention_level():

    focus_minutes = get_focus_minutes()

    if focus_minutes < 20:
        return "LOW"

    elif focus_minutes < 60:
        return "MEDIUM"

    return "HIGH"


# =========================================================
# SKY
# =========================================================

def get_sky_color(hour, attention):

    # Night
    if hour < 5 or hour >= 19:
        return (15, 25, 60)

    # LOW = cloudy / darker
    if attention == "LOW":

        if 5 <= hour < 8:
            return (115, 115, 125)

        elif 8 <= hour < 16:
            return (105, 115, 125)

        else:
            return (105, 100, 110)

    # MEDIUM = slightly muted
    elif attention == "MEDIUM":

        if 5 <= hour < 8:
            return (220, 145, 115)

        elif 8 <= hour < 16:
            return (125, 175, 210)

        else:
            return (220, 125, 105)

    # HIGH = clear sky
    else:

        if 5 <= hour < 8:
            return (255, 150, 110)

        elif 8 <= hour < 16:
            return (100, 180, 255)

        else:
            return (255, 110, 90)


def get_phase(hour):

    if 5 <= hour < 8:
        return "Sunrise"

    elif 8 <= hour < 12:
        return "Morning"

    elif 12 <= hour < 16:
        return "Afternoon"

    elif 16 <= hour < 19:
        return "Sunset"

    else:
        return "Night"


# =========================================================
# SUN
# =========================================================

def draw_sun(hour, horizon_y):

    if not (6 <= hour <= 18):
        return

    progress = (hour - 6) / 12.0

    sun_x = int(progress * width)

    sun_y = int(
        horizon_y
        - 43 * math.sin(progress * math.pi)
    )

    r = 9

    draw.ellipse(
        (
            sun_x - r,
            sun_y - r,
            sun_x + r,
            sun_y + r
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

    if not (18 <= adjusted_hour <= 30):
        return

    progress = (
        adjusted_hour - 18
    ) / 12.0

    moon_x = int(progress * width)

    moon_y = int(
        horizon_y
        - 38 * math.sin(progress * math.pi)
    )

    r = 8

    draw.ellipse(
        (
            moon_x - r,
            moon_y - r,
            moon_x + r,
            moon_y + r
        ),
        fill=(240, 240, 210)
    )

    # Crescent cutout
    draw.ellipse(
        (
            moon_x,
            moon_y - r - 2,
            moon_x + r + 6,
            moon_y + r
        ),
        fill=sky_color
    )


# =========================================================
# STARS
# =========================================================

def draw_stars():

    t = time.time()

    for star in stars:

        brightness = int(
            180
            + 70
            * (
                math.sin(
                    t * 3 + star["phase"]
                )
                + 1
            )
            / 2
        )

        draw.point(
            (
                star["x"],
                star["y"]
            ),
            fill=(
                brightness,
                brightness,
                brightness
            )
        )


# =========================================================
# CLOUDS
# =========================================================

def draw_cloud(x, y, dark=False):

    if dark:
        color = (75, 80, 90)
    else:
        color = (190, 195, 200)

    draw.ellipse(
        (x, y + 4, x + 18, y + 13),
        fill=color
    )

    draw.ellipse(
        (x + 8, y, x + 25, y + 14),
        fill=color
    )

    draw.ellipse(
        (x + 18, y + 4, x + 36, y + 13),
        fill=color
    )


# =========================================================
# RAIN
# =========================================================

def draw_rain(amount, speed=20):

    t = time.time()

    for i in range(amount):

        x = int(
            (
                i * 23
                + t * speed
            )
            % width
        )

        y = int(
            (
                i * 17
                + t * speed * 1.5
            )
            % 105
        )

        draw.line(
            (
                x,
                y,
                x - 2,
                y + 5
            ),
            fill=(170, 205, 225),
            width=1
        )


# =========================================================
# WEATHER
# =========================================================

def draw_weather(attention):

    # LOW = cloudy + heavier rain
    if attention == "LOW":

        draw_cloud(30, 30, True)
        draw_cloud(90, 42, True)
        draw_cloud(150, 28, True)
        draw_cloud(200, 46, True)

        draw_rain(
            amount=28,
            speed=25
        )

    # MEDIUM = few clouds + light rain
    elif attention == "MEDIUM":

        draw_cloud(45, 42, False)
        draw_cloud(170, 47, False)

        draw_rain(
            amount=9,
            speed=15
        )

    # HIGH = no cloud / no rain


# =========================================================
# OCEAN
# =========================================================

def draw_ocean(horizon_y, beach_y, attention):

    if attention == "LOW":
        ocean_color = (20, 80, 140)
        wave_height = 5
        spacing = 14

    elif attention == "MEDIUM":
        ocean_color = (20, 100, 160)
        wave_height = 3
        spacing = 20

    else:
        ocean_color = (25, 120, 175)
        wave_height = 1
        spacing = 28

    draw.rectangle(
        (
            0,
            horizon_y,
            width,
            beach_y
        ),
        fill=ocean_color
    )

    t = int(time.time() * 8)

    for y in range(
        horizon_y + 7,
        beach_y,
        9
    ):

        for x in range(
            -spacing,
            width + spacing,
            spacing
        ):

            x += t % spacing

            draw.line(
                [
                    (x, y),
                    (x + 5, y - wave_height),
                    (x + 10, y)
                ],
                fill=(190, 225, 235),
                width=1
            )


# =========================================================
# BEACH
# =========================================================

def draw_beach(beach_y):

    sand_color = (225, 195, 135)

    draw.rectangle(
        (
            0,
            beach_y,
            width,
            height
        ),
        fill=sand_color
    )

    draw.line(
        (
            0,
            beach_y,
            width,
            beach_y
        ),
        fill=(245, 245, 225),
        width=2
    )


# =========================================================
# PALM TREE
# =========================================================

def draw_palm_tree(base_x, base_y, sway=0):

    trunk_color = (120, 75, 35)

    top_x = base_x + 7
    top_y = base_y - 34

    draw.line(
        (
            base_x,
            base_y,
            top_x,
            top_y
        ),
        fill=trunk_color,
        width=5
    )

    leaf_color = (35, 130, 55)

    leaves = [
        (-20, -7),
        (-18, 3),
        (-10, -15),
        (0, -17),
        (10, -15),
        (20, -7),
        (18, 4)
    ]

    for dx, dy in leaves:

        dx += sway

        draw.line(
            (
                top_x,
                top_y,
                top_x + dx,
                top_y + dy
            ),
            fill=leaf_color,
            width=3
        )

    # coconuts
    draw.ellipse(
        (
            top_x - 4,
            top_y,
            top_x,
            top_y + 4
        ),
        fill=(90, 55, 25)
    )

    draw.ellipse(
        (
            top_x,
            top_y + 1,
            top_x + 4,
            top_y + 5
        ),
        fill=(90, 55, 25)
    )


# =========================================================
# BOAT
# =========================================================

def draw_boat(x, y, flip=False):

    hull = [
        (x, y),
        (x + 14, y),
        (x + 10, y + 5),
        (x + 3, y + 5)
    ]

    draw.polygon(
        hull,
        fill=(100, 60, 35)
    )

    mast_x = x + 7

    draw.line(
        (
            mast_x,
            y,
            mast_x,
            y - 8
        ),
        fill=(70, 50, 40)
    )

    if not flip:

        sail = [
            (mast_x, y - 8),
            (mast_x, y),
            (mast_x + 6, y - 2)
        ]

    else:

        sail = [
            (mast_x, y - 8),
            (mast_x, y),
            (mast_x - 6, y - 2)
        ]

    draw.polygon(
        sail,
        fill=(230, 230, 210)
    )


# =========================================================
# PERSON
# =========================================================

def draw_person(x, y):

    draw.ellipse(
        (
            x,
            y,
            x + 3,
            y + 3
        ),
        fill=(90, 65, 45)
    )

    draw.line(
        (
            x + 1,
            y + 4,
            x + 1,
            y + 10
        ),
        fill=(50, 50, 50),
        width=2
    )

    draw.line(
        (
            x + 1,
            y + 10,
            x - 2,
            y + 14
        ),
        fill=(50, 50, 50)
    )

    draw.line(
        (
            x + 1,
            y + 10,
            x + 4,
            y + 14
        ),
        fill=(50, 50, 50)
    )


# =========================================================
# BIRD
# =========================================================

def draw_bird(x, y):

    draw.arc(
        (
            x,
            y,
            x + 6,
            y + 4
        ),
        200,
        340,
        fill=(30, 30, 30)
    )

    draw.arc(
        (
            x + 5,
            y,
            x + 11,
            y + 4
        ),
        200,
        340,
        fill=(30, 30, 30)
    )


# =========================================================
# CRAB
# =========================================================

def draw_crab(x, y):

    color = (205, 70, 40)

    draw.ellipse(
        (
            x,
            y,
            x + 7,
            y + 4
        ),
        fill=color
    )

    draw.line(
        (x, y + 2, x - 3, y),
        fill=color
    )

    draw.line(
        (x + 7, y + 2, x + 10, y),
        fill=color
    )

    draw.line(
        (x + 1, y + 4, x - 1, y + 7),
        fill=color
    )

    draw.line(
        (x + 6, y + 4, x + 8, y + 7),
        fill=color
    )


# =========================================================
# TURTLE
# =========================================================

def draw_turtle(x, y, flip=False):

    shell = (55, 120, 70)
    skin = (80, 150, 85)

    draw.ellipse(
        (
            x,
            y,
            x + 12,
            y + 7
        ),
        fill=shell
    )

    if not flip:

        draw.ellipse(
            (
                x + 11,
                y + 2,
                x + 15,
                y + 5
            ),
            fill=skin
        )

    else:

        draw.ellipse(
            (
                x - 3,
                y + 2,
                x + 1,
                y + 5
            ),
            fill=skin
        )

    draw.line(
        (
            x + 2,
            y + 7,
            x,
            y + 10
        ),
        fill=skin
    )

    draw.line(
        (
            x + 9,
            y + 7,
            x + 11,
            y + 10
        ),
        fill=skin
    )


# =========================================================
# MOVEMENT
# =========================================================

def move_left_to_right(speed, offset=0):

    return int(
        (
            time.time() * speed
            + offset
        )
        % (width + 40)
    ) - 20


def move_right_to_left(speed, offset=0):

    return width - (
        int(
            (
                time.time() * speed
                + offset
            )
            % (width + 40)
        )
    ) + 10


# =========================================================
# LOW ATTENTION
# =========================================================

def draw_low_attention():

    # Boats
    draw_boat(
        move_left_to_right(12, 0),
        85
    )

    draw_boat(
        move_left_to_right(15, 90),
        94
    )

    draw_boat(
        move_right_to_left(10, 30),
        89,
        True
    )

    draw_boat(
        move_right_to_left(14, 130),
        97,
        True
    )

    # People
    for speed, offset, y in [
        (10, 0, 111),
        (13, 70, 116),
        (9, 150, 114)
    ]:

        draw_person(
            move_left_to_right(speed, offset),
            y
        )

    for speed, offset, y in [
        (11, 20, 113),
        (14, 100, 118),
        (8, 180, 112)
    ]:

        draw_person(
            move_right_to_left(speed, offset),
            y
        )


# =========================================================
# MEDIUM ATTENTION
# =========================================================

def draw_medium_attention():

    # Fewer boats
    draw_boat(
        move_left_to_right(8, 10),
        88
    )

    draw_boat(
        move_right_to_left(7, 110),
        95,
        True
    )

    # Fewer people
    draw_person(
        move_left_to_right(7, 30),
        114
    )

    draw_person(
        move_left_to_right(5, 130),
        118
    )

    draw_person(
        move_right_to_left(6, 70),
        112
    )

    draw_person(
        move_right_to_left(5, 180),
        117
    )


# =========================================================
# HIGH ATTENTION
# =========================================================

def draw_high_attention():

    # Very few human distractions
    draw_boat(
        move_left_to_right(4, 80),
        91
    )

    draw_person(
        move_right_to_left(3, 50),
        117
    )

    # Birds
    draw_bird(
        move_left_to_right(8, 0),
        32
    )

    draw_bird(
        move_left_to_right(10, 90),
        42
    )

    draw_bird(
        move_right_to_left(7, 40),
        35
    )

    draw_bird(
        move_right_to_left(9, 150),
        48
    )

    # Crabs
    draw_crab(
        move_left_to_right(4, 0),
        121
    )

    draw_crab(
        move_left_to_right(3, 90),
        126
    )

    draw_crab(
        move_left_to_right(5, 180),
        117
    )

    draw_crab(
        move_right_to_left(4, 40),
        124
    )

    draw_crab(
        move_right_to_left(3, 130),
        119
    )

    draw_crab(
        move_right_to_left(5, 210),
        128
    )

    # Turtles
    draw_turtle(
        move_left_to_right(2, 20),
        110
    )

    draw_turtle(
        move_left_to_right(1.5, 150),
        116
    )

    draw_turtle(
        move_right_to_left(2, 90),
        113,
        True
    )

    draw_turtle(
        move_right_to_left(1.3, 210),
        120,
        True
    )


# =========================================================
# MAIN LOOP
# =========================================================

while True:

    now_loop = time.time()

    delta_time = (
        now_loop
        - last_loop_time
    )

    last_loop_time = now_loop


    # =====================================================
    # BUTTON INPUT
    # =====================================================

    current_a = button_a.value
    current_b = button_b.value

    both_pressed = (
        not current_a
        and not current_b
    )


    # A + B = Toggle Demo / Real
    if both_pressed:

        if not combo_active:

            demo_mode = not demo_mode

            if demo_mode:

                demo_start_time = time.time()

                demo_focus_minutes = 0

                print("DEMO MODE")

            else:

                print("REAL MODE")

            combo_active = True

    else:

        combo_active = False


        # A = Start Focus
        if (
            last_button_a
            and not current_a
        ):

            if not focus_active:

                focus_active = True

                print("FOCUS STARTED")


        # B = Stop Focus
        if (
            last_button_b
            and not current_b
        ):

            if focus_active:

                focus_active = False

                print("FOCUS STOPPED")


    last_button_a = current_a
    last_button_b = current_b


    # =====================================================
    # FOCUS TIMER
    # =====================================================

    if focus_active:

        if demo_mode:

            # Demo:
            # 1 real second = 1 simulated focus minute
            demo_focus_minutes += delta_time

        else:

            total_focus_seconds += delta_time


    # =====================================================
    # STATE
    # =====================================================

    hour = get_current_hour()

    attention = get_attention_level()

    phase = get_phase(hour)

    focus_minutes = get_focus_minutes()

    sky_color = get_sky_color(
        hour,
        attention
    )


    # =====================================================
    # LAYOUT
    # =====================================================

    horizon_y = 80
    beach_y = 108


    # =====================================================
    # SKY
    # =====================================================

    draw.rectangle(
        (
            0,
            0,
            width,
            horizon_y
        ),
        fill=sky_color
    )


    # =====================================================
    # SUN / MOON
    # =====================================================

    if 6 <= hour <= 18:

        # LOW = cloudy, no sun visible
        if attention != "LOW":

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


    # =====================================================
    # WEATHER
    # =====================================================

    draw_weather(
        attention
    )


    # =====================================================
    # OCEAN
    # =====================================================

    draw_ocean(
        horizon_y,
        beach_y,
        attention
    )


    # =====================================================
    # BEACH
    # =====================================================

    draw_beach(
        beach_y
    )


    # =====================================================
    # ATTENTION ACTIVITY
    # =====================================================

    if attention == "LOW":

        draw_low_attention()

    elif attention == "MEDIUM":

        draw_medium_attention()

    else:

        draw_high_attention()


    # =====================================================
    # PALM TREE SWAY
    # =====================================================

    if attention == "HIGH":

        sway = int(
            math.sin(
                time.time() * 2
            ) * 3
        )

    elif attention == "MEDIUM":

        sway = int(
            math.sin(
                time.time()
            ) * 1
        )

    else:

        sway = 0


    # =====================================================
    # PALM TREES
    # =====================================================

    draw_palm_tree(
        10,
        130,
        sway
    )

    draw_palm_tree(
        32,
        132,
        sway
    )

    draw_palm_tree(
        178,
        131,
        -sway
    )

    draw_palm_tree(
        210,
        132,
        -sway
    )


    # =====================================================
    # STATUS BAR
    # =====================================================

    mode_text = (
        "DEMO"
        if demo_mode
        else "REAL"
    )

    focus_minutes_int = int(focus_minutes)

    focus_status_text = (
        f"FOCUS [{focus_minutes_int}m]"
        if focus_active
        else f"IDLE [{focus_minutes_int}m]"
    )


    # -----------------------------------------------------
    # LEFT: MODE + PHASE
    # -----------------------------------------------------

    draw.text(
        (5, 3),
        mode_text,
        font=small_font,
        fill="white"
    )

    draw.text(
        (5, 13),
        phase,
        font=small_font,
        fill="white"
    )


    # -----------------------------------------------------
    # CENTER: REAL / DEMO TIME
    # -----------------------------------------------------

    if demo_mode:

        display_hour = int(hour)

        display_minute = int(
            (hour - display_hour) * 60
        )

        time_text = (
            f"{display_hour:02d}:"
            f"{display_minute:02d}"
        )

    else:

        time_text = time.strftime("%H:%M")


    bbox = draw.textbbox(
        (0, 0),
        time_text,
        font=font
    )

    time_width = (
        bbox[2]
        - bbox[0]
    )

    time_x = int(
        (width - time_width) / 2
    )

    draw.text(
        (
            time_x,
            5
        ),
        time_text,
        font=font,
        fill="white"
    )


    # -----------------------------------------------------
    # RIGHT: FOCUS + TIME
    # -----------------------------------------------------

    right_x = 165

    draw.text(
        (
            right_x,
            3
        ),
        focus_status_text,
        font=small_font,
        fill="white"
    )

    draw.text(
        (
            right_x,
            13
        ),
        attention,
        font=small_font,
        fill="white"
    )


    # =====================================================
    # DISPLAY
    # =====================================================

    disp.image(
        image,
        rotation
    )

    time.sleep(0.05)