"""
The live bundle of the current model, and questions about the live world.
"""

from __future__ import annotations

import json
from http import HTTPStatus

from cramera.http.messages import HttpMethod
from cramera_bridge.documents import DocumentFile
from cramera_bridge.geometry import NumericPose, PublishedColor
from cramera_bridge.live.bundles import WorldSignature
from cramera_bridge.live.world_model import ObjectKey
from cramera_bridge.questions.live import (
    LIVE_TITLE,
    SpatialValueFormat,
    WorldHighlightKeys,
    WorldQuestion,
    WorldVariable,
)
from cramera_protocol.bundle.files import BundleFile
from cramera_protocol.bundle.index import ReservedSceneName
from cramera_protocol.bundle.scene import SceneDescription
from cramera_protocol.live import LiveRoute
from cramera_protocol.questions import (
    AnswerColumn,
    QueryAnswer,
    QueryRequest,
    QueryScope,
    QuestionRequest,
    QuestionsParameter,
    Vocabulary,
)
from cramera_bridge.questions.presets import RobotQuestion
from semantic_digital_twin.semantic_annotations.semantic_annotations import Table
from krrood_spatial_math.spatial_types import HomogeneousTransformationMatrix, Point3
from semantic_digital_twin.world_description.geometry import Box, Scale

from .conftest import dataset_body, live_request

# %% live bundle


def test_the_live_bundle_is_written_once_per_model(publisher, kitchen, locations):
    scene = publisher.live_scene().scene
    scene_file = locations.local_scenes / scene / BundleFile.SCENE
    written = scene_file.stat().st_mtime_ns
    kitchen.milk_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
        2.0, 0.0, 0.9, reference_frame=kitchen.root
    )
    assert publisher.live_scene().scene == ReservedSceneName.LIVE
    assert scene_file.stat().st_mtime_ns == written
    description = DocumentFile(scene_file).read(SceneDescription)
    assert description.bundle_signature == publisher.info().bundle_signature
    assert description.world_bound is True


def test_the_live_bundle_describes_the_free_objects_where_they_are(
    publisher, kitchen, locations
):
    kitchen.milk_connection.origin = HomogeneousTransformationMatrix.from_xyz_rpy(
        1.5, -0.2, 0.9, yaw=0.3, reference_frame=kitchen.root
    )
    scene = publisher.live_scene().scene
    description = DocumentFile(locations.local_scenes / scene / BundleFile.SCENE).read(
        SceneDescription
    )
    objects = {entry.key: entry for entry in description.objects}
    assert set(objects) == set(publisher.model.object_keys())
    milk = objects[ObjectKey.of(kitchen.milk)]
    assert milk.spawn == NumericPose.of_entity(kitchen.world, kitchen.milk).published()
    assert (
        milk.color == PublishedColor(kitchen.milk.visual.shapes[0].color).hexadecimal()
    )
    assert (
        locations.local_scenes / scene / objects[ObjectKey.of(kitchen.cup)].mesh
    ).is_file()


def test_a_new_free_object_changes_the_signature(publisher, kitchen):
    before = WorldSignature(publisher.model, None).digest()
    with kitchen.world.modify_world():
        kitchen.world.remove_connection(kitchen.cup.parent_connection)
        kitchen.world.remove_kinematic_structure_entity(kitchen.cup)
    assert WorldSignature(publisher.model, None).digest() != before


def test_a_model_change_changes_the_signature(publisher, kitchen):
    before = WorldSignature(publisher.model, None).digest()
    with kitchen.world.modify_world():
        kitchen.world.remove_connection(kitchen.table.parent_connection)
        kitchen.world.remove_kinematic_structure_entity(kitchen.table)
    assert WorldSignature(publisher.model, None).digest() != before


def test_a_body_drawn_anew_changes_the_signature(publisher, kitchen):
    before = WorldSignature(publisher.model, None).digest()
    with kitchen.world.modify_world():
        kitchen.table.visual.shapes[0] = Box(scale=Scale(2.0, 0.6, 0.05))
    assert WorldSignature(publisher.model, None).digest() != before


# %% questions


def test_the_live_presets_suit_the_world(publisher):
    catalog = publisher.presets()
    texts = [preset.text for preset in catalog.presets]
    assert catalog.title == LIVE_TITLE
    assert RobotQuestion.WHICH_ROBOT.value in texts
    assert WorldQuestion.BODIES.value in texts
    assert WorldQuestion.HANDLES.value not in texts


def test_a_new_annotation_adds_its_question(publisher, kitchen):
    with kitchen.world.modify_world():
        kitchen.world.add_semantic_annotation(Table(root=kitchen.table))
    texts = [preset.text for preset in publisher.presets().presets]
    assert WorldQuestion.SURFACES.value in texts


def test_a_query_answers_with_the_current_objects(answerer, publisher, kitchen):
    answer = answerer.answer(
        live_request(
            HttpMethod.POST, LiveRoute.QUERY, dataset_body("query_robot_free.json")
        )
    )
    document = QueryAnswer.from_json(json.loads(answer.body))
    assert document.ok is True
    assert [row[AnswerColumn.ENTITY] for row in document.rows] == [
        str(body.name) for body in publisher.model.objects
    ]
    assert str(kitchen.milk.id) in document.highlight


def test_a_question_matches_a_preset(publisher):
    match = publisher.answer_question(QuestionRequest(text="show all scene bodies"))
    assert match.matched is True
    assert match.preset.text == WorldQuestion.BODIES


def test_a_broken_query_is_an_answer_without_rows(publisher):
    answer = publisher.answer_query(QueryRequest(code="an(entity(nothing))"))
    assert answer.ok is False
    assert answer.rows == []


def test_vocabulary_and_members_are_served(answerer):
    vocabulary = answerer.answer(
        live_request(
            HttpMethod.GET,
            LiveRoute.VOCABULARY,
            query=f"{QuestionsParameter.SCOPE}={QueryScope.CURRENT_STATE}",
        )
    )
    members = answerer.answer(
        live_request(
            HttpMethod.GET,
            LiveRoute.MEMBERS,
            query=f"{QuestionsParameter.NAME}={WorldVariable.OBJECT}",
        )
    )
    names = [
        entry.name
        for entry in Vocabulary.from_json(json.loads(vocabulary.body)).entries
    ]
    assert WorldVariable.OBJECT.value in names
    assert members.status is HTTPStatus.OK


def test_an_unknown_scope_or_a_missing_name_is_refused(answerer):
    unknown = answerer.answer(
        live_request(
            HttpMethod.GET,
            LiveRoute.VOCABULARY,
            query=f"{QuestionsParameter.SCOPE}=dreams",
        )
    )
    missing = answerer.answer(live_request(HttpMethod.GET, LiveRoute.MEMBERS))
    assert unknown.status is HTTPStatus.BAD_REQUEST
    assert missing.status is HTTPStatus.BAD_REQUEST


def test_highlights_name_objects_by_key_and_links_by_name(publisher, kitchen):
    keys = WorldHighlightKeys(publisher.current_objects)
    table = Table(root=kitchen.table)
    with kitchen.world.modify_world():
        kitchen.world.add_semantic_annotation(table)
    assert keys.keys_of(kitchen.milk) == [str(kitchen.milk.id)]
    assert keys.keys_of(kitchen.table) == [str(kitchen.table.name)]
    assert keys.keys_of(table) == [str(kitchen.table.name)]
    assert keys.keys_of("milk") == []


def test_spatial_values_are_written_as_numbers(kitchen):
    value_format = SpatialValueFormat()
    pose = HomogeneousTransformationMatrix.from_xyz_rpy(1, 2, 3)
    assert value_format.accepts(pose) and not value_format.accepts(3)
    assert value_format.text(Point3(1, 2, 3)) == "(1.00, 2.00, 3.00)"
    assert value_format.text(pose).startswith("(1.00, 2.00, 3.00)")
