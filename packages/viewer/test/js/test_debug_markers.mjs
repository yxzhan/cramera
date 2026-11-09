import { test } from 'node:test';
import assert from 'node:assert/strict';

import * as THREE from 'three';

import { DebugMarkers, MarkerBuilder, MarkerDrawing, markerKey } from '../../src/cramera/web/scene/debug-markers.js';
import { MarkerKey, MarkerKind, MarkerOverlayKey } from '../../src/cramera/web/protocol.js';

const TOPIC = '/semworld/viz_marker';

/**
 * @param {Object} fields The fields that differ from a red box at the origin.
 * @returns {Object} A marker of the overlay.
 */
function marker(fields = {}) {
  return {
    [MarkerKey.TOPIC]: TOPIC,
    [MarkerKey.NAMESPACE]: 'cells',
    [MarkerKey.IDENTIFIER]: 0,
    [MarkerKey.KIND]: MarkerKind.CUBE,
    [MarkerKey.POSE]: [0, 0, 0, 0, 0, 0, 1],
    [MarkerKey.SCALE]: [0.1, 0.2, 0.3],
    [MarkerKey.COLOR]: '#ff0000',
    [MarkerKey.OPACITY]: 1,
    [MarkerKey.POINTS]: [],
    [MarkerKey.TEXT]: '',
    [MarkerKey.COLORS]: [],
    ...fields,
  };
}

/**
 * @param {Array<Object>} markers Markers.
 * @returns {Object} An overlay of them.
 */
function overlay(markers) {
  return { [MarkerOverlayKey.VERSION]: 1, [MarkerOverlayKey.MARKERS]: markers };
}

function fakeView() {
  return { scene: new THREE.Scene(), renders: 0, requestRender() { this.renders += 1; } };
}

function build(fields) {
  return new MarkerBuilder(null).build(marker(fields));
}

// %% one marker

test('a box stands at its pose with its size, colour and opacity', () => {
  const holder = build({ [MarkerKey.POSE]: [1, 2, 3, 0, 0, 0, 1], [MarkerKey.OPACITY]: 0.5 });
  const [box] = holder.children;
  assert.deepEqual(holder.position.toArray(), [1, 2, 3]);
  assert.deepEqual(box.scale.toArray(), [0.1, 0.2, 0.3]);
  assert.equal(`#${box.material.color.getHexString()}`, '#ff0000');
  assert.equal(box.material.transparent, true);
  assert.equal(box.material.opacity, 0.5);
});

test('a cylinder stands upright along z, as RViz draws it', () => {
  const holder = build({ [MarkerKey.KIND]: MarkerKind.CYLINDER, [MarkerKey.SCALE]: [0.1, 0.1, 0.5] });
  const size = new THREE.Box3().setFromObject(holder).getSize(new THREE.Vector3());
  assert.ok(Math.abs(size.z - 0.5) < 1e-9);
  assert.ok(Math.abs(size.y - 0.1) < 1e-9);
});

test('an arrow without points points along x, its head a share of its length', () => {
  const [shaft, head] = build({ [MarkerKey.KIND]: MarkerKind.ARROW, [MarkerKey.SCALE]: [1, 0.05, 0.1] }).children;
  const headLength = MarkerDrawing.HEAD_SHARE;
  assert.ok(Math.abs(shaft.scale.y - (1 - headLength)) < 1e-9);
  assert.equal(shaft.scale.x, 0.05);
  assert.ok(Math.abs(head.scale.y - headLength) < 1e-9);
  assert.equal(head.scale.x, 0.1);
  assert.ok(Math.abs(head.position.x - (1 - headLength / 2)) < 1e-9);
});

test('an arrow between two points takes its diameters and head length from its scale', () => {
  const [shaft, head] = build({
    [MarkerKey.KIND]: MarkerKind.ARROW,
    [MarkerKey.SCALE]: [0.02, 0.06, 0.1],
    [MarkerKey.POINTS]: [[0, 0, 0], [0, 0, 1]],
  }).children;
  assert.equal(shaft.scale.x, 0.02);
  assert.equal(head.scale.x, 0.06);
  assert.ok(Math.abs(head.scale.y - 0.1) < 1e-9);
  assert.ok(Math.abs(head.position.z - 0.95) < 1e-9);
});

test('a line strip joins its points one after the other, coloured by their own colours', () => {
  const [lines] = build({
    [MarkerKey.KIND]: MarkerKind.LINE_STRIP,
    [MarkerKey.POINTS]: [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
    [MarkerKey.COLORS]: ['#00ff00', '#0000ff', '#ffffff'],
  }).children;
  assert.equal(lines.count, 2);
  const color = new THREE.Color();
  lines.getColorAt(1, color);
  assert.equal(`#${color.getHexString()}`, '#0000ff');
});

test('a line list joins its points in pairs', () => {
  const [lines] = build({ [MarkerKey.KIND]: MarkerKind.LINE_LIST, [MarkerKey.POINTS]: [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0], [4, 0, 0]] }).children;
  assert.equal(lines.count, 2);
});

test('a box list draws one box per point', () => {
  const [boxes] = build({ [MarkerKey.KIND]: MarkerKind.CUBE_LIST, [MarkerKey.POINTS]: [[0, 0, 0], [1, 2, 3]] }).children;
  assert.equal(boxes.count, 2);
  const matrix = new THREE.Matrix4();
  boxes.getMatrixAt(1, matrix);
  assert.deepEqual(new THREE.Vector3().setFromMatrixPosition(matrix).toArray(), [1, 2, 3]);
});

test('points are dots as wide as the scale says', () => {
  const [points] = build({ [MarkerKey.KIND]: MarkerKind.POINTS, [MarkerKey.SCALE]: [0.03, 0.03, 0], [MarkerKey.POINTS]: [[0, 0, 0], [1, 0, 0]] }).children;
  assert.ok(points instanceof THREE.Points);
  assert.equal(points.material.size, 0.03);
  assert.equal(points.geometry.getAttribute('position').count, 2);
});

test('a text faces the camera at the height its scale gives', () => {
  const [text] = build({ [MarkerKey.KIND]: MarkerKind.TEXT, [MarkerKey.TEXT]: 'sink', [MarkerKey.SCALE]: [0, 0, 0.2] }).children;
  assert.ok(text instanceof THREE.Sprite);
  assert.equal(text.scale.y, 0.2);
});

test('a marker without points draws nothing instead of failing', () => {
  for (const kind of [MarkerKind.LINE_STRIP, MarkerKind.LINE_LIST, MarkerKind.CUBE_LIST, MarkerKind.SPHERE_LIST, MarkerKind.POINTS]) {
    assert.equal(build({ [MarkerKey.KIND]: kind }).children.length, 0);
  }
});

// %% every marker

test('markers are drawn beside the content, moved when they only move, and removed when gone', () => {
  const view = fakeView();
  const markers = new DebugMarkers(view);
  markers.show(overlay([marker(), marker({ [MarkerKey.IDENTIFIER]: 1 })]));
  assert.equal(markers.count, 2);
  assert.equal(markers.group.parent, view.scene);
  const first = markers.group.getObjectByName(markerKey(marker()));
  markers.show(overlay([marker({ [MarkerKey.POSE]: [0, 0, 1, 0, 0, 0, 1] })]));
  assert.equal(markers.count, 1);
  assert.equal(markers.group.getObjectByName(markerKey(marker())), first);
  assert.equal(first.position.z, 1);
  markers.show(overlay([marker({ [MarkerKey.COLOR]: '#00ff00' })]));
  assert.notEqual(markers.group.getObjectByName(markerKey(marker())), first);
  markers.clear();
  assert.equal(markers.count, 0);
});

test('a namespace can be hidden, and stays hidden as its markers change', () => {
  const markers = new DebugMarkers(fakeView());
  markers.show(overlay([marker(), marker({ [MarkerKey.NAMESPACE]: 'path', [MarkerKey.IDENTIFIER]: 1 })]));
  markers.setNamespaceShown(TOPIC, 'cells', false);
  markers.show(overlay([marker({ [MarkerKey.COLOR]: '#00ff00' }), marker({ [MarkerKey.NAMESPACE]: 'path', [MarkerKey.IDENTIFIER]: 1 })]));
  const visible = markers.group.children.filter((holder) => holder.visible).map((holder) => holder.name);
  assert.deepEqual(visible, [markerKey(marker({ [MarkerKey.NAMESPACE]: 'path', [MarkerKey.IDENTIFIER]: 1 }))]);
  assert.deepEqual(markers.namespaces().map(({ namespace, count, shown }) => [namespace, count, shown]), [['cells', 1, false], ['path', 1, true]]);
});

test('the markers can be switched off as a whole', () => {
  const markers = new DebugMarkers(fakeView());
  assert.equal(markers.shown, true);
  markers.setShown(false);
  assert.equal(markers.group.visible, false);
});

test('a marker following a body is moved by each state, once its overlay is drawn', () => {
  const markers = new DebugMarkers(fakeView());
  const following = marker({ [MarkerKey.IDENTIFIER]: 1 });
  markers.place(1, { 1: [2, 0, 0, 0, 0, 0, 1] });
  markers.show(overlay([marker(), following]));
  const holder = markers.group.getObjectByName(markerKey(following));
  assert.equal(holder.position.x, 2);
  markers.place(1, { 1: [3, 0, 0, 0, 0, 0, 1] });
  assert.equal(holder.position.x, 3);
  markers.place(2, { 1: [4, 0, 0, 0, 0, 0, 1] });
  assert.equal(holder.position.x, 3);
  assert.equal(markers.group.getObjectByName(markerKey(marker())).position.x, 0);
});
