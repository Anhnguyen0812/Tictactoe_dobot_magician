"""Board rules and lightweight Gomoku AI for board sizes 3..15."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterable, Optional

EMPTY = " "
DIRECTIONS = ((1, 0), (0, 1), (1, 1), (1, -1))

MIN_BOARD_SIZE = 3
MAX_BOARD_SIZE = 15
BOARD_SIZES = tuple(range(MIN_BOARD_SIZE, MAX_BOARD_SIZE + 1))

# Longest winning run the engine ever needs; wider windows are never scored.
MAX_WIN_LENGTH = 5


def win_length_for_size(size: int) -> int:
    """3x3 uses 3-in-a-row; 4x4 uses 4; every larger board uses 5."""
    if not MIN_BOARD_SIZE <= size <= MAX_BOARD_SIZE:
        raise ValueError(
            f"Board size must be between {MIN_BOARD_SIZE} and {MAX_BOARD_SIZE}"
        )
    return min(MAX_WIN_LENGTH, size)


# Difficulty levels. HARD falls back to the per-size depth cap and time budget
# below, which is the strongest configuration this engine reaches (~5 s per
# move on 10x10).
DIFFICULTY_EASY = "easy"
DIFFICULTY_MEDIUM = "medium"
DIFFICULTY_HARD = "hard"
DIFFICULTIES = (DIFFICULTY_EASY, DIFFICULTY_MEDIUM, DIFFICULTY_HARD)


@dataclass(frozen=True)
class DifficultyProfile:
    """Search effort of one difficulty level.

    A depth or time_limit of None falls back to the per-size defaults
    SEARCH_DEPTH_BY_SIZE and TIME_LIMIT_BY_SIZE.
    """

    depth: Optional[int]
    time_limit: Optional[float]


DIFFICULTY_PROFILES = {
    # Two plies: one robot move plus the single best human reply.
    DIFFICULTY_EASY: DifficultyProfile(depth=2, time_limit=0.5),
    # Four plies: short forcing sequences are visible.
    DIFFICULTY_MEDIUM: DifficultyProfile(depth=4, time_limit=2.0),
    # Per-size defaults: 5 plies and ~5 s per move on the large boards.
    DIFFICULTY_HARD: DifficultyProfile(depth=None, time_limit=None),
}


@dataclass
class GomokuGame:
    size: int
    human: str = "X"
    robot: str = "O"

    def __post_init__(self) -> None:
        self.win_length = win_length_for_size(self.size)
        self.board = [[EMPTY for _ in range(self.size)] for _ in range(self.size)]

    def inside(self, row: int, col: int) -> bool:
        return 0 <= row < self.size and 0 <= col < self.size

    def is_empty(self, row: int, col: int) -> bool:
        return self.inside(row, col) and self.board[row][col] == EMPTY

    def place(self, row: int, col: int, symbol: str) -> bool:
        if not self.is_empty(row, col):
            return False
        self.board[row][col] = symbol
        return True

    def undo(self, row: int, col: int) -> None:
        self.board[row][col] = EMPTY

    def available_moves(self) -> list[tuple[int, int]]:
        return [
            (r, c)
            for r in range(self.size)
            for c in range(self.size)
            if self.board[r][c] == EMPTY
        ]

    def winning_cells(self) -> list[tuple[int, int]]:
        """Return the cells of the first winning run, or an empty list."""
        k = self.win_length
        for r in range(self.size):
            for c in range(self.size):
                symbol = self.board[r][c]
                if symbol == EMPTY:
                    continue
                for dr, dc in DIRECTIONS:
                    end_r = r + (k - 1) * dr
                    end_c = c + (k - 1) * dc
                    if not self.inside(end_r, end_c):
                        continue
                    cells = [(r + i * dr, c + i * dc) for i in range(k)]
                    if all(self.board[cr][cc] == symbol for cr, cc in cells):
                        return cells
        return []

    def winner(self) -> Optional[str]:
        cells = self.winning_cells()
        if not cells:
            return None
        row, col = cells[0]
        return self.board[row][col]

    def full(self) -> bool:
        return not any(EMPTY in row for row in self.board)

    def print_board(self) -> None:
        width = len(str(self.size * self.size))
        for r, row in enumerate(self.board):
            cells = []
            for c, value in enumerate(row):
                label = value if value != EMPTY else str(r * self.size + c + 1)
                cells.append(f"{label:>{width}}")
            print(" | ".join(cells))
            if r < self.size - 1:
                print("-+-".join("-" * width for _ in range(self.size)))


class GomokuAI:
    """Gomoku AI: alpha-beta minimax over a window-pattern evaluator.

    Improvements over the original run-counter:
    - sliding-window pattern scoring, so broken shapes such as
      "_ X X _ X _" and open threats are valued correctly;
    - incremental evaluation (only the lines through a placed stone are
      rescored), which makes leaf evaluation O(1);
    - Zobrist hashing plus a transposition table to reuse searched nodes;
    - iterative deepening bounded by a per-move time budget;
    - easy/medium/hard profiles that cap the search depth and the time budget.
    """

    WIN_SCORE = 100_000_000
    INF = float("inf")

    # Score of a win_length window that holds a single symbol and no opponent
    # stone. The index is how many window cells are that symbol. Because every
    # window of a line is summed, open threats (covered by many windows)
    # outscore blocked ones (covered by few).
    WINDOW_SCORE = (0, 1, 20, 400, 8_000, WIN_SCORE, WIN_SCORE)

    # Iterative-deepening cap per board size; 3x3 searches the whole tree.
    # The depth actually reached depends on the per-size time budget, so the
    # cap is only a safety bound: raising it never costs quality, but a cap
    # that is too low stops the search while budget is still left.
    SEARCH_DEPTH_BY_SIZE = {
        4: 7,
        5: 6, 6: 6, 7: 6, 8: 6, 9: 6,
        10: 6, 11: 6, 12: 6, 13: 6, 14: 6, 15: 6,
    }

    # Per-move thinking budget in seconds. A wider board costs more per node,
    # so boards from LARGE_BOARD_SIZE up need a larger budget to reach the
    # depth the smaller boards reach within DEFAULT_TIME_LIMIT.
    LARGE_BOARD_SIZE = 10
    LARGE_BOARD_TIME_LIMIT = 5.0
    DEFAULT_TIME_LIMIT = 1.5

    # The deadline is sampled every N nodes. A node costs ~2 ms, so sampling
    # every 1024 nodes let a move run up to ~2 s past its budget.
    DEADLINE_CHECK_INTERVAL_NODES = 64

    # Transposition-table bound flags.
    EXACT, LOWER, UPPER = 0, 1, 2

    def __init__(
        self,
        robot: str = "O",
        human: str = "X",
        difficulty: str = DIFFICULTY_HARD,
        max_depth: Optional[int] = None,
        time_limit: Optional[float] = None,
    ) -> None:
        profile = DIFFICULTY_PROFILES.get(difficulty)
        if profile is None:
            raise ValueError(
                f"Unknown difficulty {difficulty!r}; "
                f"expected one of {', '.join(DIFFICULTIES)}"
            )

        self.robot = robot
        self.human = human
        self.difficulty = difficulty
        # An explicit argument wins, then the difficulty profile, then the
        # per-size defaults resolved in _max_depth/_time_limit.
        self.max_depth = (
            max_depth if max_depth is not None else profile.depth
        )
        self.time_limit = (
            time_limit if time_limit is not None else profile.time_limit
        )
        self.last_depth: Optional[int] = None

        # Zobrist keys and line structures, rebuilt when the size changes.
        self._size: Optional[int] = None
        self._win: Optional[int] = None
        self._board: list[list[str]] = []
        self._zobrist_robot: list[list[int]] = []
        self._zobrist_human: list[list[int]] = []
        self._zobrist_side = 0
        self._lines: list[list[tuple[int, int]]] = []
        self._cell_refs: dict[
            tuple[int, int], list[tuple[int, int]]
        ] = {}
        self._line_score: dict[str, list[float]] = {}
        self._totals: dict[str, float] = {}
        self._hash = 0

        # Search bookkeeping.
        self._transposition: dict[
            int, tuple[int, float, int, Optional[tuple[int, int]]]
        ] = {}
        self._deadline = 0.0
        self._nodes = 0
        self._timed_out = False
        self._root_score = 0.0

    def choose_move(
        self,
        game: GomokuGame,
    ) -> Optional[tuple[int, int]]:
        """Return the best move for the robot.

        Iterative deepening runs depth 1, 2, 3, ... until the time budget or
        the size cap is reached, keeping the best move from the last fully
        completed depth.
        """

        moves = game.available_moves()
        if not moves:
            return None

        self._ensure_state(game)
        self._nodes = 0
        self._timed_out = False

        # Empty board: choose the center.
        if len(moves) == game.size * game.size:
            center = game.size // 2
            self.last_depth = self._max_depth(game)
            return center, center

        candidates = self._candidate_moves(game, radius=2)

        # 1. Win immediately if possible.
        winning_move = self._find_winning_move(game, candidates, self.robot)
        if winning_move is not None:
            self.last_depth = 1
            return winning_move

        # 2. Block the human's immediate win.
        blocking_move = self._find_winning_move(game, candidates, self.human)
        if blocking_move is not None:
            self.last_depth = 1
            return blocking_move

        # Keep the transposition table from growing without bound.
        if len(self._transposition) > 300_000:
            self._transposition.clear()

        self._deadline = time.monotonic() + self._time_limit(game)
        self.last_depth = 0
        best_move = candidates[0]

        for depth in range(1, self._max_depth(game) + 1):
            move = self._root_search(game, depth)
            if self._timed_out:
                break
            best_move = move
            self.last_depth = depth
            # Stop early once a forced win or loss is proven.
            if abs(self._root_score) >= self.WIN_SCORE:
                break

        return best_move

    def _time_limit(self, game: GomokuGame) -> float:
        """Return the per-move thinking budget for this board size."""

        if self.time_limit is not None:
            return self.time_limit

        if game.size >= self.LARGE_BOARD_SIZE:
            return self.LARGE_BOARD_TIME_LIMIT

        return self.DEFAULT_TIME_LIMIT

    def _root_search(
        self,
        game: GomokuGame,
        depth: int,
    ) -> tuple[int, int]:
        """Run one iterative-deepening pass over every root move."""

        candidates = self._candidate_moves(game, radius=2)
        candidates = self._force_moves(game, candidates, self.robot)

        key = self._hash ^ self._zobrist_side
        entry = self._transposition.get(key)
        tt_move = entry[3] if entry is not None else None

        ordered = self._ordered_moves(game, candidates, self.robot, tt_move)
        best_move = ordered[0]
        best_score = -self.INF
        alpha, beta = -self.INF, self.INF

        for row, col in ordered:
            self._set(row, col, self.robot)
            score = self._search(
                game, depth - 1, alpha, beta, False, (row, col), self.robot
            )
            self._clear(row, col, self.robot)

            if self._timed_out:
                break

            if score > best_score:
                best_score, best_move = score, (row, col)

            alpha = max(alpha, best_score)

        self._root_score = best_score
        if not self._timed_out and best_score > -self.INF:
            flag = self.EXACT if best_score < beta else self.LOWER
            self._transposition[key] = (depth, best_score, flag, best_move)

        return best_move

    def _search(
        self,
        game: GomokuGame,
        depth: int,
        alpha: float,
        beta: float,
        maximizing: bool,
        last_move: tuple[int, int],
        last_symbol: str,
    ) -> float:
        """Alpha-beta minimax over the incremental evaluation state."""

        self._nodes += 1
        if (
            (self._nodes % self.DEADLINE_CHECK_INTERVAL_NODES) == 0
            and time.monotonic() >= self._deadline
        ):
            self._timed_out = True

        if self._timed_out:
            return self._evaluate_board()

        # Only the most recent stone can create a new win.
        if self._is_winning_position(game, last_move, last_symbol):
            if last_symbol == self.robot:
                return float(self.WIN_SCORE)
            return -float(self.WIN_SCORE)

        if game.full():
            return 0.0

        if depth <= 0:
            return self._evaluate_board()

        key = self._hash ^ (self._zobrist_side if maximizing else 0)
        entry = self._transposition.get(key)
        tt_move = None
        if entry is not None:
            entry_depth, entry_value, entry_flag, tt_move = entry
            if entry_depth >= depth:
                if entry_flag == self.EXACT:
                    return entry_value
                if entry_flag == self.LOWER and entry_value >= beta:
                    return entry_value
                if entry_flag == self.UPPER and entry_value <= alpha:
                    return entry_value

        alpha_original = alpha
        current_symbol = self.robot if maximizing else self.human
        candidates = self._candidate_moves(game, radius=2)
        candidates = self._force_moves(game, candidates, current_symbol)
        ordered = self._ordered_moves(
            game, candidates, current_symbol, tt_move
        )

        best_move = ordered[0]

        if maximizing:
            value = -self.INF
            for row, col in ordered:
                self._set(row, col, self.robot)
                score = self._search(
                    game, depth - 1, alpha, beta, False, (row, col),
                    self.robot,
                )
                self._clear(row, col, self.robot)
                if score > value:
                    value, best_move = score, (row, col)
                alpha = max(alpha, value)
                if alpha >= beta or self._timed_out:
                    break
        else:
            value = self.INF
            for row, col in ordered:
                self._set(row, col, self.human)
                score = self._search(
                    game, depth - 1, alpha, beta, True, (row, col),
                    self.human,
                )
                self._clear(row, col, self.human)
                if score < value:
                    value, best_move = score, (row, col)
                beta = min(beta, value)
                if alpha >= beta or self._timed_out:
                    break

        if not self._timed_out:
            if value <= alpha_original:
                flag = self.UPPER
            elif value >= beta:
                flag = self.LOWER
            else:
                flag = self.EXACT
            self._transposition[key] = (depth, value, flag, best_move)

        return value

    def _force_moves(
        self,
        game: GomokuGame,
        candidates: list[tuple[int, int]],
        symbol: str,
    ) -> list[tuple[int, int]]:
        """Narrow to forced moves when a win or a required block exists."""

        winning = [move for move in candidates if self._wins(game, move, symbol)]
        if winning:
            return winning

        opponent = self._opponent(symbol)
        blocks = [move for move in candidates if self._wins(game, move, opponent)]
        if blocks:
            return blocks

        return candidates

    def _evaluate_board(self) -> float:
        """Return the score from the robot's perspective in O(1)."""

        return self._totals[self.robot] - self._totals[self.human] * 1.1

    # ========================================================
    # INCREMENTAL STATE: ZOBRIST KEYS AND LINE SCORES
    # ========================================================
    def _ensure_state(self, game: GomokuGame) -> None:
        """Rebuild tables on size change and resync scores with the board.

        External moves (placed by the GUI or the robot loop) are not tracked,
        so the state is rebuilt from the live board at the start of each
        search.
        """
        if self._size != game.size or self._win != game.win_length:
            self._size = game.size
            self._win = game.win_length
            self._build_tables()
            self._transposition.clear()

        self._board = game.board
        self._rebuild()

    def _build_tables(self) -> None:
        """Create Zobrist keys and the list of scoreable lines for the size."""

        size = self._size
        rng = random.Random(0x5D0B07 + size * 1_000_003 + self._win)
        self._zobrist_robot = [
            [rng.getrandbits(64) for _ in range(size)] for _ in range(size)
        ]
        self._zobrist_human = [
            [rng.getrandbits(64) for _ in range(size)] for _ in range(size)
        ]
        self._zobrist_side = rng.getrandbits(64)
        self._build_lines()
        self._line_score = {
            self.robot: [0.0] * len(self._lines),
            self.human: [0.0] * len(self._lines),
        }
        self._totals = {self.robot: 0.0, self.human: 0.0}

    def _build_lines(self) -> None:
        """Collect every row, column and diagonal long enough to hold a win."""

        size = self._size
        win = self._win
        lines: list[list[tuple[int, int]]] = []

        for row in range(size):
            lines.append([(row, col) for col in range(size)])
        for col in range(size):
            lines.append([(row, col) for row in range(size)])

        # Diagonals going down-right.
        for start in range(-(size - 1), size):
            row = 0 if start >= 0 else -start
            col = start if start >= 0 else 0
            line = []
            while row < size and col < size:
                line.append((row, col))
                row += 1
                col += 1
            if len(line) >= win:
                lines.append(line)

        # Diagonals going down-left.
        for start in range(2 * size - 1):
            row = 0 if start < size else start - size + 1
            col = start if start < size else size - 1
            line = []
            while row < size and col >= 0:
                line.append((row, col))
                row += 1
                col -= 1
            if len(line) >= win:
                lines.append(line)

        self._lines = lines
        self._cell_refs = {}
        for index, line in enumerate(lines):
            for position, cell in enumerate(line):
                self._cell_refs.setdefault(cell, []).append((index, position))

    def _line_value(
        self,
        line: list[tuple[int, int]],
        symbol: str,
    ) -> float:
        """Sum the window scores of one line for one symbol."""

        win = self._win
        board = self._board
        score = 0.0
        for start in range(len(line) - win + 1):
            stones = 0
            blocked = False
            for offset in range(win):
                row, col = line[start + offset]
                value = board[row][col]
                if value == symbol:
                    stones += 1
                elif value != EMPTY:
                    blocked = True
                    break
            if not blocked:
                score += self.WINDOW_SCORE[stones]
        return score

    def _rebuild(self) -> None:
        """Recompute the hash and every line score from the live board."""

        size = self._size
        self._hash = 0
        for row in range(size):
            for col in range(size):
                value = self._board[row][col]
                if value == self.robot:
                    self._hash ^= self._zobrist_robot[row][col]
                elif value == self.human:
                    self._hash ^= self._zobrist_human[row][col]

        for symbol in (self.robot, self.human):
            total = 0.0
            for index, line in enumerate(self._lines):
                value = self._line_value(line, symbol)
                self._line_score[symbol][index] = value
                total += value
            self._totals[symbol] = total

    def _set(self, row: int, col: int, symbol: str) -> None:
        """Place a stone and update the hash and affected line scores."""

        self._board[row][col] = symbol
        if symbol == self.robot:
            self._hash ^= self._zobrist_robot[row][col]
        else:
            self._hash ^= self._zobrist_human[row][col]
        self._refresh(row, col)

    def _clear(self, row: int, col: int, symbol: str) -> None:
        """Remove a stone and update the hash and affected line scores."""

        self._board[row][col] = EMPTY
        if symbol == self.robot:
            self._hash ^= self._zobrist_robot[row][col]
        else:
            self._hash ^= self._zobrist_human[row][col]
        self._refresh(row, col)

    def _refresh(self, row: int, col: int) -> None:
        """Rescore only the lines that pass through one cell."""

        for index, _ in self._cell_refs.get((row, col), ()):
            line = self._lines[index]
            for symbol in (self.robot, self.human):
                value = self._line_value(line, symbol)
                self._totals[symbol] += value - self._line_score[symbol][index]
                self._line_score[symbol][index] = value

    def _ordered_moves(
        self,
        game: GomokuGame,
        moves: Iterable[tuple[int, int]],
        symbol: str,
        tt_move: Optional[tuple[int, int]] = None,
    ) -> list[tuple[int, int]]:
        """Sort promising moves first; the transposition move goes first."""

        opponent = self._opponent(symbol)
        center = (game.size - 1) / 2
        scored_moves = []

        for move in moves:
            if move == tt_move:
                scored_moves.append((self.INF, move))
                continue

            attack_score = self._cell_score(move, symbol)
            defense_score = self._cell_score(move, opponent)
            center_score = -(abs(move[0] - center) + abs(move[1] - center))
            scored_moves.append(
                (attack_score + defense_score * 1.1 + center_score, move)
            )

        scored_moves.sort(key=lambda item: item[0], reverse=True)

        # Limiting the number of branches is important on large boards.
        branch_limit = self._branch_limit(game)
        return [move for _, move in scored_moves[:branch_limit]]

    def _cell_score(
        self,
        move: tuple[int, int],
        symbol: str,
    ) -> float:
        """Local window score for placing symbol at move.

        Only the windows that contain the cell are scored, and the board is
        left untouched, so this stays cheap and side-effect free.
        """

        row, col = move
        board = self._board
        if board[row][col] != EMPTY:
            return -self.INF

        win = self._win
        score = 0.0
        for index, position in self._cell_refs.get((row, col), ()):
            line = self._lines[index]
            low = max(0, position - win + 1)
            high = min(position, len(line) - win)
            for start in range(low, high + 1):
                stones = 0
                blocked = False
                for offset in range(start, start + win):
                    cell_row, cell_col = line[offset]
                    if cell_row == row and cell_col == col:
                        stones += 1
                        continue
                    value = board[cell_row][cell_col]
                    if value == symbol:
                        stones += 1
                    elif value != EMPTY:
                        blocked = True
                        break
                if not blocked:
                    score += self.WINDOW_SCORE[stones]
        return score

    def _find_winning_move(
        self,
        game: GomokuGame,
        moves: Iterable[tuple[int, int]],
        symbol: str,
    ) -> Optional[tuple[int, int]]:
        for move in moves:
            if self._wins(game, move, symbol):
                return move

        return None

    def _wins(
        self,
        game: GomokuGame,
        move: tuple[int, int],
        symbol: str,
    ) -> bool:
        row, col = move

        if not game.is_empty(row, col):
            return False

        # The cell itself is not read: _is_winning_position already counts the
        # candidate stone, so mutating the board here would only cost time.
        return self._is_winning_position(game, move, symbol)

    def _is_winning_position(
        self,
        game: GomokuGame,
        move: tuple[int, int],
        symbol: str,
    ) -> bool:
        """Check only the four lines passing through move."""

        row, col = move

        for dr, dc in DIRECTIONS:
            count = 1

            count += self._count(
                game,
                row,
                col,
                dr,
                dc,
                symbol,
            )
            count += self._count(
                game,
                row,
                col,
                -dr,
                -dc,
                symbol,
            )

            if count >= game.win_length:
                return True

        return False

    @staticmethod
    def _count(
        game: GomokuGame,
        row: int,
        col: int,
        dr: int,
        dc: int,
        symbol: str,
    ) -> int:
        count = 0
        current_row = row + dr
        current_col = col + dc

        while (
            game.inside(current_row, current_col)
            and game.board[current_row][current_col]
            == symbol
        ):
            count += 1
            current_row += dr
            current_col += dc

        return count

    @staticmethod
    def _candidate_moves(
        game: GomokuGame,
        radius: int,
    ) -> list[tuple[int, int]]:
        """Return empty cells near existing stones."""

        occupied = [
            (row, col)
            for row in range(game.size)
            for col in range(game.size)
            if game.board[row][col] != EMPTY
        ]

        if not occupied:
            center = game.size // 2
            return [(center, center)]

        result = set()

        for row, col in occupied:
            for row_offset in range(
                -radius,
                radius + 1,
            ):
                for col_offset in range(
                    -radius,
                    radius + 1,
                ):
                    candidate_row = row + row_offset
                    candidate_col = col + col_offset

                    if game.is_empty(
                        candidate_row,
                        candidate_col,
                    ):
                        result.add(
                            (candidate_row, candidate_col)
                        )

        return list(result) or game.available_moves()

    def _max_depth(self, game: GomokuGame) -> int:
        """Return the iterative-deepening cap for this board size."""

        if self.max_depth is not None:
            return self.max_depth

        # Full search guarantees optimal play on 3x3.
        if game.size == 3:
            return game.size * game.size

        return self.SEARCH_DEPTH_BY_SIZE.get(game.size, 4)

    @staticmethod
    def _branch_limit(game: GomokuGame) -> int:
        if game.size <= 3:
            return game.size * game.size

        if game.size <= 5:
            return 14

        if game.size <= 7:
            return 12

        return 10

    def _opponent(self, symbol: str) -> str:
        if symbol == self.robot:
            return self.human

        return self.robot
