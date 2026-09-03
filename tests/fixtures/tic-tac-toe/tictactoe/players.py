"""Move selection."""

import random

from .board import Board


def choose_move(board: Board):
    """Choose one available cell."""
    return random.choice(board.empty_cells())
