"""Game orchestration."""

from .board import Board


class Game:
    """Own a board and alternate marks."""

    def __init__(self):
        self.board = Board()
        self.mark = "X"

    def play(self, position):
        """Apply one move and rotate the mark."""
        self.board.place(position, self.mark)
        self.mark = "O" if self.mark == "X" else "X"

    def is_over(self):
        """Return whether the game has ended."""
        return self.board.has_winner() or self.board.is_full()
