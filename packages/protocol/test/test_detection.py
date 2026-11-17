"""
Detecting objects: the region a detection looks in, the references later steps make to
what it finds, and scans of the scene.
"""

from __future__ import annotations

import pytest

from cramera_protocol.browser import BROWSER_ENUMERATIONS
from cramera_protocol.detection import (
    DetectedObjectReference,
    DetectionGrounding,
    DetectionRegion,
    DetectionRegionKey,
    RegionKind,
    ScanRequest,
    SceneScan,
    SceneScanKey,
    SceneScanOrder,
)
from cramera_protocol.exceptions import InvalidDocumentValue
from cramera_protocol.geometry import Pose
from cramera_protocol.plan_builder import DemoDocument, PlanDocument, PlannedObject

# %% regions


def test_a_region_without_a_kind_is_the_whole_table():
    assert DetectionRegion.from_json({}) == DetectionRegion(kind=RegionKind.TABLE)


def test_a_region_of_an_unknown_kind_is_refused():
    with pytest.raises(InvalidDocumentValue):
        DetectionRegion.from_json({DetectionRegionKey.KIND: "kitchen"})


# %% references


def test_a_reference_names_the_detection_step_it_was_written_for():
    reference = DetectedObjectReference("step_2")
    assert DetectedObjectReference.from_value(reference.text) == reference


def test_an_object_identity_is_no_reference():
    assert DetectedObjectReference.from_value("red_cube") is None


# %% scans


def test_a_scan_order_and_its_scan_survive_their_json():
    cube = PlannedObject(
        identifier="red_cube",
        model="cubes/red.urdf",
        pose=Pose(0.5, 0, 0.9, 0, 0, 0, 1),
    )
    order = SceneScanOrder(
        demo=DemoDocument(plan=PlanDocument(name="empty")), simulated_objects=[cube]
    )
    scan = SceneScan(objects=[cube], grounding=DetectionGrounding.ASSUMED)
    assert SceneScanOrder.from_json(order.to_json()) == order
    assert SceneScan.from_json(scan.to_json()) == scan
    assert (
        ScanRequest.from_json(
            ScanRequest(plan=PlanDocument(name="empty")).to_json()
        ).region
        == DetectionRegion()
    )


def test_the_browser_reads_the_names_of_a_scan():
    assert {RegionKind, DetectionGrounding, SceneScanKey} <= set(BROWSER_ENUMERATIONS)


def test_a_region_names_a_zone_or_lies_around_a_marker():
    zone = DetectionRegion.from_json(
        {DetectionRegionKey.KIND: RegionKind.ZONE, DetectionRegionKey.ZONE: "left_tray"}
    )
    marker = DetectionRegion(
        kind=RegionKind.MARKER, pose=Pose(0.6, 0.2, 0.88, 0, 0, 0, 1)
    )
    assert zone.zone == "left_tray"
    assert DetectionRegion.from_json(marker.to_json()) == marker
