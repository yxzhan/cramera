"""Which bodies the object overlay streams, rather than the baked scene bundle.

A demo object spawns, gets carried and disappears mid-run, so the overlay streams its pose
live; the scene around it is bundled once and loaded by the viewer. Baking an object into
that bundle is what makes the viewer reload the page on every pick and place: re-parenting
the body onto a gripper changes the bundle's signature, and a changed signature means a
new model to load.

There are two ways to be an overlay body:

- **The name looks like a mesh file.** The convention this repo's own demos follow --
  ``bowl.stl`` -- which needs no registration and survives a world fetched from another
  process, because the name travels with the body.
- **The name was registered here.** For worlds whose bodies are named by somebody else:
  upstream's ``coraplex_garmi_demo`` calls its objects ``bowl`` and ``spoon``, and a demo
  driving it cannot rename them -- ``World.get_body_by_name`` is memoized, so renaming a
  body mid-run leaves lookups resolving to the old name.

Registration is by name rather than by identity so that it can happen before the body
exists. Registering afterwards would have the bundle built once with the object in it and
then again without, which is the very reload this avoids.
"""

from __future__ import annotations

from typing_extensions import Optional, Set

from semantic_digital_twin.world_description.world_entity import Body

from cramera.mesh_format import MeshFormat

_REGISTERED_NAMES: Set[str] = set()


def mark_overlay_bodies(*names: str) -> None:
    """Stream the bodies with these names through the overlay, whatever they are called.

    :param names: Body names, without the world prefix -- ``"bowl"``, not ``"apartment/bowl"``.
    """
    _REGISTERED_NAMES.update(str(name) for name in names)


def registered_overlay_bodies() -> Set[str]:
    """The names registered so far."""
    return set(_REGISTERED_NAMES)


def overlay_name(body: Body) -> Optional[str]:
    """The name the overlay publishes ``body`` under, or ``None`` if it is scenery.

    :param body: The body to classify.
    """
    basename = str(body.name).split("/")[-1]
    if basename in _REGISTERED_NAMES or MeshFormat.of_path(basename) is not None:
        return basename
    return None


def is_overlay_body(body: Body) -> bool:
    """Whether the overlay renders ``body`` instead of the scene bundle.

    :param body: The body to classify.
    """
    return overlay_name(body) is not None
