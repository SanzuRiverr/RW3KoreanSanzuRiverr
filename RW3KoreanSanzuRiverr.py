"""Entry point for the game's mods.<folder>.<folder> loader."""
from .core.runtime import install

# Let ModHandler record failures in the game's mod error screen.
install()
