"""Code present in the whole-repository graph but absent from entry focus."""


def debug_board(board, formatter_name):
    """Exercise an intentionally dynamic call boundary."""
    return getattr(board, formatter_name)()
