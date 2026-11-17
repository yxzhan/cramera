import { test } from 'node:test';
import assert from 'node:assert/strict';

import * as THREE from 'three';

import { ObjectPaint } from '../../src/cramera_plan_builder/web/builder/plan-scene.js';

// %% an object's colours

const RED = 0xcc1a1a;
const CUBE = 'aicor/objects/cubes/red_cube/model.urdf';

/**
 * @returns {THREE.Group} A library model's link as its URDF describes it, in its own colour.
 */
const redLink = () => {
  const link = new THREE.Group();
  link.add(new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.05, 0.05), new THREE.MeshPhongMaterial({ color: RED })));
  return link;
};

const meshes = (model) => {
  const found = [];
  model.traverse((child) => child.isMesh && found.push(child));
  return found;
};

test('a library model keeps the colours its URDF gives it', () => {
  const model = redLink();
  new ObjectPaint(CUBE, null, false).paint(model);
  assert.deepEqual(meshes(model).map((mesh) => mesh.material.color.getHex()), [RED]);
});

test('each copy of a library model is painted on its own materials', () => {
  const described = redLink();
  const copy = described.clone();
  new ObjectPaint(CUBE, null, false).paint(copy);
  meshes(copy)[0].material.color.setHex(0x00ff00);
  assert.equal(meshes(described)[0].material.color.getHex(), RED);
});

test('a colour the plan gives an object paints the whole model', () => {
  const model = redLink();
  new ObjectPaint(CUBE, '#1a40cc', false).paint(model);
  assert.deepEqual(meshes(model).map((mesh) => mesh.material.color.getHex()), [0x1a40cc]);
});

test('a put-down target shows see-through, not in the model colours', () => {
  const model = redLink();
  new ObjectPaint(CUBE, null, true).paint(model);
  const [mesh] = meshes(model);
  assert.notEqual(mesh.material.color.getHex(), RED);
  assert.equal(mesh.material.transparent, true);
});

test('a mesh without a description is drawn in the default object colour', () => {
  const model = redLink();
  const paint = new ObjectPaint('milk.stl', null, false);
  paint.paint(model);
  assert.deepEqual(meshes(model).map((mesh) => mesh.material.color.getHex()), [paint.material.color.getHex()]);
});
