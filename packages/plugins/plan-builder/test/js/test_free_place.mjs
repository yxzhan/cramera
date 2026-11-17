import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { ZoneKey } from 'cramera-viewer/protocol.js';
import { FreePlace, RobotFrame } from '../../src/cramera_plan_builder/web/builder/free-place.js';
import { PlanPose } from '../../src/cramera_plan_builder/web/builder/poses.js';

const TRACY_REACH = JSON.parse(readFileSync(new URL('dataset/tracy_reach.json', import.meta.url), 'utf8'));
const AT_ORIGIN = PlanPose.of(0, 0, 0, 0);
const LEFT_TRAY = { [ZoneKey.IDENTIFIER]: 'left_tray', [ZoneKey.LABEL]: 'Left tray', [ZoneKey.AREA]: { minX: 0.6, maxX: 0.8, minY: 0.3, maxY: 0.5 } };
const close = (actual, expected) => actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 1e-9, `${actual} is not ${expected}`));

test('a marker over the table rests on its top, upright, keeping its heading', () => {
  const place = new FreePlace(TRACY_REACH, AT_ORIGIN);
  const tilted = [0.6, 0.3, 1.4, 0.1, 0, Math.sin(0.25), Math.cos(0.25)];
  const snapped = place.snapped(tilted);
  assert.deepEqual(PlanPose.position(snapped), [0.6, 0.3, 0.88]);
  assert.ok(Math.abs(PlanPose.yaw(snapped) - PlanPose.yaw(tilted)) < 0.05);
  assert.equal(snapped[3], 0);
});

test('a marker beside the table rests on the floor and no arm reaches it', () => {
  const place = new FreePlace(TRACY_REACH, AT_ORIGIN);
  const beyond = place.snapped(PlanPose.of(1.5, 0.1, 0.9, 0));
  assert.deepEqual(PlanPose.position(beyond), [1.5, 0.1, 0]);
  assert.equal(place.reaches(beyond), false);
  assert.equal(place.reaches(place.snapped(PlanPose.of(0.6, 0.3, 0, 0))), true);
});

test('the table and the reach move with the robot', () => {
  const turned = PlanPose.of(2, 1, 0, Math.PI / 2);
  const place = new FreePlace(TRACY_REACH, turned);
  const ahead = new RobotFrame(turned).toWorld([0.6, 0, 0]);
  close(ahead, [2, 1.6, 0]);
  assert.equal(PlanPose.position(place.snapped(PlanPose.of(...ahead.slice(0, 2), 0, 0)))[2], 0.88);
  assert.equal(PlanPose.position(place.snapped(PlanPose.of(2.6, 0.5, 0, 0)))[2], 0);
});

test('without a known reach a marker keeps its height and its reach is unknown', () => {
  const place = new FreePlace(null, AT_ORIGIN);
  assert.deepEqual(PlanPose.position(place.snapped(PlanPose.of(0.6, 0.3, 0.5, 0))), [0.6, 0.3, 0.5]);
  assert.equal(place.reaches(PlanPose.of(9, 9, 0, 0)), null);
  assert.deepEqual(place.zones([LEFT_TRAY]), []);
});

test('a zone lies on the table top, its corners in world coordinates', () => {
  const [zone] = new FreePlace(TRACY_REACH, PlanPose.of(1, 0, 0, 0)).zones([LEFT_TRAY]);
  assert.equal(zone.identifier, 'left_tray');
  assert.equal(zone.corners.length, 4);
  close(zone.corners[0].slice(0, 2), [1.6, 0.3]);
  close(zone.corners[2].slice(0, 2), [1.8, 0.5]);
  assert.ok(zone.corners.every((corner) => corner[2] > 0.88 && corner[2] < 0.89));
});
