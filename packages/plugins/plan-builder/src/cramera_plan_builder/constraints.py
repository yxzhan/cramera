"""
Constraints written in plain language, compiled by the rule book into the monitor the
live publisher observes and the boolean field of the step's own action it turns on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from typing_extensions import Any, Dict, List, Optional, Sequence

from cramera_plan_builder.exceptions import (
    ConstraintReferenceMissing,
    ConstraintSubjectMissing,
)
from cramera_protocol.live_messages import MonitorSpecification
from cramera_protocol.robot_profile import CameraMount
from cramera_protocol.plan_builder import (
    ActionFieldKind,
    ActionSchema,
    ConstraintRule,
    ConstraintRuleBook,
    ConstraintText,
    RuleValue,
    StepConstraint,
)

RULE_BOOK = Path(__file__).parent / "rules" / "constraints.json"
"""
The rule book the builder compiles constraints with.
"""

RULE_VALUES = frozenset(member.value for member in RuleValue)
"""
The texts that stand for a value the sentence or the plan supplies.
"""

WORD_SEPARATOR = "_"
"""
What separates the words of an object identity, read as a space in a sentence.
"""

# %% objects named in a sentence


@dataclass(frozen=True)
class NamedObjects:
    """
    The plan's objects a sentence names, by their identity or its words.
    """

    sentence: str
    """
    The sentence in lowercase.
    """

    objects: Sequence[str]
    """
    The identities of the plan's objects.
    """

    def in_order(self) -> List[str]:
        """
        :return: The named objects in the order the sentence names them first.
        """
        positions = {}
        for identifier in self.objects:
            position = self.position_of(identifier)
            if position is not None:
                positions[identifier] = position
        return sorted(positions, key=lambda identifier: positions[identifier])

    def position_of(self, identifier: str) -> Optional[int]:
        """
        :param identifier: An object's identity.
        :return: Where the sentence first names it as a whole word, or None.
        """
        spellings = {
            identifier.lower(),
            identifier.lower().replace(WORD_SEPARATOR, " "),
        }
        found = [
            match.start()
            for spelling in spellings
            for match in [re.search(rf"\b{re.escape(spelling)}\b", self.sentence)]
            if match is not None
        ]
        return min(found) if found else None


# %% compiling


@dataclass(frozen=True)
class ConstraintCompiler:
    """
    Compiles constraint sentences with a rule book: the first rule whose phrasing the
    sentence matches decides the monitor.
    """

    book: ConstraintRuleBook
    """
    The rules and how lengths are read.
    """

    camera_mount: Optional[CameraMount] = None
    """
    How the camera of the deployment's robot is mounted, which decides the rules it is
    offered; None when no profile says, which offers every rule.
    """

    @classmethod
    def of_file(
        cls, path: Path = RULE_BOOK, camera_mount: Optional[CameraMount] = None
    ) -> ConstraintCompiler:
        """
        :param path: A rule book file.
        :param camera_mount: How the camera of the deployment's robot is mounted.
        :return: The compiler of its rules.
        """
        return cls(
            book=ConstraintRuleBook.from_json(
                json.loads(path.read_text(encoding="utf-8"))
            ),
            camera_mount=camera_mount,
        )

    def offered_rules(self) -> List[ConstraintRule]:
        """
        :return: The rules offered to the deployment's robot, in the order they are
            tried.
        """
        return [
            rule for rule in self.book.rules if rule.is_offered_to(self.camera_mount)
        ]

    def compile(
        self, request: ConstraintText, action: Optional[ActionSchema] = None
    ) -> StepConstraint:
        """
        :param request: A sentence with what the plan tells about its subject.
        :param action: The schema of the step's action, or None when the step is not
            attached yet or the workspace does not offer its action.
        :return: The monitor type the sentence means, and once it is attached to a step,
            the monitor the live publisher observes and the boolean field of the step's
            action the rule turns on, when the action has it.
        :raises ConstraintSubjectMissing: When an attached constraint's monitor needs an
            object and neither the sentence nor the step names one.
        :raises ConstraintReferenceMissing: When an attached constraint's monitor needs
            a second object the sentence does not name.
        """
        sentence = request.text.lower()
        rule = self.rule_for(sentence)
        if rule is None:
            return StepConstraint(text=request.text)
        attached = request.step_action is not None
        monitor = None
        if rule.observed and attached:
            monitor = MonitorSpecification(
                monitor_type=rule.monitor_type,
                parameters=self.parameters(rule, sentence, request),
            )
        switch = None
        if attached and action is not None and self.has_switch(rule, action):
            switch = rule.switch
        return StepConstraint(
            text=request.text,
            monitor_type=rule.monitor_type,
            monitor=monitor,
            switch=switch,
        )

    @staticmethod
    def has_switch(rule: ConstraintRule, action: ActionSchema) -> bool:
        """
        :param rule: The rule a sentence matched.
        :param action: The schema of the step's action.
        :return: Whether the rule names a switch the action has as a boolean field.
        """
        if rule.switch is None:
            return False
        offered = action.field_named(rule.switch)
        return offered is not None and offered.kind is ActionFieldKind.BOOLEAN

    def rule_for(self, sentence: str) -> Optional[ConstraintRule]:
        """
        :param sentence: A sentence in lowercase.
        :return: The first rule offered to the robot whose phrasing it matches, or
            None.
        """
        for rule in self.offered_rules():
            if re.search(rule.phrasing, sentence) is not None:
                return rule
        return None

    def parameters(
        self, rule: ConstraintRule, sentence: str, request: ConstraintText
    ) -> Dict[str, Any]:
        """
        :param rule: The rule the sentence matched.
        :param sentence: The sentence in lowercase.
        :param request: The request, naming the step's object and the plan's objects.
        :return: The rule's parameters with the values the sentence and the plan
            supply in place of each :class:`RuleValue`.
        :raises ConstraintSubjectMissing: When no object is named for
            :attr:`RuleValue.OBJECT`.
        :raises ConstraintReferenceMissing: When no second object is named for
            :attr:`RuleValue.REFERENCE`.
        """
        named = NamedObjects(sentence, request.objects).in_order()
        subject = named[0] if named else request.step_object
        references = [identifier for identifier in named if identifier != subject]
        length = self.length(sentence)
        supplied = {
            RuleValue.OBJECT: subject,
            RuleValue.REFERENCE: references[0] if references else None,
            RuleValue.LENGTH: length if length is not None else rule.default_length,
        }
        return {
            key: self.resolved(value, supplied, request.text)
            for key, value in rule.parameters.items()
        }

    @staticmethod
    def resolved(value: Any, supplied: Dict[RuleValue, Any], text: str) -> Any:
        """
        :param value: A parameter value of a rule.
        :param supplied: What each :class:`RuleValue` stands for in this sentence.
        :param text: The constraint as it was written.
        :return: The value, or what it stands for when it is a :class:`RuleValue`.
        :raises ConstraintSubjectMissing: When it stands for an object none names.
        :raises ConstraintReferenceMissing: When it stands for a second object none
            names.
        """
        if not isinstance(value, str) or value not in RULE_VALUES:
            return value
        member = RuleValue(value)
        if supplied[member] is None and member is RuleValue.OBJECT:
            raise ConstraintSubjectMissing(text)
        if supplied[member] is None and member is RuleValue.REFERENCE:
            raise ConstraintReferenceMissing(text)
        return supplied[member]

    def length(self, sentence: str) -> Optional[float]:
        """
        :param sentence: A sentence in lowercase.
        :return: The first length it gives, in metres, or None.
        """
        found = re.search(self.book.length, sentence)
        if found is None:
            return None
        return float(found.group(1)) * self.book.units[found.group(2)]
