"""
The kinds of a plan's free objects a step can look for.
"""

from __future__ import annotations

from enum import StrEnum


class ObjectType(StrEnum):
    """
    The kinds of a plan's free objects a step can look for, each standing for the
    annotation its objects carry in the built world.
    """

    OBJECT = "object"
    """
    Any free object.
    """

    BLOCK = "block"
    """
    A small block taken from above, such as a cube.
    """
