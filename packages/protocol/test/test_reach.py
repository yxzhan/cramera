"""
What a robot reaches: the sphere around each arm's shoulder that a marker is quickly
checked against, and the table top a marker snaps to.
"""

from __future__ import annotations

from cramera_protocol.browser import BROWSER_ENUMERATIONS
from cramera_protocol.geometry import FloorRectangle, Vector3
from cramera_protocol.reach import (
    ArmReach,
    RobotReach,
    RobotReachKey,
    TableTop,
)


def tracy_like() -> RobotReach:
    """
    :return: Two arms over a table, the left one reaching 1 m around its shoulder.
    """
    return RobotReach(
        robot="tracy",
        arms=[
            ArmReach(arm="left", shoulder=Vector3(0.1, 0.2, 1.1), radius=1.0),
            ArmReach(arm="right", shoulder=Vector3(0.1, -0.2, 1.1), radius=1.0),
        ],
        table=TableTop(
            area=FloorRectangle(min_x=0, max_x=1.2, min_y=-0.8, max_y=0.8), height=0.88
        ),
    )


def test_a_point_within_an_arms_sphere_may_be_reached_and_one_beyond_every_arm_not():
    reach = tracy_like()
    assert reach.reaches(Vector3(0.6, 0.2, 0.9))
    assert reach.reaches(Vector3(0.6, -0.6, 0.9))
    assert not reach.reaches(Vector3(1.5, 0.1, 0.0))


def test_a_marker_rests_on_the_table_above_it_and_on_the_floor_beside_it():
    reach = tracy_like()
    assert reach.surface_height(0.6, 0.3) == 0.88
    assert reach.surface_height(1.5, 0.1) == 0.0


def test_the_reach_survives_its_json_and_the_browser_reads_its_keys():
    reach = tracy_like()
    assert RobotReach.from_json(reach.to_json()) == reach
    assert RobotReachKey in BROWSER_ENUMERATIONS
