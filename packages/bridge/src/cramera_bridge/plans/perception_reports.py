"""
Perception reports: what a step's perception query asked, every object the perception
reported, which one the step took and why it left the others, and the outcome in one
sentence a person reads beside the step.

The verdicts follow the rules a run applies: coraplex's RoboKudo client drops an object
of another color or type and one outside the box looked in, the detection takes the
first object left, and the step stops before moving when that object lies farther from
where the step expected it than the step allows. A step expects its objects where the plan
was validated with them, or, once it was planned again, where they were last seen.

What the perception checks, the type it names, is kept apart from what a person reads,
the object as the plan names it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from typing_extensions import List, Optional

from cramera_protocol.geometry import Bounds, Vector3
from cramera_protocol.plan_builder import (
    PerceptionReport,
    ReportedObject,
    ReportedObjectVerdict,
    SurfaceDepth,
)

CENTIMETRES = 100.0
"""
Centimetres in a metre.
"""

MILLIMETRES = 1000.0
"""
Millimetres in a metre.
"""

VALIDATED_SPOT = "where the plan was validated"
"""
Where a step expects its objects until it is planned again, as a person reads it.
"""

LAST_SEEN_SPOT = "where it was last seen"
"""
Where a step planned again expects its objects, as a person reads it.
"""

# %% answers


@dataclass(frozen=True)
class ReportedDesignator:
    """
    One object a perception reported, as it reported it.
    """

    position: Vector3
    """
    Where it was seen, in metres in the world's root.
    """

    size: Optional[Vector3] = None
    """
    The extent of the box around it, None when none was measured.
    """

    colors: List[str] = field(default_factory=list)
    """
    The colors it was seen in, most dominant first.
    """

    type: str = ""
    """
    The type it was reported as, empty when the perception did not classify it.
    """

    inside_surface: Optional[SurfaceDepth] = None
    """
    How far the plan's object looked for would lie inside the surface it stands on,
    put where this object was seen; None when inside none, or when the query looks for
    none of the plan's objects.
    """


@dataclass(frozen=True)
class PerceptionAnswer:
    """
    What a perception answered to one query.
    """

    objects: Optional[List[ReportedDesignator]]
    """
    Every object reported, in the order reported; None when the query went unanswered.
    """


# %% searches


def centimetres(metres: float) -> str:
    """
    :param metres: A distance.
    :return: It in whole centimetres, or in half ones below ten, as a person reads it.
    """
    value = metres * CENTIMETRES
    if value < 10 and round(value * 2) % 2 == 1:
        return f"{round(value * 2) / 2:g} cm"
    return f"{round(value)} cm"


def sentence(text: str) -> str:
    """
    :param text: A sentence.
    :return: It starting with a capital letter.
    """
    return text[:1].upper() + text[1:]


def object_name(identifier: str) -> str:
    """
    :param identifier: The identity of one of the plan's objects, such as ``red_cube``.
    :return: It as a person names it, as the step cards do: "red cube".
    """
    return identifier.replace("_", " ")


def millimetres(metres: float) -> str:
    """
    :param metres: A distance.
    :return: It in whole millimetres, at least one, as a person reads it.
    """
    return f"{max(1, round(metres * MILLIMETRES))} mm"


@dataclass(frozen=True)
class PerceptionSearch:
    """
    What a step's perception query asks: an object of a type, maybe of a color, inside a
    box, and where the step expects it.
    """

    type: str
    """
    The type asked for, as the perception names it; what an object reported is checked
    against, never shown to a person.
    """

    box: Bounds
    """
    The box looked in, in metres in the world's root.
    """

    target: Optional[str] = None
    """
    The identity of the plan's object looked for, None when the query names none.
    """

    color: Optional[str] = None
    """
    The color asked for, None for any.
    """

    table_wide: bool = False
    """
    Whether the box is the whole table rather than one around where the plan was
    validated.
    """

    grounded: Optional[Vector3] = None
    """
    Where the step expects the object just before the query, None when it expects it
    nowhere in particular.
    """

    validated: Optional[Vector3] = None
    """
    Where the plan was validated with the object, when the step was planned again since
    with the object expected elsewhere, at :attr:`grounded`; None otherwise.
    """

    tolerance: Optional[float] = None
    """
    How far, in metres, from :attr:`grounded` the object may lie for the step to go on.
    """

    def report(self, answer: PerceptionAnswer) -> PerceptionReport:
        """
        :param answer: What the perception answered.
        :return: The report of the query with every object judged.
        """
        objects = [] if answer.objects is None else self.judged(answer.objects)
        return PerceptionReport(
            target=self.target,
            looked_for=self.looked_for(),
            type=self.type,
            color=self.color,
            box=self.box,
            table_wide=self.table_wide,
            grounded=self.grounded,
            validated=self.validated,
            tolerance=self.tolerance,
            answered=answer.objects is not None,
            objects=objects,
            summary=self.summary(answer.objects is not None, objects),
        )

    def judged(self, designators: List[ReportedDesignator]) -> List[ReportedObject]:
        """
        :param designators: The objects reported, in order.
        :return: Each with whether the step took it and why not.
        """
        judged = []
        taken = False
        for designator in designators:
            verdict = self.rejection(designator)
            if verdict is None and taken:
                verdict = ReportedObjectVerdict.NOT_CHOSEN
            if verdict is None:
                taken = True
                verdict = (
                    ReportedObjectVerdict.TOO_FAR
                    if self.is_too_far(designator.position)
                    else ReportedObjectVerdict.ACCEPTED
                )
            judged.append(
                ReportedObject(
                    position=designator.position,
                    size=designator.size,
                    colors=list(designator.colors),
                    type=designator.type,
                    verdict=verdict,
                    distance=self.distance(designator.position),
                    inside_surface=designator.inside_surface,
                )
            )
        return judged

    def rejection(
        self, designator: ReportedDesignator
    ) -> Optional[ReportedObjectVerdict]:
        """
        :param designator: An object reported.
        :return: Why the step cannot take it, None when it could.
        """
        if (
            self.color is not None
            and designator.colors
            and self.color not in designator.colors
        ):
            return ReportedObjectVerdict.WRONG_COLOR
        if designator.type and designator.type.strip().lower() != self.type.lower():
            return ReportedObjectVerdict.WRONG_TYPE
        if not self.contains(designator.position):
            return ReportedObjectVerdict.OUTSIDE_BOX
        return None

    def contains(self, position: Vector3) -> bool:
        """
        :param position: A position in the world's root.
        :return: Whether it lies inside the box looked in.
        """
        low, high = self.box.minimum, self.box.maximum
        return (
            low.x <= position.x <= high.x
            and low.y <= position.y <= high.y
            and low.z <= position.z <= high.z
        )

    def distance(self, position: Vector3) -> Optional[float]:
        """
        :param position: A position in the world's root.
        :return: How far it lies from where the step expects the object, horizontally,
            as the step measures it on the table; None when it expects no position.
        """
        if self.grounded is None:
            return None
        return math.hypot(position.x - self.grounded.x, position.y - self.grounded.y)

    def is_too_far(self, position: Vector3) -> bool:
        """
        :param position: Where the object taken was seen.
        :return: Whether it lies farther from where the step expects the object than
            the step allows.
        """
        distance = self.distance(position)
        return (
            distance is not None
            and self.tolerance is not None
            and distance > self.tolerance
        )

    # %% the sentence

    def looked_for(self) -> str:
        """
        :return: The object looked for as a person names it: "the red cube" for the
            plan's red_cube, else by its color.
        """
        if self.target:
            return f"the {object_name(self.target)}"
        return self.kind()

    def kind(self) -> str:
        """
        :return: "a red object", or "an object" for any color.
        """
        return f"a {self.color} object" if self.color else "an object"

    def where(self) -> str:
        """
        :return: Where the query looked, as a person reads it.
        """
        if self.table_wide:
            return "on the table"
        if self.grounded is None:
            return "in the box looked in"
        reach = (self.box.maximum.x - self.box.minimum.x) / 2
        return f"within {centimetres(reach)} of {self.spot()}"

    def spot(self) -> str:
        """
        :return: Where the step expects the object, which distances are measured from,
            as a person reads it.
        """
        return VALIDATED_SPOT if self.validated is None else LAST_SEEN_SPOT

    def away(self, position: Vector3, distance: float) -> str:
        """
        :param position: Where an object was seen.
        :param distance: How far it lies from where the step expects the object.
        :return: How far it lies from there and, for a step planned again, from where
            the plan was validated, as a person reads it.
        """
        away = f"{centimetres(distance)} from {self.spot()}"
        if self.validated is None:
            return away
        from_validated = math.hypot(
            position.x - self.validated.x, position.y - self.validated.y
        )
        return f"{away}, {centimetres(from_validated)} from {VALIDATED_SPOT}"

    def summary(self, answered: bool, objects: List[ReportedObject]) -> str:
        """
        :param answered: Whether the perception answered.
        :param objects: The objects reported, judged.
        :return: The outcome in one sentence.
        """
        if not answered:
            return (
                f"The object recognition (RoboKudo) did not answer the search for "
                f"{self.looked_for()}."
            )
        taken = next(
            (
                seen
                for seen in objects
                if seen.verdict
                in (ReportedObjectVerdict.ACCEPTED, ReportedObjectVerdict.TOO_FAR)
            ),
            None,
        )
        if taken is not None:
            return " ".join(
                part for part in (self.found(taken), self.raised(taken)) if part
            )
        outside = [
            seen
            for seen in objects
            if seen.verdict is ReportedObjectVerdict.OUTSIDE_BOX
        ]
        if outside and self.grounded is not None:
            nearest = min(outside, key=lambda seen: seen.distance)
            width = self.box.maximum.x - self.box.minimum.x
            return sentence(
                f"{self.looked_for()} was seen "
                f"{self.away(nearest.position, nearest.distance)}, outside the "
                f"{centimetres(width)} box looked in."
            )
        nothing = f"no {self.color} object" if self.color else "no object"
        others = len(objects)
        tail = (
            ""
            if others == 0
            else f"; {others} other object{'s' if others > 1 else ''} "
            f"{'were' if others > 1 else 'was'} reported"
        )
        if self.table_wide:
            return f"A table-wide search found {nothing}{tail}."
        return f"{nothing.capitalize()} was found {self.where()}{tail}."

    def raised(self, taken: ReportedObject) -> str:
        """
        :param taken: The object the step took.
        :return: How far it was reported inside the surface it stands on and that it
            was raised onto it; empty when it lay inside none.
        """
        inside = taken.inside_surface
        if inside is None:
            return ""
        return sentence(
            f"{self.looked_for()} was reported {millimetres(inside.depth)} inside the "
            f"{inside.surface.replace('_', ' ')} and was raised onto it."
        )

    def found(self, taken: ReportedObject) -> str:
        """
        :param taken: The object the step took.
        :return: Where it was found, as a person reads it.
        """
        if self.table_wide:
            return f"A table-wide search found {self.looked_for()}."
        if taken.distance is None:
            return sentence(f"{self.looked_for()} was found {self.where()}.")
        away = self.away(taken.position, taken.distance)
        if taken.verdict is ReportedObjectVerdict.TOO_FAR:
            return sentence(
                f"{self.looked_for()} was seen {away}; the step allows "
                f"{centimetres(self.tolerance)}."
            )
        return sentence(f"{self.looked_for()} was found {away}.")
