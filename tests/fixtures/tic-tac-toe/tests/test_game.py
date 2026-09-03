from tictactoe.game import Game


def test_new_game_is_not_over():
    game = Game()
    assert not game.is_over()
