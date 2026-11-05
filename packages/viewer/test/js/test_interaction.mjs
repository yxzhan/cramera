import { test } from 'node:test';
import assert from 'node:assert/strict';

import { KeyboardShortcuts, PlaybackActions, ShortcutKey } from '../../src/cramera/web/app/keyboard-shortcuts.js';
import { DetailsPanel } from '../../src/cramera/web/panels/details-panel.js';
import { LayerObject, LayersPanel, ViewSwitch } from '../../src/cramera/web/panels/layers-panel.js';
import { SelectionKey, SelectionKind } from '../../src/cramera/web/protocol.js';
import { selectionOf } from '../../src/cramera/web/scene/scene-picking.js';
import { FakeDocument } from './support/fake-document.mjs';

// %% selection

/**
 * @param {Object} properties The node's own properties.
 * @param {Object|null} parent Its parent.
 * @returns {Object} A scene graph node.
 */
function node(properties, parent = null) {
  return { userData: {}, ...properties, parent };
}

test('a mesh selects the innermost link it belongs to and that link\'s model', () => {
  const robot = node({ isURDFRobot: true, name: 'pr2' });
  const arm = node({ isURDFLink: true, urdfName: 'l_upper_arm_link' }, robot);
  const hand = node({ isURDFLink: true, urdfName: 'l_gripper_palm_link' }, arm);
  const mesh = node({}, node({}, hand));
  assert.deepEqual(selectionOf(mesh), {
    [SelectionKey.KIND]: SelectionKind.LINK,
    [SelectionKey.MODEL]: 'pr2',
    [SelectionKey.NAME]: 'l_gripper_palm_link',
  });
});

test('a mesh of a free object selects the object by its key', () => {
  const holder = node({ userData: { objectKey: 'milk.stl' } });
  assert.deepEqual(selectionOf(node({}, holder)), { [SelectionKey.KIND]: SelectionKind.OBJECT, [SelectionKey.NAME]: 'milk.stl' });
});

test('a mesh outside links and objects selects nothing', () => {
  assert.equal(selectionOf(node({}, node({}))), null);
});

test('details show the selection above the scene facts and clear it again', () => {
  const details = new DetailsPanel(new FakeDocument());
  details.showSelection({ [SelectionKey.KIND]: SelectionKind.LINK, [SelectionKey.MODEL]: 'pr2', [SelectionKey.NAME]: 'base_link' });
  assert.match(details.panel.element.innerText, /Selected.*Linkbase_link.*Modelpr2/s);
  details.showSelection({ [SelectionKey.KIND]: SelectionKind.OBJECT, [SelectionKey.NAME]: 'cup' });
  assert.match(details.panel.element.innerText, /Objectcup/);
  details.showSelection(null);
  assert.doesNotMatch(details.panel.element.innerText, /Selected/);
});

// %% layers

test('layer switches show and change their state', () => {
  let occlusion = false;
  const document = new FakeDocument();
  const panel = new LayersPanel(document.createElement('div'), [new ViewSwitch('Ambient occlusion', () => occlusion, (enabled) => { occlusion = enabled; })], () => {});
  const [title, row] = panel.element.children;
  assert.equal(title.children[0].textContent, 'Layers');
  const input = row.children[0];
  assert.equal(row.children[1].textContent, 'Ambient occlusion');
  assert.equal(input.checked, false);
  input.checked = true;
  input.dispatch('change');
  assert.equal(occlusion, true);
  occlusion = false;
  panel.refresh();
  assert.equal(input.checked, false);
});

test('the layers fold down to their title and open again', () => {
  const panel = new LayersPanel(new FakeDocument().createElement('div'), [], () => {});
  const fold = panel.element.children[0].children[1];
  assert.equal(fold.getAttribute('aria-expanded'), 'true');
  fold.dispatch('click');
  assert.equal(panel.element.classList.contains('folded'), true);
  assert.equal(fold.getAttribute('aria-expanded'), 'false');
  fold.dispatch('click');
  assert.equal(panel.element.classList.contains('folded'), false);
});

test('the scene\'s objects are listed and light up when picked', () => {
  const picked = [];
  const panel = new LayersPanel(new FakeDocument().createElement('div'), [], (key) => picked.push(key));
  panel.showObjects([new LayerObject('milk_1', 'milk'), new LayerObject('cup_2', 'cup')]);
  const list = panel.element.children.at(-1);
  const buttons = list.children.map((item) => item.children[0]);
  assert.deepEqual(buttons.map((button) => button.textContent), ['milk', 'cup']);
  buttons[1].dispatch('click');
  assert.deepEqual(picked, ['cup_2']);
});

// %% keyboard

test('shortcuts play, step, jump and focus, but not while a form control has focus', () => {
  const document = new FakeDocument();
  const calls = [];
  const shortcuts = new KeyboardShortcuts(document.documentElement, new PlaybackActions(() => calls.push('toggle'), (frames) => calls.push(['step', frames]), (last) => calls.push(['jump', last]), () => calls.push('focus')));
  const press = (key, target = document.body, modifiers = {}) => document.documentElement.dispatch('keydown', { key, target, ...modifiers });
  press(ShortcutKey.PLAY_PAUSE);
  press(ShortcutKey.PREVIOUS_FRAME);
  press(ShortcutKey.NEXT_FRAME);
  press(ShortcutKey.FIRST_FRAME);
  press(ShortcutKey.LAST_FRAME);
  press(ShortcutKey.FOCUS_SELECTION);
  press('F');
  press('x');
  press(ShortcutKey.PLAY_PAUSE, document.createElement('select'));
  press(ShortcutKey.NEXT_FRAME, document.body, { ctrlKey: true });
  assert.deepEqual(calls, ['toggle', ['step', -1], ['step', 1], ['jump', false], ['jump', true], 'focus', 'focus']);
  shortcuts.dispose();
  press(ShortcutKey.PLAY_PAUSE);
  assert.equal(calls.length, 7);
});

// %% highlight

import * as THREE from 'three';
import { ownBounds } from '../../src/cramera/web/scene/scene-picking.js';

test('a link\'s box encloses its own meshes, not the links below it', () => {
  const link = Object.assign(new THREE.Group(), { isURDFLink: true });
  link.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));
  const joint = Object.assign(new THREE.Group(), { isURDFJoint: true });
  joint.position.set(5, 0, 0);
  joint.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));
  link.add(joint);
  const box = ownBounds(link);
  assert.deepEqual(box.min.toArray(), [-0.5, -0.5, -0.5]);
  assert.deepEqual(box.max.toArray(), [0.5, 0.5, 0.5]);
});

// %% highlights

import { boxedGroups, GLOW_INTENSITY, HIGHLIGHT_COLOR, highlightedParts, SceneHighlights } from '../../src/cramera/web/scene/scene-highlights.js';

test('highlight keys name links by their URDF name and objects by their key', () => {
  const content = new THREE.Group();
  const robot = Object.assign(new THREE.Group(), { isURDFRobot: true });
  const link = Object.assign(new THREE.Group(), { isURDFLink: true, urdfName: 'l_gripper_palm_link' });
  const other = Object.assign(new THREE.Group(), { isURDFLink: true, urdfName: 'base_link' });
  const cup = new THREE.Group();
  cup.userData.objectKey = 'cup';
  robot.add(link, other);
  content.add(robot, cup);
  assert.deepEqual(highlightedParts(content, ['cup', 'l_gripper_palm_link', 'missing']), [link, cup]);
  assert.deepEqual(highlightedParts(content, []), []);
});

/**
 * @returns {Object} A robot with an arm of two links, each with a mesh, and a free
 *   object with a mesh, in a view's content.
 */
function highlightedScene() {
  const content = new THREE.Group();
  const robot = Object.assign(new THREE.Group(), { isURDFRobot: true });
  const shoulder = Object.assign(new THREE.Group(), { isURDFLink: true, urdfName: 'shoulder' });
  const elbow = Object.assign(new THREE.Group(), { isURDFLink: true, urdfName: 'elbow' });
  const joint = Object.assign(new THREE.Group(), { isURDFJoint: true });
  joint.position.set(2, 0, 0);
  const material = new THREE.MeshStandardMaterial({ color: 0x808080 });
  const shoulderMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
  const elbowMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
  shoulder.add(shoulderMesh, joint);
  joint.add(elbow);
  elbow.add(elbowMesh);
  robot.add(shoulder);
  const cup = new THREE.Group();
  cup.userData.objectKey = 'cup';
  const cupMesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material);
  cup.position.set(0, 5, 0);
  cup.add(cupMesh);
  content.add(robot, cup);
  const scene = new THREE.Scene();
  scene.add(content);
  const view = { content, scene, renders: 0, requestRender() { this.renders += 1; } };
  return { view, shoulder, elbow, cup, material, meshes: [shoulderMesh, elbowMesh, cupMesh] };
}

test('the links of one model share a box, and each free object has its own', () => {
  const { shoulder, elbow, cup } = highlightedScene();
  assert.deepEqual(boxedGroups([shoulder, cup, elbow]), [[shoulder, elbow], [cup]]);
});

test('highlighted meshes glow and get their own material back when cleared', () => {
  const { view, material, meshes } = highlightedScene();
  const highlights = new SceneHighlights(view);
  assert.equal(highlights.show(['shoulder', 'elbow', 'cup']), 3);
  for (const mesh of meshes) {
    assert.notEqual(mesh.material, material);
    assert.equal(mesh.material.emissive.getHex(), HIGHLIGHT_COLOR);
    assert.equal(mesh.material.emissiveIntensity, GLOW_INTENSITY);
  }
  const boxes = view.scene.children.filter((child) => child instanceof THREE.Box3Helper);
  assert.equal(boxes.length, 2, 'one box for the arm, one for the cup');
  assert.deepEqual(boxes[0].box.min.toArray(), [-0.5, -0.5, -0.5]);
  assert.deepEqual(boxes[0].box.max.toArray(), [2.5, 0.5, 0.5]);
  highlights.clear();
  for (const mesh of meshes) assert.equal(mesh.material, material);
  assert.deepEqual(view.scene.children.filter((child) => child instanceof THREE.Box3Helper), []);
});

test('a link\'s glow leaves the links below it as they are', () => {
  const { view, material, meshes } = highlightedScene();
  new SceneHighlights(view).show(['shoulder']);
  assert.notEqual(meshes[0].material, material);
  assert.equal(meshes[1].material, material);
});
