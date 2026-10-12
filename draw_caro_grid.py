"""Draw an N x N caro/Gomoku grid with Dobot Magician.

N can be selected from 3 to 15.
The robot's CURRENT pose is treated as the TOP-LEFT OUTER CORNER
of the grid and as the pen-down drawing height.

Default coordinate orientation:
    columns extend toward +X
    rows extend toward -Y

Change X_DIRECTION or Y_DIRECTION if the physical board is oriented
in another direction.

The whole grid is drawn line by line. Each grid line is one full straight
stroke drawn with the pen down; the pen is lifted between lines so no drag
marks appear, and the lines are ordered serpentine so the pen-up travel
between consecutive lines stays minimal.

No HOME command is used and SetHOMEParams is never called.
"""
from __future__ import annotations

import time
from typing import List, Tuple

from lib import DobotDllType as dType
from gomoku_ai import MAX_BOARD_SIZE, MIN_BOARD_SIZE

# ============================================================
# CONNECTION
# ============================================================
COM_PORT = "COM40"
BAUDRATE = 115200

# ============================================================
# GRID DEFAULTS
# ============================================================
DEFAULT_BOARD_SIZE = 3
DEFAULT_CELL_SIZE_MM = 25.0
DEFAULT_HOVER_HEIGHT_MM = 12.0

MIN_CELL_SIZE_MM = 8.0
MAX_CELL_SIZE_MM = 50.0
MIN_HOVER_HEIGHT_MM = 5.0
MAX_HOVER_HEIGHT_MM = 50.0

# From TOP-LEFT:
# +1 means increasing coordinate; -1 means decreasing coordinate.
X_DIRECTION = 1.0
Y_DIRECTION = -1.0

# ============================================================
# MOTION SETTINGS
# ============================================================
# Magician firmware limits: xyz velocity/acceleration <= 200,
# r velocity/acceleration <= 100, ratio <= 100.
# Lower the acceleration values if fast, short lines look shaky.
PTP_XYZ_VELOCITY = 200.0
PTP_XYZ_ACCELERATION = 200.0
PTP_R_VELOCITY = 100.0
PTP_R_ACCELERATION = 100.0
PTP_VELOCITY_RATIO = 100.0
PTP_ACCELERATION_RATIO = 100.0

COMMAND_TIMEOUT_SECONDS = 30.0
RECOVERY_DELAY_MS = 100

# Software-only guardrails. Confirm them against the actual installation.
X_MIN = 140.0
X_MAX = 320.0
Y_MIN = -220.0
Y_MAX = 220.0
Z_MIN = -100.0
Z_MAX = 150.0


class GridRobot:
    def __init__(self, port: str, baudrate: int):
        self.api = dType.load()
        self.connected = False

        result = dType.ConnectDobot(self.api, port, baudrate)
        if result[0] != dType.DobotConnect.DobotConnect_NoError:
            raise RuntimeError(
                f"Cannot connect to Dobot on {port}: state={result[0]}"
            )

        self.connected = True
        print(f"Connected: firmware={result[3]} {result[4]}")

        dType.SetQueuedCmdForceStopExec(self.api)
        dType.SetQueuedCmdClear(self.api)
        dType.ClearAllAlarmsState(self.api)
        self.configure_motion()
        dType.SetQueuedCmdStartExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        if self.has_alarm():
            raise RuntimeError(
                f"Alarm after connection: {self.alarm_codes()}"
            )

    def configure_motion(self) -> None:
        # Do not call SetHOMEParams here.
        dType.SetPTPCoordinateParams(
            self.api,
            PTP_XYZ_VELOCITY,
            PTP_XYZ_ACCELERATION,
            PTP_R_VELOCITY,
            PTP_R_ACCELERATION,
            isQueued=0,
        )
        dType.SetPTPCommonParams(
            self.api,
            PTP_VELOCITY_RATIO,
            PTP_ACCELERATION_RATIO,
            isQueued=0,
        )

    def pose(self) -> List[float]:
        return dType.GetPose(self.api)

    def alarm_codes(self) -> List[Tuple[int, int]]:
        raw, length = dType.GetAlarmsState(self.api)
        return [
            (index, value)
            for index, value in enumerate(raw[:length])
            if value != 0
        ]

    def has_alarm(self) -> bool:
        return bool(self.alarm_codes())

    def wait_for_index(self, index: int) -> None:
        deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS

        while index > dType.GetQueuedCmdCurrentIndex(self.api)[0]:
            if self.has_alarm():
                raise RuntimeError(
                    f"Dobot alarm during movement: {self.alarm_codes()}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Command {index} timed out")
            dType.dSleep(5)

        if self.has_alarm():
            raise RuntimeError(
                f"Dobot alarm after movement: {self.alarm_codes()}"
            )

    def move(self, x: float, y: float, z: float, r: float) -> None:
        if self.has_alarm():
            raise RuntimeError(
                f"Cannot move while alarm is active: {self.alarm_codes()}"
            )

        index = dType.SetPTPCmd(
            self.api,
            dType.PTPMode.PTPMOVLXYZMode,
            x,
            y,
            z,
            r,
            isQueued=1,
        )[0]
        self.wait_for_index(index)

    def recover(self) -> None:
        print("[RECOVERY] Stopping, clearing queue and alarm...")
        dType.SetQueuedCmdForceStopExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)
        dType.SetQueuedCmdClear(self.api)
        dType.ClearAllAlarmsState(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)
        self.configure_motion()
        dType.SetQueuedCmdStartExec(self.api)
        dType.dSleep(RECOVERY_DELAY_MS)

        if self.has_alarm():
            raise RuntimeError(
                f"Alarm remains after recovery: {self.alarm_codes()}"
            )

    def close(self) -> None:
        if not self.connected:
            return
        try:
            dType.SetQueuedCmdForceStopExec(self.api)
            dType.SetQueuedCmdClear(self.api)
        finally:
            dType.DisconnectDobot(self.api)
            self.connected = False
            print("Dobot disconnected.")


def read_int(prompt: str, default: int, minimum: int, maximum: int) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"Enter an integer from {minimum} to {maximum}.")


def read_float(
    prompt: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    while True:
        raw = input(f"{prompt} [{default:g}]: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
            if minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print(f"Enter a number from {minimum:g} to {maximum:g}.")


def inside_workspace(x: float, y: float, z: float) -> bool:
    return (
        X_MIN <= x <= X_MAX
        and Y_MIN <= y <= Y_MAX
        and Z_MIN <= z <= Z_MAX
    )


def validate_grid(
    top_left_x: float,
    top_left_y: float,
    z_draw: float,
    hover_height: float,
    board_size: int,
    cell_size: float,
) -> Tuple[float, float]:
    total_size = board_size * cell_size
    bottom_right_x = top_left_x + X_DIRECTION * total_size
    bottom_right_y = top_left_y + Y_DIRECTION * total_size
    z_hover = z_draw + hover_height

    points = (
        ("top-left draw", top_left_x, top_left_y, z_draw),
        ("top-left hover", top_left_x, top_left_y, z_hover),
        ("top-right", bottom_right_x, top_left_y, z_draw),
        ("bottom-left", top_left_x, bottom_right_y, z_draw),
        ("bottom-right", bottom_right_x, bottom_right_y, z_draw),
    )

    unsafe = [
        (name, x, y, z)
        for name, x, y, z in points
        if not inside_workspace(x, y, z)
    ]
    if unsafe:
        details = ", ".join(
            f"{name}=({x:.1f}, {y:.1f}, {z:.1f})"
            for name, x, y, z in unsafe
        )
        raise ValueError(
            f"Grid is outside configured workspace: {details}"
        )

    return bottom_right_x, bottom_right_y


def build_line_plan(
    board_size: int,
) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """Return the grid lines in a serpentine order that minimizes travel.

    Each entry is ((row, col), (row, col)) describing one full grid line.
    Lines are ordered so the end of a line is adjacent to the start of the
    next one, which keeps the pen-up travel to a single cell.
    """
    last = board_size
    lines: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []

    # Horizontal lines: top to bottom, alternating direction.
    for row in range(last + 1):
        if row % 2 == 0:
            lines.append(((row, 0), (row, last)))
        else:
            lines.append(((row, last), (row, 0)))

    # Vertical lines continue from the side where the horizontals ended.
    if lines[-1][1][1] == 0:
        columns = range(0, last + 1)
    else:
        columns = range(last, -1, -1)

    going_up = True
    for col in columns:
        if going_up:
            lines.append(((last, col), (0, col)))
        else:
            lines.append(((0, col), (last, col)))
        going_up = not going_up

    return lines


def draw_one_line(
    robot: GridRobot,
    start: Tuple[float, float],
    end: Tuple[float, float],
    z_draw: float,
    z_hover: float,
    r_head: float,
) -> None:
    """Travel with the pen up, lower, draw exactly one line, then lift."""
    sx, sy = start
    ex, ey = end

    robot.move(sx, sy, z_hover, r_head)
    robot.move(sx, sy, z_draw, r_head)
    robot.move(ex, ey, z_draw, r_head)
    robot.move(ex, ey, z_hover, r_head)


def draw_grid(
    robot: GridRobot,
    board_size: int,
    cell_size: float,
    hover_height: float,
) -> None:
    top_left = robot.pose()
    x0, y0, z_draw, r_head = top_left[:4]
    z_hover = z_draw + hover_height

    bottom_right_x, bottom_right_y = validate_grid(
        x0,
        y0,
        z_draw,
        hover_height,
        board_size,
        cell_size,
    )

    lines = build_line_plan(board_size)

    print(
        f"[GRID] {board_size}x{board_size}, "
        f"cell={cell_size:.1f} mm, total={board_size * cell_size:.1f} mm"
    )
    print(
        f"[TOP-LEFT] X={x0:.2f}, Y={y0:.2f}, "
        f"Z={z_draw:.2f}, R={r_head:.2f}"
    )
    print(
        f"[BOTTOM-RIGHT] X={bottom_right_x:.2f}, "
        f"Y={bottom_right_y:.2f}"
    )
    print(
        f"[PATH] {len(lines)} lines, one pen-down per line, "
        f"serpentine order to minimize travel."
    )

    for step, (start_rc, end_rc) in enumerate(lines, start=1):
        start_row, start_col = start_rc
        end_row, end_col = end_rc
        start = (
            x0 + X_DIRECTION * start_col * cell_size,
            y0 + Y_DIRECTION * start_row * cell_size,
        )
        end = (
            x0 + X_DIRECTION * end_col * cell_size,
            y0 + Y_DIRECTION * end_row * cell_size,
        )

        print(
            f"[LINE] {step}/{len(lines)} "
            f"({start[0]:.1f}, {start[1]:.1f}) -> "
            f"({end[0]:.1f}, {end[1]:.1f})"
        )
        draw_one_line(robot, start, end, z_draw, z_hover, r_head)

    print("[DONE] Grid completed. Pen is lifted above the last line.")


def main() -> None:
    print("=" * 72)
    print(f"DOBOT CARO GRID DRAWER: {MIN_BOARD_SIZE}x{MIN_BOARD_SIZE} "
          f"TO {MAX_BOARD_SIZE}x{MAX_BOARD_SIZE}")
    print("=" * 72)
    print("Place the pen at the TOP-LEFT OUTER CORNER of the grid.")
    print("The current Z must be the drawing height.")
    print("Default direction: columns +X, rows -Y.")
    print("No HOME command is used.")
    print("=" * 72)

    board_size = read_int(
        "Board size",
        DEFAULT_BOARD_SIZE,
        MIN_BOARD_SIZE,
        MAX_BOARD_SIZE,
    )
    cell_size = read_float(
        "Cell size in mm",
        DEFAULT_CELL_SIZE_MM,
        MIN_CELL_SIZE_MM,
        MAX_CELL_SIZE_MM,
    )
    hover_height = read_float(
        "Pen lift height in mm",
        DEFAULT_HOVER_HEIGHT_MM,
        MIN_HOVER_HEIGHT_MM,
        MAX_HOVER_HEIGHT_MM,
    )

    total_size = board_size * cell_size
    print(
        f"Grid preview: {board_size}x{board_size}, "
        f"physical size={total_size:.1f} x {total_size:.1f} mm"
    )

    confirmation = input(
        "Type DRAW to start the physical robot: "
    ).strip()
    if confirmation != "DRAW":
        print("Cancelled.")
        return

    robot = GridRobot(COM_PORT, BAUDRATE)
    try:
        draw_grid(robot, board_size, cell_size, hover_height)
    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.")
        dType.SetQueuedCmdForceStopExec(robot.api)
    except Exception as error:
        print(f"\n[ERROR] {error}")
        try:
            dType.SetQueuedCmdForceStopExec(robot.api)
            if robot.has_alarm():
                print(f"[ALARM] {robot.alarm_codes()}")
                robot.recover()
        except Exception as recovery_error:
            print(f"[RECOVERY FAILED] {recovery_error}")
    finally:
        robot.close()


if __name__ == "__main__":
    main()
