"""
The query parameters of the viewer's page.
"""

from __future__ import annotations

from enum import StrEnum

# %% parameters


class PageParameter(StrEnum):
    """
    The query parameters the viewer's page reads from its address.
    """

    SCENE = "scene"
    """
    The bundle the page opens.
    """

    LIVE = "live"
    """
    The address of a live publisher the page follows instead of a bundle.
    """

    LAYOUT = "layout"
    """
    A :class:`PageLayout` other than the full page.
    """

    PANEL = "panel"
    """
    The identity of the panel the page shows first.
    """

    FULLSCREEN = "fullscreen"
    """
    Present, with any value or none: the page takes the whole screen, as F11 does, at a
    person's first click or key press, since browsers allow it only then.
    """


class PageLayout(StrEnum):
    """
    How much of the viewer's page is shown.
    """

    SCENE = "scene"
    """
    The scene alone, as in a window it was popped out into.
    """


# %% appearance


class Theme(StrEnum):
    """
    The colour themes of the viewer's pages.
    """

    DARK = "dark"
    """
    Light text on dark surfaces; the default.
    """

    LIGHT = "light"
    """
    Dark text on light surfaces.
    """


class PageStorageKey(StrEnum):
    """
    What the viewer's pages remember in the browser's local storage.
    """

    THEME = "cramera.theme"
    """
    The :class:`Theme` a person chose.
    """

    SPLIT = "cramera.split"
    """
    The share of the scene page's width the side column takes, in percent, as a person
    dragged the divider.
    """

    QUERY_SPLIT = "cramera.query-split"
    """
    The share of the side column's height the query slot takes, in percent, as a person
    dragged the divider below it.
    """

    QUERY_FOLDED = "cramera.query-folded"
    """
    Whether a person folded the query slot to its heading.
    """
