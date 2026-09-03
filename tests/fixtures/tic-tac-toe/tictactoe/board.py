"""Board state and winner checks."""

WIN_LINES = (
    (0, 1, 2),
    (3, 4, 5),
    (6, 7, 8),
    (0, 3, 6),
    (1, 4, 7),
    (2, 5, 8),
    (0, 4, 8),
    (2, 4, 6),
)


class Board:
    """Mutable board used by one game."""

    def __init__(self):
        self.cells = [""] * 9

    def place(self, position, mark):
        """Place a mark in an empty cell."""
        if self.cells[position]:
            raise ValueError("cell is occupied")
        self.cells[position] = mark

    def empty_cells(self):
        """Return the indexes still available."""
        return [index for index, value in enumerate(self.cells) if not value]

    def has_winner(self):
        """Return whether any line contains one repeated mark."""
        return any(
            self.cells[a] and self.cells[a] == self.cells[b] == self.cells[c]
            for a, b, c in WIN_LINES
        )

    def is_full(self):
        """Return whether no move remains."""
        return all(self.cells)
