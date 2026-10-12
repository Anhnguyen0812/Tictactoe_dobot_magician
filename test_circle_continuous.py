"""Test one continuous native circle without drawing a radius line.

Required files:
    test_circle_continuous.py
    robot_controller.py
    lib/DobotDllType.py
    lib/DobotDll.dll

Test flow:
1. Manually/JOG place the pen at the desired circle centre on the paper.
2. The current X/Y become the circle centre and current Z becomes draw height.
3. Lift vertically at the centre.
4. Move horizontally to the circle edge while lifted.
5. Lower vertically at the edge.
6. Draw one native full circle with SetCircleCmd.
7. Lift vertically at the edge.
8. Return above the centre without touching the paper.

No HOME command is used.
"""
from __future__ import annotations

import math

from robot_controller import DobotController

# ============================================================
# TEST CONFIGURATION
# ============================================================
COM_PORT = "COM40"
BAUDRATE = 115200

DEFAULT_RADIUS_MM = 10.0
DEFAULT_HOVER_HEIGHT_MM = 15.0

MIN_RADIUS_MM = 3.0
MAX_RADIUS_MM = 40.0
MIN_HOVER_HEIGHT_MM = 5.0
MAX_HOVER_HEIGHT_MM = 50.0

# Basic Cartesian guardrails for this local test.
# Adjust only after confirming the real workspace in DobotStudio.
X_MIN = 140.0
X_MAX = 320.0
Y_MIN = -220.0
Y_MAX = 220.0
Z_MIN = -100.0
Z_MAX = 150.0


def read_float(prompt: str, default: float) -> float:
    value = input(f"{prompt} [{default:g}]: ").strip()
    return default if not value else float(value)


def inside_workspace(x: float, y: float, z: float) -> bool:
    return X_MIN <= x <= X_MAX and Y_MIN <= y <= Y_MAX and Z_MIN <= z <= Z_MAX


def validate_circle(cx: float, cy: float, z_draw: float,
                    radius: float, hover_height: float) -> None:
    if not MIN_RADIUS_MM <= radius <= MAX_RADIUS_MM:
        raise ValueError(
            f"Radius must be in [{MIN_RADIUS_MM}, {MAX_RADIUS_MM}] mm"
        )

    if not MIN_HOVER_HEIGHT_MM <= hover_height <= MAX_HOVER_HEIGHT_MM:
        raise ValueError(
            f"Hover height must be in "
            f"[{MIN_HOVER_HEIGHT_MM}, {MAX_HOVER_HEIGHT_MM}] mm"
        )

    z_hover = z_draw + hover_height
    check_points = (
        ("centre draw", cx, cy, z_draw),
        ("centre hover", cx, cy, z_hover),
        ("east", cx + radius, cy, z_draw),
        ("west", cx - radius, cy, z_draw),
        ("north", cx, cy + radius, z_draw),
        ("south", cx, cy - radius, z_draw),
    )

    unsafe = [
        (name, x, y, z)
        for name, x, y, z in check_points
        if not inside_workspace(x, y, z)
    ]
    if unsafe:
        details = ", ".join(
            f"{name}=({x:.1f}, {y:.1f}, {z:.1f})"
            for name, x, y, z in unsafe
        )
        raise ValueError(f"Circle is outside configured workspace: {details}")


def print_pose(prefix: str, pose) -> None:
    print(
        f"[{prefix}] X={pose[0]:.2f}, Y={pose[1]:.2f}, "
        f"Z={pose[2]:.2f}, R={pose[3]:.2f}"
    )


def draw_circle_without_radius(
    robot: DobotController,
    radius: float,
    hover_height: float,
) -> None:
    centre_pose = robot.pose()
    cx, cy, z_draw, r_head = centre_pose[:4]
    z_hover = z_draw + hover_height
    edge_x = cx + radius
    edge_y = cy

    validate_circle(cx, cy, z_draw, radius, hover_height)

    print_pose("CENTRE/DRAW", centre_pose)
    print(f"[TEST] Radius={radius:.2f} mm, hover={hover_height:.2f} mm")
    print(
        "[PATH] centre draw -> centre hover -> edge hover -> "
        "edge draw -> full circle -> edge hover -> centre hover"
    )

    # 1. Lift vertically at the centre, so no radius is drawn.
    print("[1/6] Lift vertically at centre...")
    robot.move(cx, cy, z_hover, r_head, wait=True)

    # 2. Move above the east edge while the pen is off the paper.
    print("[2/6] Move above circle edge...")
    robot.move(edge_x, edge_y, z_hover, r_head, wait=True)

    # 3. Lower vertically at the edge, creating no radial stroke.
    print("[3/6] Lower vertically at circle edge...")
    robot.move(edge_x, edge_y, z_draw, r_head, wait=True)

    # Verify start position before native circle command.
    actual_start = robot.pose()
    start_error = math.sqrt(
        (actual_start[0] - edge_x) ** 2
        + (actual_start[1] - edge_y) ** 2
        + (actual_start[2] - z_draw) ** 2
    )
    if start_error > 2.0:
        raise RuntimeError(
            f"Robot did not reach circle edge; error={start_error:.2f} mm"
        )

    # 4. One continuous native circle.
    print("[4/6] Draw one continuous native circle...")
    robot.circle_continuous(cx, cy, z_draw, radius, r_head)

    # 5. Lift vertically at the edge.
    print("[5/6] Lift vertically at circle edge...")
    robot.move(edge_x, edge_y, z_hover, r_head, wait=True)

    # 6. Return above the centre. Do not lower, to avoid marking the centre.
    print("[6/6] Return above centre...")
    robot.move(cx, cy, z_hover, r_head, wait=True)

    final_pose = robot.pose()
    print_pose("FINISHED/HOVER", final_pose)
    print("[DONE] Circle test completed without drawing a radius line.")


def main() -> None:
    print("=" * 68)
    print("DOBOT CONTINUOUS CIRCLE TEST")
    print("=" * 68)
    print("1. Clear the robot workspace.")
    print("2. Place the pen at the desired circle CENTRE on the paper.")
    print("3. The current Z will be used as the drawing height.")
    print("4. The program does not call HOME.")
    print("5. Press Ctrl+C to stop if the path is unexpected.")
    print("=" * 68)

    radius = read_float("Circle radius in mm", DEFAULT_RADIUS_MM)
    hover_height = read_float(
        "Vertical lift above paper in mm",
        DEFAULT_HOVER_HEIGHT_MM,
    )

    confirmation = input(
        "Type DRAW to run the physical robot test: "
    ).strip()
    if confirmation != "DRAW":
        print("Cancelled.")
        return

    robot = DobotController(COM_PORT, BAUDRATE)
    try:
        if robot.has_alarm():
            robot.print_alarms()
            robot.recover()

        draw_circle_without_radius(robot, radius, hover_height)

    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.")
        robot.force_stop()
    except Exception as error:
        print(f"\n[ERROR] {error}")
        try:
            robot.force_stop()
            if robot.has_alarm():
                robot.print_alarms()
                robot.recover()
        except Exception as recovery_error:
            print(f"[RECOVERY FAILED] {recovery_error}")
    finally:
        robot.close()


if __name__ == "__main__":
    main()
