"""Tkinter GUI to validate the Gomoku AI without camera or robot.

Run:  python gomoku_ui_test.py

Each board square is one cell, matching the robot layout (the pen draws
inside a cell, not on an intersection). Cell (row, col) maps directly to
GomokuGame.board[row][col].

The board size selector uses the same depth strategy as gomoku_ai
(3x3 full search, 4x4 depth 7, 5x5 depth 5, 6x6/7x7 depth 4, 8x8/9x9 depth 3).
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

from gomoku_ai import EMPTY, GomokuAI, GomokuGame

CANVAS_SIZE = 560
MARGIN = 30
STONE_RADIUS_RATIO = 0.40

COLOR_BOARD = "#e8b96a"
COLOR_LINE = "#4a3213"
COLOR_HUMAN = "#1565c0"
COLOR_ROBOT = "#c62828"
COLOR_LAST = "#fdd835"
COLOR_WIN = "#2e7d32"


class GomokuApp:
    """Small board GUI used to test the search algorithm quickly."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Gomoku AI Test")
        self.game: GomokuGame | None = None
        self.ai = GomokuAI()
        self.history: list[tuple[str, tuple[int, int]]] = []
        self.last_move: tuple[int, int] | None = None
        self.busy = False
        self.cell_size = 1.0
        self._build_ui()
        self.new_game()

    # ========================================================
    # UI CONSTRUCTION
    # ========================================================
    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(toolbar, text="Board:").pack(side=tk.LEFT)
        self.size_box = ttk.Combobox(
            toolbar, width=3, state="readonly", values=(3, 4, 5, 6, 7, 8, 9, 10)
        )
        self.size_box.set(5)
        self.size_box.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Label(toolbar, text="First move:").pack(side=tk.LEFT)
        self.first_box = ttk.Combobox(
            toolbar, width=7, state="readonly", values=("human", "robot")
        )
        self.first_box.set("human")
        self.first_box.pack(side=tk.LEFT, padx=(4, 12))

        ttk.Button(toolbar, text="New Game", command=self.new_game).pack(
            side=tk.LEFT
        )
        ttk.Button(toolbar, text="Undo", command=self.undo).pack(
            side=tk.LEFT, padx=6
        )

        body = ttk.Frame(self.root)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            body,
            width=CANVAS_SIZE,
            height=CANVAS_SIZE,
            background=COLOR_BOARD,
            highlightthickness=0,
        )
        self.canvas.pack(side=tk.LEFT)
        self.canvas.bind("<Button-1>", self.on_click)

        side = ttk.Frame(body, padding=8)
        side.pack(side=tk.RIGHT, fill=tk.Y)

        self.status = ttk.Label(
            side, text="", wraplength=220, justify=tk.LEFT
        )
        self.status.pack(side=tk.TOP, anchor="w")

        ttk.Label(side, text="Log:").pack(side=tk.TOP, anchor="w", pady=(8, 2))
        self.log = tk.Text(side, width=34, height=24, state=tk.DISABLED)
        self.log.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # ========================================================
    # GAME LIFECYCLE
    # ========================================================
    def new_game(self) -> None:
        size = int(self.size_box.get())
        self.game = GomokuGame(size)
        self.history.clear()
        self.last_move = None
        self.busy = False
        self.cell_size = (CANVAS_SIZE - 2 * MARGIN) / size
        self.draw()
        self.write_log("-" * 30)
        self.write_log(
            f"New game {size}x{size} (win={self.game.win_length})"
        )
        if self.first_box.get() == "robot":
            self.set_status("Robot moves first...")
            self.busy = True
            self.root.after(200, self.robot_move)
        else:
            self.set_status("Your turn (X). Click a cell.")

    def undo(self) -> None:
        """Remove the robot reply and the human move that caused it."""
        if self.busy or not self.history:
            return
        player, (row, col) = self.history.pop()
        self.game.undo(row, col)
        if player == "robot" and self.history:
            player, (row, col) = self.history.pop()
            self.game.undo(row, col)
        self.last_move = None
        self.draw()
        self.set_status("Undo done. Your turn (X).")
        self.write_log("Undo one round")

    # ========================================================
    # RENDERING
    # ========================================================
    def _cell_center(self, row: int, col: int) -> tuple[float, float]:
        cell = self.cell_size
        return (
            MARGIN + (col + 0.5) * cell,
            MARGIN + (row + 0.5) * cell,
        )

    def draw(self) -> None:
        self.canvas.delete("all")
        assert self.game is not None
        size = self.game.size
        cell = self.cell_size
        span = MARGIN + size * cell

        # One square per cell, so there are size + 1 grid lines per axis.
        for index in range(size + 1):
            position = MARGIN + index * cell
            self.canvas.create_line(
                MARGIN, position, span, position, fill=COLOR_LINE
            )
            self.canvas.create_line(
                position, MARGIN, position, span, fill=COLOR_LINE
            )

        radius = cell * STONE_RADIUS_RATIO
        for row in range(size):
            for col in range(size):
                symbol = self.game.board[row][col]
                if symbol == EMPTY:
                    continue
                x, y = self._cell_center(row, col)
                color = (
                    COLOR_HUMAN
                    if symbol == self.game.human
                    else COLOR_ROBOT
                )
                self.canvas.create_oval(
                    x - radius, y - radius, x + radius, y + radius,
                    fill=color, outline=COLOR_LINE, width=2,
                )

        if self.last_move is not None:
            x, y = self._cell_center(*self.last_move)
            marker = radius * 0.35
            self.canvas.create_oval(
                x - marker, y - marker, x + marker, y + marker,
                outline=COLOR_LAST, width=2,
            )

        for row, col in self.game.winning_cells():
            x, y = self._cell_center(row, col)
            self.canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                outline=COLOR_WIN, width=4,
            )

    # ========================================================
    # INPUT AND TURNS
    # ========================================================
    def on_click(self, event: tk.Event) -> None:
        assert self.game is not None
        if self.busy or self.game.winner() or self.game.full():
            return
        col = int((event.x - MARGIN) / self.cell_size)
        row = int((event.y - MARGIN) / self.cell_size)
        if not self.game.is_empty(row, col):
            return
        self._apply_human(row, col)

    def _apply_human(self, row: int, col: int) -> None:
        assert self.game is not None
        if not self.game.place(row, col, self.game.human):
            return
        self.history.append(("human", (row, col)))
        self.last_move = (row, col)
        self.draw()
        self.write_log(f"Human  X -> ({row}, {col})")
        if self._finish_if_over():
            return
        self.busy = True
        self.set_status("Robot is thinking...")
        self.root.after(30, self.robot_move)

    def robot_move(self) -> None:
        assert self.game is not None
        started = time.perf_counter()
        move = self.ai.choose_move(self.game)
        elapsed = time.perf_counter() - started
        depth = self.ai.last_depth if self.ai.last_depth is not None else "-"

        if move is None:
            self.busy = False
            self._finish_if_over()
            return

        self.game.place(move[0], move[1], self.game.robot)
        self.history.append(("robot", move))
        self.last_move = move
        self.draw()
        self.write_log(
            f"Robot  O -> {move}  depth={depth}  {elapsed:.3f}s"
        )
        self.busy = False
        if self._finish_if_over():
            return
        self.set_status(f"Your turn (X). AI last think: {elapsed:.3f}s")

    def _finish_if_over(self) -> bool:
        assert self.game is not None
        winner = self.game.winner()
        if winner:
            name = "You (X)" if winner == self.game.human else "Robot (O)"
            self.draw()
            self.set_status(f"Winner: {name}")
            self.write_log(f"Winner: {name}")
            return True
        if self.game.full():
            self.set_status("Draw")
            self.write_log("Draw")
            return True
        return False

    # ========================================================
    # HELPERS
    # ========================================================
    def write_log(self, message: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def set_status(self, message: str) -> None:
        self.status.configure(text=message)


def main() -> None:
    root = tk.Tk()
    GomokuApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
