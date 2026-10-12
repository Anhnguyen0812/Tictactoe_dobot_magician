"""Variable-size 3x3..15x15 robot Gomoku game.

Supports easy/medium/hard levels and a choice of who moves first. A failed
drawing operation does not end the game: the player can reposition the arm and
retry, or reconnect the robot and retry.
"""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

from gomoku_ai import (
    BOARD_SIZES,
    DIFFICULTIES,
    DIFFICULTY_HARD,
    MAX_BOARD_SIZE,
    MIN_BOARD_SIZE,
    GomokuAI,
    GomokuGame,
)
from robot_controller import DobotController

# Players the human can put first at the start of a game.
FIRST_PLAYERS = ("human", "robot")

# Answers accepted by the recovery prompt shown after a failed drawing.
RETRY_DRAW = "1"
RECONNECT_ROBOT = "2"
STOP_GAME = "3"

DOBOT_PORT = "COM40"
CAMERA_INDEX = 0
CAMERA_MAX_INDEX = 5
BOARD_PIXELS = 900
Z_DRAW = -67.0
Z_HOVER = 15.0
CIRCLE_RADIUS_RATIO = 0.27
WIN_LINE_EXTENSION_RATIO = 0.4
DETECT_INTERVAL = 0.15
CONFIRM_SECONDS = 0.45

# Errors the robot raises when a motion cannot be completed: alarms, timeouts
# and rejected parameters (a zero circle radius caused by bad calibration).
# Any other exception is a bug in this script, not a robot fault.
ROBOT_FAULT_ERRORS = (RuntimeError, TimeoutError, ValueError)

# Right-hand overlay panel that shows the played moves and timings.
PANEL_WIDTH = 300
COLOR_ROBOT = (0, 200, 0)
COLOR_HUMAN = (0, 140, 255)
COLOR_PANEL_BG = (28, 28, 28)

# Backends tried in order when opening the camera.
CAMERA_BACKENDS = tuple(
    getattr(cv2, name)
    for name in ("CAP_DSHOW", "CAP_MSMF", "CAP_ANY")
    if hasattr(cv2, name)
)


def open_camera(preferred_index: int = CAMERA_INDEX):
    """Open the first camera that actually delivers frames.

    Tries the preferred index first, then the remaining indices, across all
    known backends. Returns None when no camera can capture.
    """
    indices = [preferred_index] + [
        index for index in range(CAMERA_MAX_INDEX) if index != preferred_index
    ]
    for index in indices:
        for backend in CAMERA_BACKENDS:
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                ok, _ = capture.read()
                if ok:
                    print(f"[CAMERA] Opened index={index}, backend={backend}")
                    return capture
            capture.release()
    return None


def render_side_panel(
    history: list[tuple[str, tuple[int, int]]],
    last_think: Optional[float] = None,
):
    """Build the overlay panel with one colored bar per played move.

    history items are (player, (row, col)) where player is "human" or
    "robot". last_think is the robot's most recent thinking time in seconds.
    """
    panel = np.full((BOARD_PIXELS, PANEL_WIDTH, 3), COLOR_PANEL_BG, np.uint8)
    cv2.line(panel, (0, 0), (0, BOARD_PIXELS - 1), (90, 90, 90), 2)
    cv2.putText(panel, "MOVE HISTORY", (14, 36),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2)

    cursor_y = 60
    if last_think is not None:
        cv2.putText(panel, f"AI think: {last_think:.3f} s", (14, cursor_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 225, 225), 2)
        cursor_y += 26

    step_y = 30
    bar_height = 22
    bar_full = PANEL_WIDTH - 30
    for index, (player, (row, col)) in enumerate(history):
        y = cursor_y + index * step_y
        if y + bar_height > BOARD_PIXELS - 8:
            break
        is_robot = player == "robot"
        color = COLOR_ROBOT if is_robot else COLOR_HUMAN
        # Bar length grows with the move order to read as a timeline.
        fraction = min(1.0, (index + 1) / 12.0)
        bar_length = max(70, int(bar_full * fraction))
        cv2.rectangle(panel, (14, y), (14 + bar_length, y + bar_height),
                      color, -1)
        symbol = "O" if is_robot else "X"
        cv2.putText(panel, f"{index + 1:>2}. {symbol} ({row},{col})",
                    (20, y + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (15, 15, 15), 1, cv2.LINE_AA)
    return panel


class BoardVision:
    def __init__(self, size: int):
        self.size = size
        self.capture = open_camera(CAMERA_INDEX)
        if self.capture is None:
            raise RuntimeError(
                "Cannot open camera. Close other apps using it "
                "(Teams/Zoom/Camera), allow it in Windows Settings > "
                "Privacy > Camera, then retry or change CAMERA_INDEX."
            )
        self.camera_points = None

    def read(self):
        ok, frame = self.capture.read()
        if not ok:
            raise RuntimeError("Cannot read camera frame")
        return frame

    def calibrate(self) -> None:
        points = []
        window = "CAMERA CALIBRATION"

        def click(event, x, y, flags, param):
            if event == cv2.EVENT_LBUTTONDOWN and len(points) < 4:
                points.append((x, y))

        cv2.namedWindow(window)
        cv2.setMouseCallback(window, click)
        while len(points) < 4:
            frame = self.read()
            for i, p in enumerate(points):
                cv2.circle(frame, p, 7, (0, 255, 0), -1)
                cv2.putText(frame, str(i + 1), p, cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (0, 255, 255), 2)
            cv2.putText(frame, "TL, TR, BR, BL | R reset | Q quit", (20, 35),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("r"), ord("R")):
                points.clear()
            elif key in (ord("q"), ord("Q")):
                raise KeyboardInterrupt
        self.camera_points = np.float32(points)
        cv2.destroyWindow(window)

    def warp(self, frame):
        target = np.float32([[0, 0], [BOARD_PIXELS - 1, 0],
                             [BOARD_PIXELS - 1, BOARD_PIXELS - 1],
                             [0, BOARD_PIXELS - 1]])
        matrix = cv2.getPerspectiveTransform(self.camera_points, target)
        return cv2.warpPerspective(frame, matrix, (BOARD_PIXELS, BOARD_PIXELS))

    @staticmethod
    def binary(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(2.0, (8, 8)).apply(gray)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7)
        kernel = np.ones((3, 3), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        return cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)

    def detect_x(self, roi):
        bw = self.binary(roi)
        edges = cv2.Canny(bw, 40, 120)
        scale = max(0.45, roi.shape[0] / 180.0)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
            threshold=max(8, int(16 * scale)),
            minLineLength=max(12, int(28 * scale)),
            maxLineGap=max(6, int(14 * scale)))
        pos = neg = 0
        debug = cv2.cvtColor(bw, cv2.COLOR_GRAY2BGR)
        if lines is not None:
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if 20 <= angle <= 70:
                    pos += 1
                    cv2.line(debug, (x1, y1), (x2, y2), (0, 255, 0), 2)
                elif -70 <= angle <= -20:
                    neg += 1
                    cv2.line(debug, (x1, y1), (x2, y2), (0, 0, 255), 2)
        minimum_ink = max(45, int(180 * scale * scale))
        return pos > 0 and neg > 0 and cv2.countNonZero(bw) >= minimum_ink, debug

    def analyze(self, game: GomokuGame, history=None, last_think=None):
        warped = self.warp(self.read())
        normal = warped.copy()
        black_white = cv2.cvtColor(self.binary(warped), cv2.COLOR_GRAY2BGR)
        cell = BOARD_PIXELS / self.size
        margin_ratio = 0.18
        detected = []

        for r in range(self.size):
            for c in range(self.size):
                x1, y1 = round(c * cell), round(r * cell)
                x2, y2 = round((c + 1) * cell), round((r + 1) * cell)
                for view in (normal, black_white):
                    cv2.rectangle(view, (x1, y1), (x2, y2), (180, 180, 180), 1)
                if not game.is_empty(r, c):
                    continue
                mx, my = int((x2 - x1) * margin_ratio), int((y2 - y1) * margin_ratio)
                roi = warped[y1 + my:y2 - my, x1 + mx:x2 - mx]
                found, debug = self.detect_x(roi)
                black_white[y1 + my:y2 - my, x1 + mx:x2 - mx] = debug
                if found:
                    detected.append((r, c))
                    cv2.rectangle(normal, (x1 + 2, y1 + 2), (x2 - 2, y2 - 2),
                                  (0, 255, 0), 3)
        preview = np.hstack((normal, black_white))
        panel = render_side_panel(history or [], last_think)
        return detected, np.hstack((preview, panel))

    def wait_human(
        self,
        game: GomokuGame,
        history=None,
        last_think=None,
    ) -> Optional[tuple[int, int]]:
        previous = None
        stable_since = None
        window = "Gomoku: normal | detection | move history"
        while True:
            detected, preview = self.analyze(game, history, last_think)
            candidate = detected[0] if len(detected) == 1 else None
            now = time.monotonic()
            if candidate != previous:
                previous, stable_since = candidate, now
            elif candidate is not None and now - stable_since >= CONFIRM_SECONDS:
                return candidate
            cv2.imshow(window, preview)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                return None
            time.sleep(DETECT_INTERVAL)

    def close(self):
        self.capture.release()
        cv2.destroyAllWindows()


class RobotGomoku:
    def __init__(
        self,
        size: int,
        difficulty: str = DIFFICULTY_HARD,
        robot_first: bool = False,
    ):
        self.game = GomokuGame(size)
        self.difficulty = difficulty
        self.robot_first = robot_first
        self.ai = GomokuAI(difficulty=difficulty)
        self.vision = BoardVision(size)
        try:
            self.robot = DobotController(DOBOT_PORT)
        except Exception:
            # The camera is already open, so release it before failing.
            self.vision.close()
            raise
        self.robot_corners = []
        self.centers = []
        self.z_draw = Z_DRAW
        self.history: list[tuple[str, tuple[int, int]]] = []
        self.last_think: Optional[float] = None

    def calibrate_robot(self):
        print("Move the pen with DobotStudio/JOG to TL, TR, BR, BL centers.")
        print("Press ENTER at each position. Do not use HOME.")
        for name in ("TL", "TR", "BR", "BL"):
            input(f"Position at {name}, then press ENTER...")
            pose = self.robot.pose()
            self.robot_corners.append((pose[0], pose[1]))
        self.z_draw = self.robot.pose()[2]
        tl, tr, br, bl = self.robot_corners
        n = self.game.size
        self.centers = []
        for r in range(n):
            v = (r + 0.5) / n
            row = []
            for c in range(n):
                u = (c + 0.5) / n
                top_x = tl[0] + u * (tr[0] - tl[0])
                top_y = tl[1] + u * (tr[1] - tl[1])
                bottom_x = bl[0] + u * (br[0] - bl[0])
                bottom_y = bl[1] + u * (br[1] - bl[1])
                row.append((top_x + v * (bottom_x - top_x),
                            top_y + v * (bottom_y - top_y)))
            self.centers.append(row)

    def _recover_robot(self) -> None:
        """Park the arm after a failed motion without masking the cause."""
        try:
            self.robot.recover()
        except Exception as recovery_error:
            print(f"Recovery failed: {recovery_error}")

    def draw_o(self, row: int, col: int) -> bool:
        cx, cy = self.centers[row][col]
        start_pose = self.robot.pose()
        # Park target: keep the original X/Y but stop at hover height, so the
        # pen tip never touches the paper on the way back.
        park_x, park_y = start_pose[0], start_pose[1]
        r_head = start_pose[3]

        # Radius adapts to the physical spacing between neighboring centers.
        distances = []
        if col + 1 < self.game.size:
            nx, ny = self.centers[row][col + 1]
            distances.append(((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5)
        if row + 1 < self.game.size:
            nx, ny = self.centers[row + 1][col]
            distances.append(((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5)
        radius = min(distances or [12.0]) * CIRCLE_RADIUS_RATIO
        start_x = cx + radius
        hover = self.z_draw + Z_HOVER
        try:
            # Lift straight up first: one combined X/Y/Z move would drag the
            # pen across the board when it starts at drawing height.
            self.robot.move(park_x, park_y, hover, r_head)
            self.robot.move(start_x, cy, hover, r_head)
            self.robot.move(start_x, cy, self.z_draw, r_head)
            self.robot.circle_three_arcs(cx, cy, self.z_draw, radius, r_head)
            self.robot.move(start_x, cy, hover, r_head)
            # Travel back to the original X/Y and stay at hover height.
            self.robot.move(park_x, park_y, hover, r_head)
            return True
        except ROBOT_FAULT_ERRORS as error:
            print(f"Draw failed: {error}")
            self._recover_robot()
            return False
        except Exception:
            # A bug in this script must not be reported as a robot fault: park
            # the arm, then let the traceback surface.
            self._recover_robot()
            raise

    def draw_winning_line(self, cells: list[tuple[int, int]]) -> bool:
        """Draw one straight line through the winning run of cells."""
        if len(cells) < 2:
            return False

        first_x, first_y = self.centers[cells[0][0]][cells[0][1]]
        last_x, last_y = self.centers[cells[-1][0]][cells[-1][1]]
        dx = last_x - first_x
        dy = last_y - first_y
        length = (dx * dx + dy * dy) ** 0.5
        if length <= 0:
            return False

        # Extend the stroke past both end cells so it reads as a strike line.
        extension = length * WIN_LINE_EXTENSION_RATIO
        unit_x, unit_y = dx / length, dy / length
        start_x = first_x - unit_x * extension
        start_y = first_y - unit_y * extension
        end_x = last_x + unit_x * extension
        end_y = last_y + unit_y * extension

        r_head = self.robot.pose()[3]
        hover = self.z_draw + Z_HOVER
        try:
            self.robot.move(start_x, start_y, hover, r_head)
            self.robot.move(start_x, start_y, self.z_draw, r_head)
            self.robot.move(end_x, end_y, self.z_draw, r_head)
            self.robot.move(end_x, end_y, hover, r_head)
            return True
        except ROBOT_FAULT_ERRORS as error:
            print(f"Win line failed: {error}")
            self._recover_robot()
            return False
        except Exception:
            # Keep script bugs visible instead of reporting a robot fault.
            self._recover_robot()
            raise

    def play(self):
        self.vision.calibrate()
        self.calibrate_robot()
        print(f"Difficulty: {self.difficulty}")
        print(f"First player: {'robot' if self.robot_first else 'human'}")
        input("Calibration finished. Press ENTER to start...")
        self.game.print_board()

        robot_turn = self.robot_first
        while not self.game.winner() and not self.game.full():
            if robot_turn:
                if not self.take_robot_turn():
                    return
            else:
                outcome = self.take_human_turn()
                if outcome is None:
                    return
                if not outcome:
                    # The cell was already taken; ask again.
                    continue
            robot_turn = not robot_turn

        self.finish_game()

    def take_human_turn(self) -> Optional[bool]:
        """Wait for one human move. None means the player quit."""
        human_move = self.vision.wait_human(
            self.game, self.history, self.last_think)
        if human_move is None:
            return None
        if not self.game.place(*human_move, self.game.human):
            return False
        self.history.append(("human", human_move))
        print(f"Human: {human_move}")
        self.game.print_board()
        return True

    def take_robot_turn(self) -> bool:
        """Think, draw the O and commit the move. False stops the game.

        When the board is full there is nothing to draw; the caller's loop
        condition then ends the game.
        """
        started = time.perf_counter()
        move = self.ai.choose_move(self.game)
        self.last_think = time.perf_counter() - started
        if move is None:
            return True

        print(f"[AI] move={move} depth={self.ai.last_depth} "
              f"think={self.last_think:.3f}s")
        if not self.draw_with_retry(*move):
            return False

        self.game.place(*move, self.game.robot)
        self.history.append(("robot", move))
        print(f"Robot: {move}")
        self.game.print_board()
        return True

    def draw_with_retry(self, row: int, col: int) -> bool:
        """Draw the O, offering recovery after every failed attempt.

        Returns False only when the player decides to stop the game.
        """
        while True:
            if self.draw_o(row, col):
                return True
            if not self.resolve_draw_failure():
                return False

    def resolve_draw_failure(self) -> bool:
        """Ask how to recover from a failed drawing. False means stop.

        draw_o has already cleared the fault, so the arm is idle and can be
        moved by hand before a retry.
        """
        while True:
            print("The robot could not finish drawing this O.")
            print(f"  {RETRY_DRAW}: move the arm to a safe place, then retry")
            print(f"  {RECONNECT_ROBOT}: reconnect the robot, then retry")
            print(f"  {STOP_GAME}: stop the game")
            answer = input(f"Choice [{RETRY_DRAW}]: ").strip() or RETRY_DRAW

            if answer == RETRY_DRAW:
                if not self.robot_is_ready():
                    print("The robot is not connected; reconnect first.")
                    continue
                return True
            if answer == RECONNECT_ROBOT:
                if self.reconnect_robot():
                    return True
                continue
            if answer == STOP_GAME:
                print("Stopping the game.")
                return False
            print(
                f"Please enter {RETRY_DRAW}, {RECONNECT_ROBOT} "
                f"or {STOP_GAME}."
            )

    def reconnect_robot(self) -> bool:
        """Close the connection and open a fresh one. False on failure.

        The board coordinates are absolute, so the calibrated centres stay
        valid and the arm does not have to be re-taught.
        """
        try:
            self.robot.close()
        except Exception as error:
            print(f"Closing the old connection failed: {error}")

        try:
            self.robot = DobotController(DOBOT_PORT)
        except Exception as error:
            print(f"Reconnect failed: {error}")
            return False
        return True

    def robot_is_ready(self) -> bool:
        """True when the current controller still has a live connection."""
        return self.robot is not None and self.robot.connected

    def finish_game(self):
        winner = self.game.winner()
        print(f"Winner: {winner}" if winner else "Draw")
        if winner == self.game.robot:
            cells = self.game.winning_cells()
            if self.draw_winning_line(cells):
                print(f"Win line drawn through {cells}")
            else:
                print("Could not draw the winning line.")

    def close(self):
        try:
            self.vision.close()
        finally:
            self.robot.close()


def choose_size() -> int:
    while True:
        try:
            size = int(input(
                f"Board size ({MIN_BOARD_SIZE}-{MAX_BOARD_SIZE}): "
            ).strip())
            if size in BOARD_SIZES:
                return size
        except ValueError:
            pass
        print(
            f"Please enter an integer from "
            f"{MIN_BOARD_SIZE} to {MAX_BOARD_SIZE}."
        )


def choose_difficulty() -> str:
    prompt = f"Difficulty ({', '.join(DIFFICULTIES)}) [{DIFFICULTY_HARD}]: "
    while True:
        answer = input(prompt).strip().lower()
        if not answer:
            return DIFFICULTY_HARD
        if answer in DIFFICULTIES:
            return answer
        print(f"Please enter one of: {', '.join(DIFFICULTIES)}.")


def choose_first_player() -> bool:
    """Return True when the robot plays the first move."""
    prompt = f"First player ({', '.join(FIRST_PLAYERS)}) [human]: "
    while True:
        answer = input(prompt).strip().lower()
        if not answer:
            return False
        if answer in FIRST_PLAYERS:
            return answer == "robot"
        print(f"Please enter one of: {', '.join(FIRST_PLAYERS)}.")


def main():
    app = RobotGomoku(
        choose_size(),
        choose_difficulty(),
        choose_first_player(),
    )
    try:
        app.play()
    finally:
        app.close()


if __name__ == "__main__":
    main()
