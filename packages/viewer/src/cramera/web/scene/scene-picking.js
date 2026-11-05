/**
 * Selecting a link or a free object by clicking it in the 3D view.
 */

import * as THREE from 'three';

import { SelectionKey, SelectionKind, ViewerEvent } from '../protocol.js';

/**
 * How far, in pixels, the pointer may move between press and release for a click
 * rather than an orbit.
 */
const CLICK_TOLERANCE = 4;

/**
 * The colour of the box around the selection.
 */
const HIGHLIGHT_COLOR = 0x14b8a6;

// %% selections

/**
 * @param {THREE.Object3D} object The object a ray hit.
 * @returns {Object|null} The selection payload: the link and model the object
 *   belongs to, or the free object it belongs to, or null for neither.
 */
export function selectionOf(object) {
  let link = null;
  for (let current = object; current; current = current.parent) {
    if (current.userData?.objectKey !== undefined) {
      return { [SelectionKey.KIND]: SelectionKind.OBJECT, [SelectionKey.NAME]: current.userData.objectKey };
    }
    if (current.isURDFLink && link === null) link = current;
    if (current.isURDFRobot && link !== null) {
      return { [SelectionKey.KIND]: SelectionKind.LINK, [SelectionKey.MODEL]: current.name, [SelectionKey.NAME]: link.urdfName };
    }
  }
  return null;
}

/**
 * @param {THREE.Object3D} owner A link or a free object.
 * @returns {THREE.Box3} The box around its own meshes; the links below a link are left out.
 */
export function ownBounds(owner) {
  const box = new THREE.Box3();
  owner.updateWorldMatrix(true, true);
  const visit = (object) => {
    if (object !== owner && (object.isURDFLink || object.isURDFJoint)) return;
    if (object.isMesh) box.expandByObject(object);
    object.children.forEach(visit);
  };
  visit(owner);
  return box;
}

// %% picking

export class ScenePicking {
  /**
   * The view whose content is picked.
   * @type {import('./scene-view.js').SceneView}
   */
  #view;

  /**
   * The bus the selection is published on.
   * @type {import('../core/event-bus.js').EventBus}
   */
  #bus;

  /**
   * Where the pointer was pressed.
   * @type {{x: number, y: number}|null}
   */
  #pressed = null;

  /**
   * The box around the selected link or object.
   * @type {THREE.Box3Helper|null}
   */
  #highlight = null;

  /**
   * The selected link or object, whose box follows it.
   * @type {THREE.Object3D|null}
   */
  #selected = null;

  /**
   * Called with the box around the body a double click hit, empty when the hit belongs
   * to none, and the point hit.
   * @type {function(THREE.Box3, THREE.Vector3): void}
   */
  #focus;

  /**
   * The canvas listeners, to remove them again.
   * @type {Array<[string, function(PointerEvent): void]>}
   */
  #listeners;

  /**
   * @param {import('./scene-view.js').SceneView} view The view whose content is picked.
   * @param {import('../core/event-bus.js').EventBus} bus The bus the selection is published on.
   * @param {function(THREE.Box3, THREE.Vector3): void} focus Called with the box around the
   *   body a double click hit, empty when the hit belongs to none, and the point hit.
   */
  constructor(view, bus, focus = () => {}) {
    this.#view = view;
    this.#bus = bus;
    this.#focus = focus;
    this.#listeners = [
      ['pointerdown', (event) => { this.#pressed = { x: event.clientX, y: event.clientY }; }],
      ['pointerup', (event) => this.#release(event)],
      ['dblclick', (event) => this.#focusOn(event)],
    ];
    for (const [type, listener] of this.#listeners) view.renderer.domElement.addEventListener(type, listener);
  }

  /**
   * @param {PointerEvent} event The release.
   */
  #release(event) {
    const pressed = this.#pressed;
    this.#pressed = null;
    if (!pressed || Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) > CLICK_TOLERANCE) return;
    this.select(this.#hit(event)?.object ?? null);
  }

  /**
   * Select what a double click hit and move the camera onto it; a double click on
   * nothing leaves the selection and the camera as they are.
   * @param {MouseEvent} event The double click.
   */
  #focusOn(event) {
    const hit = this.#hit(event);
    if (!hit) return;
    this.select(hit.object);
    this.#focus(this.selectedBounds(), hit.point);
  }

  /**
   * @param {MouseEvent} event A click.
   * @returns {THREE.Intersection|null} The nearest visible content under the pointer, or null.
   */
  #hit(event) {
    const canvas = this.#view.renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - canvas.left) / canvas.width) * 2 - 1,
      -((event.clientY - canvas.top) / canvas.height) * 2 + 1,
    );
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(pointer, this.#view.camera);
    return raycaster.intersectObject(this.#view.content, true).find((candidate) => candidate.object.visible) ?? null;
  }

  /**
   * @returns {THREE.Box3} The box around the selected link or object; empty when nothing is selected.
   */
  selectedBounds() {
    return this.#selected ? ownBounds(this.#selected) : new THREE.Box3();
  }

  /**
   * Select what an object belongs to, or clear the selection.
   * @param {THREE.Object3D|null} object A picked object, or null.
   */
  select(object) {
    this.#highlight?.removeFromParent();
    this.#highlight?.dispose();
    this.#highlight = null;
    this.#selected = null;
    const selection = object ? selectionOf(object) : null;
    if (selection) {
      this.#selected = this.#owner(object);
      this.#highlight = new THREE.Box3Helper(ownBounds(this.#selected), HIGHLIGHT_COLOR);
      this.#view.scene.add(this.#highlight);
    }
    this.#view.requestRender();
    this.#bus.publish(ViewerEvent.ENTITY_SELECTED, selection);
  }

  /**
   * Keep the box around the selection as the selection moves.
   */
  update() {
    if (this.#highlight) this.#highlight.box.copy(ownBounds(this.#selected));
  }

  /**
   * @param {THREE.Object3D} object A picked object.
   * @returns {THREE.Object3D} The link or free object it belongs to.
   */
  #owner(object) {
    for (let current = object; current; current = current.parent) {
      if (current.isURDFLink || current.userData?.objectKey !== undefined) return current;
    }
    return object;
  }

  /**
   * Remove the listeners and the highlight.
   */
  dispose() {
    for (const [type, listener] of this.#listeners) this.#view.renderer.domElement.removeEventListener(type, listener);
    this.#highlight?.removeFromParent();
    this.#highlight?.dispose();
  }
}
