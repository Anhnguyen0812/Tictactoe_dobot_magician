"""Variable-size 3x3..9x9 robot Gomoku game."""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

from gomoku_ai import GomokuAI, GomokuGame
from robot_controller import DobotController

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
    def __init__(self, size: int):
        self.game = GomokuGame(size)
        self.ai = GomokuAI()
        self.vision = BoardVision(size)
        self.robot = DobotController(DOBOT_PORT)
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

    def draw_o(self, row: int, col: int) -> bool:
        cx, cy = self.centers[row][col]
        # Save the robot pose before drawing.
        pose_before = self.robot.pose()

        # Radius adapts to the physical spacing between neighboring centers.
        distances = []
        if col + 1 < self.game.size:
            nx, ny = self.centers[row][col + 1]
            distances.append(((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5)
        if row + 1 < self.game.size:
            nx, ny = self.centers[row + 1][col]
            distances.append(((nx - cx) ** 2 + (ny - cy) ** 2) ** 0.5)
        radius = min(distances or [12.0]) * CIRCLE_RADIUS_RATIO
        r_head = self.robot.pose()[3]
        start_x = cx + radius
        hover = self.z_draw + Z_HOVER
        try:
            self.robot.move(start_x, cy, hover, r_head)
            self.robot.move(start_x, cy, self.z_draw, r_head)
            self.robot.circle_three_arcs(cx, cy, self.z_draw, radius, r_head)
            self.robot.move(start_x, cy, hover, r_head)
            self.robot.move(cx, cy, hover, r_head)
            # Restore the robot pose after drawing.
            self.robot.move(*pose_before)
            return True
        except Exception as error:
            print(f"Draw failed: {error}")
            try:
                self.robot.recover()
            except Exception as recovery_error:
                print(f"Recovery failed: {recovery_error}")
            return False

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
        except Exception as error:
            print(f"Win line failed: {error}")
            try:
                self.robot.recover()
            except Exception as recovery_error:
                print(f"Recovery failed: {recovery_error}")
            return False

    def play(self):
        self.vision.calibrate()
        self.calibrate_robot()
        input("Calibration finished. Press ENTER to start...")
        self.game.print_board()
        while True:
            human_move = self.vision.wait_human(
                self.game, self.history, self.last_think)
            if human_move is None:
                return
            if not self.game.place(*human_move, self.game.human):
                continue
            self.history.append(("human", human_move))
            print(f"Human: {human_move}")
            self.game.print_board()
            if self.game.winner() or self.game.full():
                break

            started = time.perf_counter()
            move = self.ai.choose_move(self.game)
            self.last_think = time.perf_counter() - started
            if move is None:
                break
            print(f"[AI] move={move} depth={self.ai.last_depth} "
                  f"think={self.last_think:.3f}s")
            if not self.draw_o(*move):
                print("Game paused because the robot could not draw safely.")
                return
            self.game.place(*move, self.game.robot)
            self.history.append(("robot", move))
            print(f"Robot: {move}")
            self.game.print_board()
            if self.game.winner() or self.game.full():
                break

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
            size = int(input("Board size (3-10): ").strip())
            if 3 <= size <= 10:
                return size
        except ValueError:
            pass
        print("Please enter an integer from 3 to 10.")


def main():
    app = RobotGomoku(choose_size())
    try:
        app.play()
    finally:
        app.close()


if __name__ == "__main__":
    main()
