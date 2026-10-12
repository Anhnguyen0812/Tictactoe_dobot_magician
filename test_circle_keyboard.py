"""Keyboard JOG + continuous native circle test for Dobot Magician.

Controls:
    Hold W / S       : X+ / X-
    Hold D / A       : Y+ / Y-
    Hold Up / Down   : Z+ / Z-
    Hold Right / Left: R+ / R-
    P                : print current pose
    C + 1..9         : draw continuous circle; radius = digit * 5 mm
    ESC              : force stop and exit

Circle workflow:
1. Current position is treated as the circle centre and drawing height.
2. Lift vertically at the centre.
3. Move horizontally above the east edge.
4. Lower vertically at the edge.
5. Draw one continuous native circle.
6. Lift vertically at the edge.
7. Return above the centre.

No HOME command is used.
"""
from __future__ import annotations

import math
import time

import keyboard

from lib import DobotDllType as dType
from robot_controller import DobotController

COM_PORT = "COM40"
BAUDRATE = 115200

CONTROL_INTERVAL = 0.02
POSE_CHECK_INTERVAL = 0.06
RADIUS_STEP_MM = 3.0
HOVER_HEIGHT_MM = 15.0

# Conservative JOG setup. Raise only after direction testing.
JOG_XYZ_VELOCITY = 35.0
JOG_XYZ_ACCELERATION = 25.0
JOG_R_VELOCITY = 45.0
JOG_R_ACCELERATION = 30.0
JOG_VELOCITY_RATIO = 45.0
JOG_ACCELERATION_RATIO = 35.0

# Local software guardrails. Confirm these for your actual setup.
X_MIN = 140.0
X_MAX = 320.0
Y_MIN = -220.0
Y_MAX = 220.0
Z_MIN = -100.0
Z_MAX = 150.0
LIMIT_MARGIN_MM = 5.0


def configure_jog(robot: DobotController) -> None:
    dType.SetJOGCoordinateParams(
        robot.api,
        JOG_XYZ_VELOCITY,
        JOG_XYZ_ACCELERATION,
        JOG_XYZ_VELOCITY,
        JOG_XYZ_ACCELERATION,
        JOG_XYZ_VELOCITY,
        JOG_XYZ_ACCELERATION,
        JOG_R_VELOCITY,
        JOG_R_ACCELERATION,
        isQueued=0,
    )
    dType.SetJOGCommonParams(
        robot.api,
        JOG_VELOCITY_RATIO,
        JOG_ACCELERATION_RATIO,
        isQueued=0,
    )


def print_pose(robot: DobotController, prefix: str = "POSE") -> None:
    pose = robot.pose()
    print(
        f"[{prefix}] X={pose[0]:.2f}, Y={pose[1]:.2f}, "
        f"Z={pose[2]:.2f}, R={pose[3]:.2f}"
    )


def print_help() -> None:
    print("=" * 68)
    print("DOBOT KEYBOARD + CONTINUOUS CIRCLE TEST")
    print("=" * 68)
    print(" Hold W / S        : X+ / X-")
    print(" Hold D / A        : Y+ / Y-")
    print(" Hold Up / Down    : Z+ / Z-")
    print(" Hold Right / Left : R+ / R-")
    print(" P                 : print current pose")
    print(" C + digit 1..9    : circle radius = digit * 5 mm")
    print(" ESC               : force-stop and exit")
    print("-" * 68)
    print("Before C+digit, place the pen at the desired circle CENTRE")
    print("and at the correct drawing Z. The program does not call HOME.")
    print("=" * 68)


def requested_jog():
    if keyboard.is_pressed("w"):
        return dType.JC.JogAPPressed, "+X"
    if keyboard.is_pressed("s"):
        return dType.JC.JogANPressed, "-X"
    if keyboard.is_pressed("d"):
        return dType.JC.JogBPPressed, "+Y"
    if keyboard.is_pressed("a"):
        return dType.JC.JogBNPressed, "-Y"
    if keyboard.is_pressed("up"):
        return dType.JC.JogCPPressed, "+Z"
    if keyboard.is_pressed("down"):
        return dType.JC.JogCNPressed, "-Z"
    if keyboard.is_pressed("right"):
        return dType.JC.JogDPPressed, "+R"
    if keyboard.is_pressed("left"):
        return dType.JC.JogDNPressed, "-R"
    return dType.JC.JogIdle, ""


def requested_circle_radius():
    if not keyboard.is_pressed("c"):
        return None
    for digit in range(1, 10):
        if keyboard.is_pressed(str(digit)):
            return digit * RADIUS_STEP_MM
    return None


def inside_workspace(x: float, y: float, z: float) -> bool:
    return X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX and Z_MIN <= z <= Z_MAX


def jog_direction_is_safe(command, pose) -> bool:
    x, y, z = pose[:3]
    checks = {
        dType.JC.JogAPPressed: x < X_MAX - LIMIT_MARGIN_MM,
        dType.JC.JogANPressed: x > X_MIN + LIMIT_MARGIN_MM,
        dType.JC.JogBPPressed: y < Y_MAX - LIMIT_MARGIN_MM,
        dType.JC.JogBNPressed: y > Y_MIN + LIMIT_MARGIN_MM,
        dType.JC.JogCPPressed: z < Z_MAX - LIMIT_MARGIN_MM,
        dType.JC.JogCNPressed: z > Z_MIN + LIMIT_MARGIN_MM,
    }
    return checks.get(command, True)


def stop_jog(robot: DobotController) -> None:
    dType.SetJOGCmd(
        robot.api,
        isJoint=0,
        cmd=dType.JC.JogIdle,
        isQueued=0,
    )


def start_jog(robot: DobotController, command) -> None:
    dType.SetJOGCmd(
        robot.api,
        isJoint=0,
        cmd=command,
        isQueued=0,
    )


def validate_circle(cx: float, cy: float, z_draw: float, radius: float) -> None:
    z_hover = z_draw + HOVER_HEIGHT_MM
    points = (
        ("centre draw", cx, cy, z_draw),
        ("centre hover", cx, cy, z_hover),
        ("east", cx + radius, cy, z_draw),
        ("west", cx - radius, cy, z_draw),
        ("north", cx, cy + radius, z_draw),
        ("south", cx, cy - radius, z_draw),
    )
    unsafe = [item for item in points if not inside_workspace(*item[1:])]
    if unsafe:
        detail = ", ".join(
            f"{name}=({x:.1f},{y:.1f},{z:.1f})"
            for name, x, y, z in unsafe
        )
        raise ValueError(f"Circle outside configured workspace: {detail}")


def draw_circle_without_radius(
    robot: DobotController,
    radius: float,
) -> None:
    """Use current pose as centre; lift, move to edge, lower, then draw."""
    stop_jog(robot)

    if robot.has_alarm():
        robot.print_alarms()
        robot.recover()
        configure_jog(robot)

    centre = robot.pose()
    cx, cy, z_draw, r_head = centre[:4]
    z_hover = z_draw + HOVER_HEIGHT_MM
    edge_x, edge_y = cx + radius, cy

    validate_circle(cx, cy, z_draw, radius)
    print(
        f"\n[CIRCLE] centre=({cx:.2f}, {cy:.2f}, {z_draw:.2f}), "
        f"radius={radius:.2f} mm"
    )

    try:
        # Lift at centre. The pen is no longer touching the paper.
        robot.move(cx, cy, z_hover, r_head, wait=True)
        # Move to edge while lifted, so no radius line is drawn.
        robot.move(edge_x, edge_y, z_hover, r_head, wait=True)
        # Lower vertically only at the circumference.
        robot.move(edge_x, edge_y, z_draw, r_head, wait=True)

        actual = robot.pose()
        edge_error = math.sqrt(
            (actual[0] - edge_x) ** 2
            + (actual[1] - edge_y) ** 2
            + (actual[2] - z_draw) ** 2
        )
        if edge_error > 2.0:
            raise RuntimeError(
                f"Robot is {edge_error:.2f} mm from circle edge"
            )

        robot.circle_continuous(cx, cy, z_draw, radius, r_head)

        # Lift at the edge and return above centre, without touching paper.
        robot.move(edge_x, edge_y, z_hover, r_head, wait=True)
        robot.move(cx, cy, z_hover, r_head, wait=True)
        print("[CIRCLE] Finished without drawing a radius line.\n")

    except Exception:
        robot.force_stop()
        if robot.has_alarm():
            robot.print_alarms()
        robot.recover()
        configure_jog(robot)
        raise


def main() -> None:
    robot = DobotController(COM_PORT, BAUDRATE)
    configure_jog(robot)
    print_help()
    print_pose(robot, "START")

    active_jog = dType.JC.JogIdle
    previous_p = False
    circle_latched = False
    last_pose_check = 0.0

    try:
        while True:
            now = time.monotonic()

            if keyboard.is_pressed("esc"):
                print("\n[STOP] ESC pressed.")
                stop_jog(robot)
                robot.force_stop()
                break

            radius = requested_circle_radius()
            if radius is not None and not circle_latched:
                circle_latched = True
                active_jog = dType.JC.JogIdle
                try:
                    draw_circle_without_radius(robot, radius)
                except Exception as error:
                    print(f"[CIRCLE ERROR] {error}")
            elif radius is None:
                circle_latched = False

            if radius is not None:
                time.sleep(CONTROL_INTERVAL)
                continue

            p_pressed = keyboard.is_pressed("p")
            if p_pressed and not previous_p:
                print_pose(robot)
            previous_p = p_pressed

            command, label = requested_jog()

            if (
                command != dType.JC.JogIdle
                and now - last_pose_check >= POSE_CHECK_INTERVAL
            ):
                pose = robot.pose()
                last_pose_check = now
                if robot.has_alarm():
                    robot.print_alarms()
                    command = dType.JC.JogIdle
                elif not jog_direction_is_safe(command, pose):
                    print("[SAFETY] Software workspace limit reached.")
                    command = dType.JC.JogIdle

            if command != active_jog:
                if command == dType.JC.JogIdle:
                    if active_jog != dType.JC.JogIdle:
                        stop_jog(robot)
                        print_pose(robot, "JOG STOP")
                else:
                    start_jog(robot, command)
                    print(f"[JOG] {label}")
                active_jog = command

            time.sleep(CONTROL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C pressed.")
        stop_jog(robot)
        robot.force_stop()
    finally:
        robot.close()


if __name__ == "__main__":
    main()
