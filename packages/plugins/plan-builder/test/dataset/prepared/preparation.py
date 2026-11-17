"""
A stand-in of what prepares every demo: it notes in the environment that it ran.
"""

import os

PREPARED_VARIABLE = "PREPARED_DEMO_STAND_IN"
"""
The environment variable the preparation sets.
"""


def prepare() -> None:
    """
    Note that the preparation ran, for the demo run afterwards to print.
    """
    os.environ[PREPARED_VARIABLE] = "prepared"
