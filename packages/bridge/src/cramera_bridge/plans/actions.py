"""
The native actions of the CRAM workspace as the builder offers them: every concrete
action dataclass of coraplex's actions package, each parameter read on its own from the
class that declares it and mapped to a form control by its type.

An action's parameters are the init fields its own classes declare, those
:attr:`~coraplex.robot_plans.actions.base.Action.designator_parameter` lists, not the
fields every statechart node has; a gripper configuration is offered as the arm whose
gripper it configures and the gripper state it brings the gripper into.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import pkgutil
import re
import sys
import textwrap
import types
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from types import ModuleType, SimpleNamespace

from typing_extensions import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import coraplex.robot_plans.actions
from coraplex.robot_plans.actions.base import Action
from cramera.cram_imports import CramImports
from cramera_bridge.plans.arms import ArmField, Arms
from cramera_bridge.plans.object_types import ObjectType
from cramera_bridge.exceptions import UnknownAction
from cramera_bridge.names import (
    ACTION_NAMES,
    ANNOTATION_NAMES,
    ENUM_NAMES,
    FIELD_NAMES,
)
from cramera_protocol.plan_builder import (
    ActionCatalog,
    ActionField,
    ActionFieldKind,
    ActionGroup,
    ActionSchema,
    FailedActionModule,
    HiddenActionField,
)
from krrood.utils import recursive_subclasses
from semantic_digital_twin.datastructures.definitions import GripperState
from semantic_digital_twin.datastructures.robots.gripper_configuration import (
    GripperConfiguration,
)
from semantic_digital_twin.grasping.grasp_candidates import GraspCandidate
from semantic_digital_twin.robots.robot_parts import AbstractRobotPart, EndEffector
from semantic_digital_twin.semantic_annotations.mixins import HasGraspCandidates
from krrood_spatial_math.spatial_types import Pose
from semantic_digital_twin.world_description.geometry import VolumetricBoundingBox
from semantic_digital_twin.world_description.world_entity import (
    Body,
    SemanticAnnotation,
)

ACTION_SUFFIX = "Action"
"""
The suffix of an action's class name, which its label leaves out.
"""

WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
"""
Where a new word of a class name starts.
"""

NAME_SEPARATOR = "_"
"""
Separates the words of a field's name.
"""

PARAGRAPH_SEPARATOR = "\n\n"
"""
Separates the paragraphs of a docstring.
"""

MODULE_PATH = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*\.)+(?=[A-Za-z_])")
"""
The module path in front of a class name in a type's text.
"""

ARM_ENUMERATION = Arms
"""
The enumeration a field taking a robot's arms offers its choices from.
"""

PLAN_OBJECT_TYPES = (GraspCandidate, HasGraspCandidates)
"""
The field types an object the plan places is given as: the object itself, which is
graspable, or the grasp of it closest to the robot.
"""


class ActionConvention(StrEnum):
    """
    Field names coraplex uses throughout its actions for one meaning.
    """

    DESTINATION = "target_location"
    """
    The pose an action puts its object down at, or drives the robot to when the action
    acts on no object.
    """


class GripperConfigurationPart(StrEnum):
    """
    The fields a gripper configuration is offered as, named as coraplex named them
    before its actions took a configuration.
    """

    GRIPPER = "gripper"
    """
    The arm whose gripper is configured.
    """

    MOTION = "motion"
    """
    The gripper state the gripper is brought into.
    """


@dataclass(frozen=True)
class ConfigurationPart:
    """
    One field a gripper configuration is offered as.
    """

    part: GripperConfigurationPart
    """
    The field's name.
    """

    python_type: Any
    """
    The type the field takes.
    """

    documentation: str
    """
    What the field means.
    """


GRIPPER_CONFIGURATION_PARTS: Tuple[ConfigurationPart, ...] = (
    ConfigurationPart(
        part=GripperConfigurationPart.GRIPPER,
        python_type=EndEffector,
        documentation="The gripper that is configured, picked by its arm.",
    ),
    ConfigurationPart(
        part=GripperConfigurationPart.MOTION,
        python_type=GripperState,
        documentation="The state the gripper is brought into.",
    ),
)
"""
The fields a gripper configuration is offered as, in the order they are offered.
"""

# %% modules


@dataclass(frozen=True)
class ActionModules:
    """
    The modules of the actions package, each imported on its own so a module that fails
    to import hides only its own actions.
    """

    package: ModuleType = coraplex.robot_plans.actions
    """
    The actions package.
    """

    def import_all(self) -> List[FailedActionModule]:
        """
        Import every module below :attr:`package`, the packages' modules included.

        :return: The modules that failed to import, sorted by name.
        """
        failed = []
        pending = [self.package]
        while pending:
            package = pending.pop()
            for module in pkgutil.iter_modules(
                package.__path__, f"{package.__name__}."
            ):
                imported = self.imported(module.name)
                if isinstance(imported, FailedActionModule):
                    failed.append(imported)
                elif module.ispkg:
                    pending.append(imported)
        return sorted(failed, key=lambda module: module.module)

    @staticmethod
    def imported(name: str) -> Union[ModuleType, FailedActionModule]:
        """
        A module of the workspace may fail to import in a checkout whose packages do not
        fit together; this is the boundary where that failure becomes data.

        :param name: A module's full name.
        :return: The module, or what importing it raised.
        """
        try:
            return CramImports().module(name)
        except Exception as error:
            return FailedActionModule(
                module=name, error=f"{type(error).__name__}: {error}"
            )

    def group_of(self, module: str) -> str:
        """
        :param module: A module below :attr:`package`.
        :return: The package right below :attr:`package` it lies in.
        """
        return module.removeprefix(f"{self.package.__name__}.").split(".")[0]


# %% field types


class FieldKindRule(ABC):
    """
    Maps the types of one family to the kind of field they are.
    """

    @abstractmethod
    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        """
        :param annotation: A resolved field type, without ``None`` in a union.
        :return: The kind of field it is, or None when the rule does not apply.
        """

    def offered_type(self, annotation: Any) -> Any:
        """
        :param annotation: A field type the rule applies to.
        :return: The type a step's value is read as, the field type itself unless the
            rule offers it as another.
        """
        return annotation


@dataclass(frozen=True)
class SubclassRule(FieldKindRule):
    """
    Classes deriving from a base, apart from some excluded families, are one kind.
    """

    base: type
    """
    The base class.
    """

    kind: ActionFieldKind
    """
    The kind its subclasses are.
    """

    excluded: Tuple[type, ...] = ()
    """
    Subclass families that are not this kind.
    """

    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        if (
            isinstance(annotation, type)
            and issubclass(annotation, self.base)
            and not issubclass(annotation, self.excluded)
        ):
            return self.kind
        return None


@dataclass(frozen=True)
class SurfaceSampleRule(FieldKindRule):
    """
    A pose or poses to try one after another, which a surface's sampled spots supply.
    """

    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        members = UnionMembers(annotation).present()
        if len(members) != 2 or Pose not in members:
            return None
        (other,) = [member for member in members if member is not Pose]
        if get_origin(other) is Iterable and get_args(other) == (Pose,):
            return ActionFieldKind.POSE_OR_SURFACE
        return None


@dataclass(frozen=True)
class ArmRule(FieldKindRule):
    """
    An arm, several arms or an arm's end effector, which a step names by
    :data:`ARM_ENUMERATION`.
    """

    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        if ArmField(annotation).part() is not None:
            return ActionFieldKind.ENUM
        return None


@dataclass(frozen=True)
class PlanObjectRule(FieldKindRule):
    """
    An object the plan places, which a step names by its identity.
    """

    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        if annotation in PLAN_OBJECT_TYPES:
            return ActionFieldKind.BODY
        return None


@dataclass(frozen=True)
class ObjectTypeRule(FieldKindRule):
    """
    A class of annotation to look for, which a step picks among the kinds of the plan's
    free objects, :class:`ObjectType`.
    """

    def kind_of(self, annotation: Any) -> Optional[ActionFieldKind]:
        if get_origin(annotation) is type and get_args(annotation) == (
            SemanticAnnotation,
        ):
            return ActionFieldKind.ENUM
        return None

    def offered_type(self, annotation: Any) -> Any:
        return ObjectType


FIELD_KIND_RULES: Tuple[FieldKindRule, ...] = (
    SurfaceSampleRule(),
    ObjectTypeRule(),
    ArmRule(),
    PlanObjectRule(),
    SubclassRule(Enum, ActionFieldKind.ENUM),
    SubclassRule(
        SemanticAnnotation,
        ActionFieldKind.ANNOTATION,
        excluded=(AbstractRobotPart,),
    ),
    SubclassRule(Body, ActionFieldKind.BODY),
    SubclassRule(VolumetricBoundingBox, ActionFieldKind.REGION),
    SubclassRule(Pose, ActionFieldKind.POSE),
    SubclassRule(bool, ActionFieldKind.BOOLEAN),
    SubclassRule(int, ActionFieldKind.INTEGER),
    SubclassRule(float, ActionFieldKind.NUMBER),
    SubclassRule(str, ActionFieldKind.TEXT),
)
"""
The rules mapping field types to kinds, tried in order; enumerations come before the
numbers some of them derive from, and booleans before integers.

A robot's own parts are no entities a person picks, so they are no annotation fields;
its arms are picked by side.
"""


@dataclass(frozen=True)
class UnionMembers:
    """
    The members of a union type.
    """

    annotation: Any
    """
    A resolved type.
    """

    def present(self) -> List[Any]:
        """
        :return: The members of a union other than ``None``; a type that is no union is
            its own only member.
        """
        if get_origin(self.annotation) not in (Union, types.UnionType):
            return [self.annotation]
        return [
            member for member in get_args(self.annotation) if member is not type(None)
        ]


@dataclass(frozen=True)
class ClassifiedType:
    """
    A field type with the kind of field it is.
    """

    kind: ActionFieldKind
    """
    The kind of field.
    """

    python_type: Any
    """
    The type a step's value is read as: the field type without ``None`` in a union, or
    the type its rule offers it as.
    """


@dataclass(frozen=True)
class FieldKinds:
    """
    Classifies field types by the first rule that applies.
    """

    rules: Tuple[FieldKindRule, ...] = FIELD_KIND_RULES
    """
    The rules, in the order they are tried.
    """

    def classify(self, annotation: Any) -> Optional[ClassifiedType]:
        """
        :param annotation: A resolved field type.
        :return: The kind of field it is, an optional type classified by what it holds
            when present; None for a type without a form control.
        """
        for rule in self.rules:
            kind = rule.kind_of(annotation)
            if kind is not None:
                return ClassifiedType(
                    kind=kind, python_type=rule.offered_type(annotation)
                )
        members = UnionMembers(annotation).present()
        if len(members) == 1 and members[0] is not annotation:
            return self.classify(members[0])
        return None


# %% declared fields


@dataclass(frozen=True)
class UnresolvedType:
    """
    A field type that cannot be resolved, for example one naming a class imported only
    while type checking.
    """

    reason: str
    """
    The class and message of the error resolving it raised.
    """


@dataclass(frozen=True)
class TypeText:
    """
    A field type as people read it.
    """

    annotation: Any
    """
    A resolved type.
    """

    def text(self) -> str:
        """
        :return: A class's name, or the type's text without module paths.
        """
        if isinstance(self.annotation, type) and not get_args(self.annotation):
            return self.annotation.__name__
        return MODULE_PATH.sub("", str(self.annotation))


@dataclass(frozen=True)
class ClassSource:
    """
    The source of a class, for its docstrings.
    """

    node: Optional[ast.ClassDef]
    """
    The class's syntax tree, or None when its source cannot be read.
    """

    @classmethod
    def of(cls, owner: type) -> ClassSource:
        """
        A class compiled without its source file has none to read; this is the boundary
        where that becomes data.

        :param owner: A class.
        :return: Its source.
        """
        try:
            source = textwrap.dedent(inspect.getsource(owner))
        except (OSError, TypeError):
            return cls(node=None)
        for statement in ast.parse(source).body:
            if isinstance(statement, ast.ClassDef):
                return cls(node=statement)
        return cls(node=None)

    def docstring(self) -> str:
        """
        :return: The first paragraph of the class's docstring, or the empty text.
        """
        if self.node is None:
            return ""
        text = ast.get_docstring(self.node) or ""
        return text.split(PARAGRAPH_SEPARATOR)[0].strip()

    def attribute_docstring(self, name: str) -> str:
        """
        :param name: An attribute the class declares.
        :return: The string right below its declaration, or the empty text.
        """
        if self.node is None:
            return ""
        body = self.node.body
        for statement, following in zip(body, body[1:]):
            if (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == name
                and isinstance(following, ast.Expr)
                and isinstance(following.value, ast.Constant)
                and isinstance(following.value.value, str)
            ):
                return inspect.cleandoc(following.value.value)
        return ""


@dataclass(frozen=True)
class DeclaredField:
    """
    One init field of an action dataclass, read from the class that declares it.
    """

    action: type
    """
    The action class.
    """

    declaration: dataclasses.Field
    """
    The dataclass field.
    """

    @property
    def name(self) -> str:
        """
        The field's keyword argument.
        """
        return self.declaration.name

    def owner(self) -> type:
        """
        :return: The class of the action's hierarchy whose own annotations declare the
            field.
        """
        for candidate in self.action.__mro__:
            if self.name in vars(candidate).get("__annotations__", {}):
                return candidate
        return self.action

    def annotation(self) -> Union[Any, UnresolvedType]:
        """
        The annotation is resolved in the module and class that declare it, apart from
        the action's other annotations, some of which name classes importable only while
        type checking; this is the boundary where such a name becomes data.

        :return: The field's type, or why it cannot be resolved.
        """
        owner = self.owner()
        holder = SimpleNamespace(
            __annotations__={self.name: vars(owner)["__annotations__"][self.name]}
        )
        try:
            return get_type_hints(
                holder,
                globalns=vars(sys.modules[owner.__module__]),
                localns=dict(vars(owner)),
            )[self.name]
        except (NameError, AttributeError, TypeError, SyntaxError) as error:
            return UnresolvedType(reason=f"{type(error).__name__}: {error}")

    def has_default(self) -> bool:
        """
        :return: Whether the action fills the field when no value is given.
        """
        return (
            self.declaration.default is not dataclasses.MISSING
            or self.declaration.default_factory is not dataclasses.MISSING
        )

    def default_value(self) -> Any:
        """
        :return: The JSON form of the field's default: an enumeration member's name, a
            plain value, or None for a default JSON cannot hold.
        """
        default = self.declaration.default
        if isinstance(default, Enum):
            return default.name
        if isinstance(default, (bool, int, float, str)):
            return default
        return None

    def label(self) -> str:
        """
        :return: The field's name in words.
        """
        return self.name.replace(NAME_SEPARATOR, " ")


@dataclass(frozen=True)
class ActionParameters:
    """
    The parameters of an action class, as
    :attr:`~coraplex.robot_plans.actions.base.Action.designator_parameter` lists them
    for an action.
    """

    action: type
    """
    The action class.
    """

    def declarations(self) -> List[dataclasses.Field]:
        """
        :return: The init fields the action's classes declare besides those of every
            :class:`~coraplex.robot_plans.actions.base.Action`, which the statechart
            node it runs as has, in declaration order.
        """
        statechart_fields = {
            declaration.name for declaration in dataclasses.fields(Action)
        }
        return [
            declaration
            for declaration in dataclasses.fields(self.action)
            if declaration.init and declaration.name not in statechart_fields
        ]


# %% schemas


@dataclass(frozen=True)
class DiscoveredAction:
    """
    A native action class with the schema the builder offers it by.
    """

    action_class: Type[Action]
    """
    The action's class.
    """

    schema: ActionSchema
    """
    The schema.
    """

    field_types: Dict[str, Any]
    """
    The type of each offered field, without ``None`` in a union, by field identifier.
    """

    field_names: Dict[str, str] = field(default_factory=dict)
    """
    The keyword argument each field identifier stands for.
    """

    configurations: Dict[str, Dict[GripperConfigurationPart, str]] = field(
        default_factory=dict
    )
    """
    The identifier of each field a gripper configuration is offered as, by the keyword
    argument taking the configuration.
    """

    def configuration_parts(self) -> Set[str]:
        """
        :return: The identifiers of the fields gripper configurations are offered as.
        """
        return {
            identifier
            for parts in self.configurations.values()
            for identifier in parts.values()
        }

    def field_name(self, identifier: str) -> str:
        """
        :param identifier: A field identifier of the schema.
        :return: The action's keyword argument it stands for.
        """
        return self.field_names.get(identifier, identifier)

    def looks_for_objects(self) -> bool:
        """
        :return: Whether the action looks for objects in a region it is given.
        """
        return any(
            offered.kind is ActionFieldKind.REGION for offered in self.schema.fields
        )


@dataclass
class ActionSchemaBuilder:
    """
    Reads the schema of one action class.
    """

    action_class: Type[Action]
    """
    The action's class.
    """

    group: str
    """
    The package of the actions package its module lies in.
    """

    kinds: FieldKinds
    """
    Classifies the fields' types.
    """

    sources: Dict[type, ClassSource]
    """
    The sources of the classes read so far, shared between builders.
    """

    fields: List[ActionField] = field(default_factory=list, init=False)
    """
    The offered fields, in declaration order.
    """

    hidden: List[HiddenActionField] = field(default_factory=list, init=False)
    """
    The fields left at their default.
    """

    reasons: List[str] = field(default_factory=list, init=False)
    """
    Why a step cannot be the action.
    """

    field_types: Dict[str, Any] = field(default_factory=dict, init=False)
    """
    The type of each offered field, by field identifier.
    """

    field_names: Dict[str, str] = field(default_factory=dict, init=False)
    """
    The keyword argument of each field identifier.
    """

    configurations: Dict[str, Dict[GripperConfigurationPart, str]] = field(
        default_factory=dict, init=False
    )
    """
    The fields each gripper configuration is offered as, by its keyword argument.
    """

    @property
    def identifier(self) -> str:
        """
        The action's identifier.
        """
        return ACTION_NAMES.identifier_of(self.action_class)

    def field_identifier(self, declared: DeclaredField) -> str:
        """
        :param declared: An init field of the action.
        :return: Its identifier, noting the keyword argument it stands for.
        """
        identifier = FIELD_NAMES.identifier_of(self.identifier, declared.name)
        self.field_names[identifier] = declared.name
        return identifier

    def build(self) -> DiscoveredAction:
        """
        :return: The action with its schema.
        """
        for declaration in ActionParameters(self.action_class).declarations():
            self.read(DeclaredField(self.action_class, declaration))
        self.mark_surface_fields()
        schema = ActionSchema(
            name=self.identifier,
            saved_as=ACTION_NAMES.saved_as(self.action_class),
            module=self.action_class.__module__,
            label=self.label(),
            group=self.group,
            documentation=self.source(self.action_class).docstring(),
            fields=sorted(self.fields, key=lambda offered: not offered.required),
            hidden=self.hidden,
            available=not self.reasons,
            unavailable_reason=self.reasons[0] if self.reasons else None,
        )
        return DiscoveredAction(
            action_class=self.action_class,
            schema=schema,
            field_types=self.field_types,
            field_names=self.field_names,
            configurations=self.configurations,
        )

    def read(self, declared: DeclaredField) -> None:
        """
        Offer a field, hide it or note why it makes the action unavailable.

        :param declared: An init field of the action.
        """
        annotation = declared.annotation()
        if isinstance(annotation, type) and issubclass(
            annotation, GripperConfiguration
        ):
            self.read_configuration(declared)
            return
        classified = None
        if not isinstance(annotation, UnresolvedType):
            classified = self.kinds.classify(annotation)
        if classified is None:
            self.unsupported(declared, annotation)
            return
        self.field_types[self.field_identifier(declared)] = classified.python_type
        self.fields.append(self.offered(declared, classified))

    def unsupported(
        self, declared: DeclaredField, annotation: Union[Any, UnresolvedType]
    ) -> None:
        """
        :param declared: A field whose type has no form control.
        :param annotation: Its type, or why it cannot be resolved.
        """
        if isinstance(annotation, UnresolvedType):
            type_name = f"an unresolved type ({annotation.reason})"
        else:
            type_name = TypeText(annotation).text()
        if declared.has_default():
            self.hidden.append(
                HiddenActionField(
                    name=self.field_identifier(declared), type_name=type_name
                )
            )
            return
        self.reasons.append(
            f"field {declared.name!r} has type {type_name}, which has no form control "
            f"yet"
        )

    def offered(
        self, declared: DeclaredField, classified: ClassifiedType
    ) -> ActionField:
        """
        :param declared: A field whose type has a form control.
        :param classified: Its kind.
        :return: The field as the schema offers it.
        """
        offered = ActionField(
            name=self.field_identifier(declared),
            label=declared.label(),
            kind=classified.kind,
            required=not declared.has_default(),
            default=declared.default_value(),
            documentation=self.source(declared.owner()).attribute_docstring(
                declared.name
            ),
        )
        if classified.kind is ActionFieldKind.ENUM:
            self.enumerate(
                offered, classified.python_type, declared.declaration.default
            )
        if classified.kind is ActionFieldKind.ANNOTATION:
            offered.annotation = ANNOTATION_NAMES.identifier_of(classified.python_type)
        return offered

    @staticmethod
    def enumerate(offered: ActionField, python_type: Any, default: Any) -> None:
        """
        Give an enumeration field the members it offers.

        :param offered: A field of kind enumeration.
        :param python_type: The enumeration it takes, or a type taking the robot's arms.
        :param default: The field's native default.
        """
        enumeration = (
            ARM_ENUMERATION if ArmField(python_type).part() is not None else python_type
        )
        offered.enum_type = ENUM_NAMES.identifier_of(enumeration)
        offered.members = [
            ENUM_NAMES.member_identifier(enumeration, member) for member in enumeration
        ]
        offered.saved_members = ENUM_NAMES.saved_members(enumeration)
        # an arm field's default is a member of the planner's own arm choice, which
        # shares its member names with the enumeration offered
        if isinstance(default, Enum) and default.name in enumeration.__members__:
            offered.default = ENUM_NAMES.member_identifier(
                enumeration, enumeration[default.name]
            )

    def read_configuration(self, declared: DeclaredField) -> None:
        """
        Offer a gripper configuration as the fields of
        :data:`GRIPPER_CONFIGURATION_PARTS`.

        :param declared: An init field taking a gripper configuration.
        """
        parts = {}
        for part in GRIPPER_CONFIGURATION_PARTS:
            identifier = FIELD_NAMES.identifier_of(self.identifier, part.part)
            offered = ActionField(
                name=identifier,
                label=part.part.replace(NAME_SEPARATOR, " "),
                kind=ActionFieldKind.ENUM,
                required=not declared.has_default(),
                documentation=part.documentation,
            )
            self.enumerate(offered, part.python_type, None)
            self.field_types[identifier] = part.python_type
            self.fields.append(offered)
            parts[part.part] = identifier
        self.configurations[declared.name] = parts

    def mark_surface_fields(self) -> None:
        """
        Mark the pose and surface fields that take a surface: those sampling a surface's
        spots, and the destination of an action acting on an object.
        """
        bodies = [
            offered for offered in self.fields if offered.kind is ActionFieldKind.BODY
        ]
        for offered in self.fields:
            offered.accepts_surface = (
                offered.kind is ActionFieldKind.POSE_OR_SURFACE
                or (
                    offered.kind is ActionFieldKind.POSE
                    and offered.name == ActionConvention.DESTINATION
                    and bool(bodies)
                )
            )

    def label(self) -> str:
        """
        :return: The words of the class name, without the ``Action`` suffix.
        """
        name = self.action_class.__name__
        return " ".join(WORD_BOUNDARY.split(name.removesuffix(ACTION_SUFFIX) or name))

    def source(self, owner: type) -> ClassSource:
        """
        :param owner: A class of the action's hierarchy.
        :return: Its source, read once per class.
        """
        if owner not in self.sources:
            self.sources[owner] = ClassSource.of(owner)
        return self.sources[owner]


# %% discovery


@dataclass(frozen=True)
class DiscoveredActions:
    """
    The actions found in the workspace, and the modules that failed to import.
    """

    actions: List[DiscoveredAction]
    """
    The actions, core actions first, then by label.
    """

    failed_modules: List[FailedActionModule]
    """
    The modules that failed to import.
    """

    def catalog(self) -> ActionCatalog:
        """
        :return: The schemas and failed modules as the catalog lists them.
        """
        return ActionCatalog(
            actions=[discovered.schema for discovered in self.actions],
            failed_modules=list(self.failed_modules),
        )

    def named(self, name: str) -> DiscoveredAction:
        """
        :param name: An action's identifier, or a name a plan saved it under before.
        :return: The action.
        :raises UnknownAction: When the workspace has no action of that name.
        """
        for discovered in self.actions:
            if ACTION_NAMES.matches(discovered.action_class, name):
                return discovered
        raise UnknownAction(
            name, [discovered.schema.name for discovered in self.actions]
        )


@dataclass(frozen=True)
class ActionDiscovery:
    """
    Finds the concrete action dataclasses of the actions package.
    """

    modules: ActionModules = field(default_factory=ActionModules)
    """
    The actions package's modules.
    """

    kinds: FieldKinds = field(default_factory=FieldKinds)
    """
    Classifies the fields' types.
    """

    def discover(self) -> DiscoveredActions:
        """
        :return: Every action defined in a module of the actions package that imports,
            with its schema; a class declaring itself abstract by deriving from
            :class:`~abc.ABC` directly is no action a step can be.
        """
        failed = self.modules.import_all()
        prefix = f"{self.modules.package.__name__}."
        classes = {
            candidate
            for candidate in recursive_subclasses(Action)
            if candidate.__module__.startswith(prefix)
            and dataclasses.is_dataclass(candidate)
            and not inspect.isabstract(candidate)
            and ABC not in candidate.__bases__
        }
        sources: Dict[type, ClassSource] = {}
        discovered = [
            ActionSchemaBuilder(
                action_class=candidate,
                group=self.modules.group_of(candidate.__module__),
                kinds=self.kinds,
                sources=sources,
            ).build()
            for candidate in classes
        ]
        return DiscoveredActions(
            actions=sorted(discovered, key=self.order), failed_modules=failed
        )

    @staticmethod
    def order(discovered: DiscoveredAction) -> Tuple[int, str, str]:
        """
        :param discovered: An action.
        :return: Its place in the palette: by the order of :class:`ActionGroup`, other
            groups after them, then by label.
        """
        groups = [group.value for group in ActionGroup]
        schema = discovered.schema
        rank = groups.index(schema.group) if schema.group in groups else len(groups)
        return rank, schema.group, schema.label
