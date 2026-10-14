"""Sugar Cube Dropper: detect a cup and open a one-cube servo gate.

Hardware:
- SparkFun VCNL4040 Qwiic proximity sensor
- SparkFun Servo pHAT with the servo on channel 1

Run ``python sugar_cube_dropper.py --dry-run`` before connecting hardware to
preview the state transitions. Calibrate ``--cup-threshold`` with
``qwiic_distance.py`` before running the physical prototype.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Iterable


SERVO_CHANNEL = 1
GATE_CLOSED = 90
GATE_OPEN = 35
CUP_THRESHOLD = 1000
RELEASE_THRESHOLD = 700
CONFIRM_READINGS = 3


class ConsoleServo:
    """Servo stand-in used by dry-run mode."""

    def move_servo_position(self, channel: int, position: int, swing: int = 180) -> None:
        print(f"[dry-run] servo channel {channel} -> {position} degrees")


def dispense_one_cube(servo, channel: int, open_angle: int, closed_angle: int) -> None:
    """Open the gate briefly, then return it to the closed position."""
    print("Cup confirmed. Dispensing one sugar cube...")
    servo.move_servo_position(channel, open_angle, swing=180)
    time.sleep(0.7)
    servo.move_servo_position(channel, closed_angle, swing=180)
    time.sleep(0.4)
    print("Gate closed. Remove the cup before the next serving.")


def monitor(
    readings: Iterable[int],
    servo,
    *,
    cup_threshold: int,
    release_threshold: int,
    confirm_readings: int,
    channel: int,
    open_angle: int,
    closed_angle: int,
    delay: float,
) -> None:
    """Run a one-shot-per-cup state machine over proximity readings."""
    above_count = 0
    armed = True

    servo.move_servo_position(channel, closed_angle, swing=180)
    print("Sugar Cube Dropper ready. Place a cup under the chute.")

    for proximity in readings:
        state = "ARMED" if armed else "WAITING FOR CUP REMOVAL"
        print(f"Proximity: {proximity:>6} | {state}")

        if armed:
            above_count = above_count + 1 if proximity >= cup_threshold else 0
            if above_count >= confirm_readings:
                dispense_one_cube(servo, channel, open_angle, closed_angle)
                armed = False
                above_count = 0
        elif proximity <= release_threshold:
            armed = True
            print("Cup removed. Dropper re-armed.")

        time.sleep(delay)


def hardware_readings(sensor, delay: float):
    while True:
        yield sensor.get_proximity()
        time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="simulate two cup placements")
    parser.add_argument("--cup-threshold", type=int, default=CUP_THRESHOLD)
    parser.add_argument("--release-threshold", type=int, default=RELEASE_THRESHOLD)
    parser.add_argument("--confirm-readings", type=int, default=CONFIRM_READINGS)
    parser.add_argument("--channel", type=int, choices=range(16), default=SERVO_CHANNEL)
    parser.add_argument("--open-angle", type=int, default=GATE_OPEN)
    parser.add_argument("--closed-angle", type=int, default=GATE_CLOSED)
    args = parser.parse_args()

    if args.release_threshold >= args.cup_threshold:
        parser.error("--release-threshold must be lower than --cup-threshold")
    if args.confirm_readings < 1:
        parser.error("--confirm-readings must be at least 1")

    if args.dry_run:
        readings = [100, 250, 1100, 1250, 1400, 1300, 650, 200, 1200, 1350, 1500, 300]
        monitor(
            readings,
            ConsoleServo(),
            cup_threshold=args.cup_threshold,
            release_threshold=args.release_threshold,
            confirm_readings=args.confirm_readings,
            channel=args.channel,
            open_angle=args.open_angle,
            closed_angle=args.closed_angle,
            delay=0.05,
        )
        return

    import pi_servo_hat
    import qwiic_proximity

    sensor = qwiic_proximity.QwiicProximity()
    if not sensor.connected:
        raise RuntimeError("VCNL4040 not found. Check the Qwiic connection.")
    sensor.begin()

    servo = pi_servo_hat.PiServoHat(address=0x40, min_pt=1, max_pt=2)
    servo.restart()

    try:
        monitor(
            hardware_readings(sensor, 0.1),
            servo,
            cup_threshold=args.cup_threshold,
            release_threshold=args.release_threshold,
            confirm_readings=args.confirm_readings,
            channel=args.channel,
            open_angle=args.open_angle,
            closed_angle=args.closed_angle,
            delay=0.0,
        )
    except KeyboardInterrupt:
        print("\nStopping Sugar Cube Dropper.")
    finally:
        servo.move_servo_position(args.channel, args.closed_angle, swing=180)


if __name__ == "__main__":
    main()
