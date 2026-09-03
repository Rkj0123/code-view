"""Small command-line game loop."""

from .game import Game
from .players import choose_move as pick_move


def run():
    """Play until the board is full or won."""
    game = Game()
    while not game.is_over():
        game.play(pick_move(game.board))
