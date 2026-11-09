/**
 * The debug markers a live publisher reads from ROS, drawn the way RViz draws a
 * `visualization_msgs/Marker`: arrows, boxes, spheres, cylinders, line strips and
 * lists, box and sphere lists, points and texts, at their poses in the world. A marker
 * that only moved is moved, not built again; one that follows a body is moved by every
 * state, and each topic's namespaces can be hidden.
 */

import * as THREE from 'three';

import { MarkerKey, MarkerKind, MarkerOverlayKey } from '../protocol.js';
import { applyPose } from './poses.js';

/**
 * How markers are drawn where a message leaves it open, as RViz draws them.
 */
export const MarkerDrawing = Object.freeze({
  /**
   * The share of an arrow's length its head takes when the message sets none.
   */
  HEAD_SHARE: 0.23,
  /**
   * The width of a line, a point or a text height, in metres, when the message's scale
   * is zero.
   */
  SMALLEST_SIZE: 0.01,
  /**
   * How many sides a round shape has.
   */
  ROUND_SEGMENTS: 24,
  /**
   * The roughness of a marker's surface, which is lit like the scene.
   */
  ROUGHNESS: 0.6,
  /**
   * The font size a text is drawn at before it is scaled to its height, in pixels.
   */
  TEXT_PIXELS: 64,
  /**
   * The width of the dark outline around a text's letters, in pixels.
   */
  TEXT_OUTLINE: 8,
});

/**
 * Shapes of unit size every marker shares; they are never disposed.
 */
const UnitShape = Object.freeze({
  BOX: new THREE.BoxGeometry(1, 1, 1),
  SPHERE: new THREE.SphereGeometry(0.5, MarkerDrawing.ROUND_SEGMENTS, MarkerDrawing.ROUND_SEGMENTS / 2),
  /**
   * A cylinder along y, centred on the origin.
   */
  CYLINDER: new THREE.CylinderGeometry(0.5, 0.5, 1, MarkerDrawing.ROUND_SEGMENTS),
  /**
   * A cylinder along z, as a `CYLINDER` marker stands.
   */
  UPRIGHT_CYLINDER: new THREE.CylinderGeometry(0.5, 0.5, 1, MarkerDrawing.ROUND_SEGMENTS).rotateX(Math.PI / 2),
  /**
   * A cone along y, centred on the origin, its tip at +y.
   */
  CONE: new THREE.ConeGeometry(0.5, 1, MarkerDrawing.ROUND_SEGMENTS),
});

const SHARED_SHAPES = new Set(Object.values(UnitShape));

/**
 * The direction the unit cylinder and cone point in.
 */
const UNIT_AXIS = new THREE.Vector3(0, 1, 0);

// %% one marker

/**
 * @param {Object} marker A marker of the overlay.
 * @returns {string} What names it among all markers: its topic, namespace and identity.
 */
export function markerKey(marker) {
  return [marker[MarkerKey.TOPIC], marker[MarkerKey.NAMESPACE], marker[MarkerKey.IDENTIFIER]].join('\u0000');
}

/**
 * @param {string} topic A marker topic.
 * @param {string} namespace A namespace of the topic.
 * @returns {string} What names the namespace among all topics'.
 */
export function namespaceKey(topic, namespace) {
  return `${topic}\u0000${namespace}`;
}

/**
 * @param {Object} marker A marker of the overlay.
 * @returns {string} Everything about the marker but its pose, so that a marker that only
 *   moved is told from one that changed.
 */
function shapeSignature(marker) {
  const { [MarkerKey.POSE]: pose, ...shape } = marker;
  return JSON.stringify(shape);
}

/**
 * @param {number} size A size from a message.
 * @returns {number} The size, or the smallest one drawn when the message left it zero.
 */
function drawnSize(size) {
  return size > 0 ? size : MarkerDrawing.SMALLEST_SIZE;
}

/**
 * Builds the drawing of one marker.
 */
export class MarkerBuilder {
  /**
   * The page, which draws texts; null draws them without letters.
   * @type {Document|null}
   */
  #document;

  /**
   * @param {Document|null} document The page, which draws texts.
   */
  constructor(document) {
    this.#document = document;
  }

  /**
   * @param {Object} marker A marker of the overlay.
   * @returns {THREE.Object3D} Its drawing at its pose; empty for a kind not drawn.
   */
  build(marker) {
    const holder = new THREE.Group();
    holder.name = markerKey(marker);
    applyPose(holder, marker[MarkerKey.POSE]);
    const scale = marker[MarkerKey.SCALE];
    const points = (marker[MarkerKey.POINTS] ?? []).map((point) => new THREE.Vector3(...point));
    const parts = {
      [MarkerKind.ARROW]: () => this.#arrow(marker, scale, points),
      [MarkerKind.CUBE]: () => [this.#solid(UnitShape.BOX, marker, scale)],
      [MarkerKind.SPHERE]: () => [this.#solid(UnitShape.SPHERE, marker, scale)],
      [MarkerKind.CYLINDER]: () => [this.#solid(UnitShape.UPRIGHT_CYLINDER, marker, scale)],
      [MarkerKind.LINE_STRIP]: () => [this.#lines(marker, scale, points, points.slice(0, -1).map((point, index) => [index, index + 1]))],
      [MarkerKind.LINE_LIST]: () => [this.#lines(marker, scale, points, Array.from({ length: Math.floor(points.length / 2) }, (_, pair) => [2 * pair, 2 * pair + 1]))],
      [MarkerKind.CUBE_LIST]: () => [this.#list(UnitShape.BOX, marker, scale, points)],
      [MarkerKind.SPHERE_LIST]: () => [this.#list(UnitShape.SPHERE, marker, scale, points)],
      [MarkerKind.POINTS]: () => [this.#points(marker, scale, points)],
      [MarkerKind.TEXT]: () => [this.#text(marker, scale)],
    }[marker[MarkerKey.KIND]];
    if (parts) holder.add(...parts().filter(Boolean));
    return holder;
  }

  /**
   * @param {Object} marker A marker.
   * @param {boolean} ownColors Whether its parts are coloured one by one.
   * @returns {THREE.MeshStandardMaterial} Its surface, lit like the scene.
   */
  #surface(marker, ownColors = false) {
    const opacity = marker[MarkerKey.OPACITY] ?? 1;
    return new THREE.MeshStandardMaterial({
      color: ownColors ? 0xffffff : marker[MarkerKey.COLOR],
      roughness: MarkerDrawing.ROUGHNESS,
      metalness: 0,
      transparent: opacity < 1,
      opacity,
      depthWrite: opacity >= 1,
    });
  }

  /**
   * @param {Object} marker A marker.
   * @param {number} index A point's position.
   * @returns {THREE.Color} The point's own colour, or the marker's.
   */
  #pointColor(marker, index) {
    return new THREE.Color(marker[MarkerKey.COLORS]?.[index] ?? marker[MarkerKey.COLOR]);
  }

  /**
   * @param {THREE.BufferGeometry} shape A unit shape.
   * @param {Object} marker A marker.
   * @param {Array<number>} scale Its scale.
   * @returns {THREE.Mesh} The shape scaled to the marker.
   */
  #solid(shape, marker, scale) {
    const mesh = new THREE.Mesh(shape, this.#surface(marker));
    mesh.scale.set(...scale);
    return mesh;
  }

  /**
   * An arrow along x as long as `scale[0]`, or from its first point to its second, with
   * the diameters and head length the message gives in the order RViz reads them.
   * @param {Object} marker A marker.
   * @param {Array<number>} scale Its scale.
   * @param {Array<THREE.Vector3>} points Its points.
   * @returns {Array<THREE.Mesh>} Its shaft and head.
   */
  #arrow(marker, scale, points) {
    const between = points.length >= 2;
    const start = between ? points[0] : new THREE.Vector3();
    const end = between ? points[1] : new THREE.Vector3(scale[0], 0, 0);
    const [shaftDiameter, headDiameter, setHead] = between ? scale : [scale[1], scale[2], 0];
    const length = start.distanceTo(end);
    if (length === 0) return [];
    const headLength = Math.min(length, setHead > 0 ? setHead : MarkerDrawing.HEAD_SHARE * length);
    const neck = end.clone().sub(start).setLength(length - headLength).add(start);
    const surface = this.#surface(marker);
    const head = new THREE.Mesh(UnitShape.CONE, surface);
    placeAlong(head, neck, end, drawnSize(headDiameter));
    if (headLength === length) return [head];
    const shaft = new THREE.Mesh(UnitShape.CYLINDER, surface);
    placeAlong(shaft, start, neck, drawnSize(shaftDiameter));
    return [shaft, head];
  }

  /**
   * @param {Object} marker A line strip or list.
   * @param {Array<number>} scale Its scale; the first is the line width.
   * @param {Array<THREE.Vector3>} points Its points.
   * @param {Array<Array<number>>} segments The points each segment joins.
   * @returns {THREE.InstancedMesh|null} One cylinder per segment, coloured by its first
   *   point; null without segments.
   */
  #lines(marker, scale, points, segments) {
    if (!segments.length) return null;
    const lines = new THREE.InstancedMesh(UnitShape.CYLINDER, this.#surface(marker, true), segments.length);
    const segment = new THREE.Object3D();
    segments.forEach(([from, to], index) => {
      placeAlong(segment, points[from], points[to], drawnSize(scale[0]));
      segment.updateMatrix();
      lines.setMatrixAt(index, segment.matrix);
      lines.setColorAt(index, this.#pointColor(marker, from));
    });
    return lines;
  }

  /**
   * @param {THREE.BufferGeometry} shape A unit shape.
   * @param {Object} marker A box or sphere list.
   * @param {Array<number>} scale The size of each.
   * @param {Array<THREE.Vector3>} points Where each stands.
   * @returns {THREE.InstancedMesh|null} One shape per point; null without points.
   */
  #list(shape, marker, scale, points) {
    if (!points.length) return null;
    const list = new THREE.InstancedMesh(shape, this.#surface(marker, true), points.length);
    const size = new THREE.Vector3(...scale);
    const unrotated = new THREE.Quaternion();
    const matrix = new THREE.Matrix4();
    points.forEach((point, index) => {
      list.setMatrixAt(index, matrix.compose(point, unrotated, size));
      list.setColorAt(index, this.#pointColor(marker, index));
    });
    return list;
  }

  /**
   * @param {Object} marker A points marker.
   * @param {Array<number>} scale Its scale; the first is a point's width.
   * @param {Array<THREE.Vector3>} points Its points.
   * @returns {THREE.Points|null} A square dot per point; null without points.
   */
  #points(marker, scale, points) {
    if (!points.length) return null;
    const geometry = new THREE.BufferGeometry().setFromPoints(points);
    const colors = points.flatMap((_, index) => this.#pointColor(marker, index).toArray());
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const opacity = marker[MarkerKey.OPACITY] ?? 1;
    return new THREE.Points(geometry, new THREE.PointsMaterial({ size: drawnSize(scale[0]), vertexColors: true, transparent: opacity < 1, opacity }));
  }

  /**
   * @param {Object} marker A text marker.
   * @param {Array<number>} scale Its scale; the third is the height of a capital letter.
   * @returns {THREE.Sprite} The text, always facing the camera.
   */
  #text(marker, scale) {
    const height = drawnSize(scale[2]);
    const texture = this.#textTexture(marker[MarkerKey.TEXT] ?? '', marker[MarkerKey.COLOR]);
    const opacity = marker[MarkerKey.OPACITY] ?? 1;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, color: texture ? 0xffffff : marker[MarkerKey.COLOR], transparent: true, opacity, depthWrite: false }));
    const aspect = texture ? texture.image.width / texture.image.height : 1;
    sprite.scale.set(height * aspect, height, 1);
    return sprite;
  }

  /**
   * @param {string} text A text.
   * @param {string} color Its colour.
   * @returns {THREE.CanvasTexture|null} The text in its colour with a dark outline, or
   *   null where the page cannot draw.
   */
  #textTexture(text, color) {
    const canvas = this.#document?.createElement('canvas');
    const context = canvas?.getContext?.('2d');
    if (!context) return null;
    const font = `600 ${MarkerDrawing.TEXT_PIXELS}px system-ui, sans-serif`;
    context.font = font;
    const lines = text.split('\n');
    const margin = MarkerDrawing.TEXT_OUTLINE;
    const lineHeight = MarkerDrawing.TEXT_PIXELS * 1.2;
    canvas.width = Math.ceil(Math.max(1, ...lines.map((line) => context.measureText(line).width)) + 2 * margin);
    canvas.height = Math.ceil(lines.length * lineHeight + 2 * margin);
    context.font = font;
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.lineJoin = 'round';
    context.lineWidth = MarkerDrawing.TEXT_OUTLINE;
    context.strokeStyle = 'rgba(0, 0, 0, 0.75)';
    context.fillStyle = color;
    lines.forEach((line, index) => {
      const y = margin + (index + 0.5) * lineHeight;
      context.strokeText(line, canvas.width / 2, y);
      context.fillText(line, canvas.width / 2, y);
    });
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    return texture;
  }
}

/**
 * Stretch a unit shape along y between two points.
 * @param {THREE.Object3D} part The part with a unit shape.
 * @param {THREE.Vector3} from Where it starts.
 * @param {THREE.Vector3} to Where it ends.
 * @param {number} diameter How thick it is.
 */
function placeAlong(part, from, to, diameter) {
  const along = to.clone().sub(from);
  const length = along.length();
  part.position.copy(from).addScaledVector(along, 0.5);
  part.quaternion.setFromUnitVectors(UNIT_AXIS, length > 0 ? along.divideScalar(length) : UNIT_AXIS);
  part.scale.set(diameter, length, diameter);
}

/**
 * Free what a drawing holds, but the shapes all markers share.
 * @param {THREE.Object3D} holder A marker's drawing.
 */
function dispose(holder) {
  holder.traverse((part) => {
    if (part.geometry && !SHARED_SHAPES.has(part.geometry)) part.geometry.dispose();
    part.material?.map?.dispose();
    part.material?.dispose();
    part.dispose?.();
  });
}

// %% every marker

/**
 * One namespace of a topic, as the markers controls list it.
 */
export class MarkerNamespace {
  /**
   * @param {string} topic The topic.
   * @param {string} namespace The namespace.
   * @param {number} count How many markers it has.
   * @param {boolean} shown Whether they are drawn.
   */
  constructor(topic, namespace, count, shown) {
    /**
     * The topic.
     * @type {string}
     */
    this.topic = topic;
    /**
     * The namespace.
     * @type {string}
     */
    this.namespace = namespace;
    /**
     * How many markers it has.
     * @type {number}
     */
    this.count = count;
    /**
     * Whether they are drawn.
     * @type {boolean}
     */
    this.shown = shown;
  }
}

export class DebugMarkers {
  /**
   * Every marker's drawing.
   * @type {THREE.Group}
   */
  group = new THREE.Group();

  /**
   * The drawn markers, by `markerKey`, each with its shape signature and namespace.
   * @type {Map<string, {signature: string, namespace: string, holder: THREE.Object3D}>}
   */
  #drawn = new Map();

  /**
   * The namespaces hidden, by `namespaceKey`; kept when the overlay changes.
   * @type {Set<string>}
   */
  #hidden = new Set();

  /**
   * The markers of the overlay shown.
   * @type {Array<Object>}
   */
  #markers = [];

  /**
   * The version of the overlay shown, or null before one is.
   * @type {number|null}
   */
  #version = null;

  /**
   * The newest poses of the markers that follow a body, with the version of the overlay
   * whose markers they place.
   * @type {{version: number|null, poses: Object<string, Array<number>>}}
   */
  #placement = { version: null, poses: {} };

  /**
   * Builds the markers.
   * @type {MarkerBuilder}
   */
  #builder;

  /**
   * The 3D view.
   * @type {import('./scene-view.js').SceneView}
   */
  #view;

  /**
   * @param {import('./scene-view.js').SceneView} view The 3D view, beside whose content
   *   the markers are drawn, so they are neither picked nor framed.
   * @param {Document|null} document The page, which draws texts.
   */
  constructor(view, document = null) {
    this.#view = view;
    this.#builder = new MarkerBuilder(document);
    this.group.name = 'debug markers';
    view.scene.add(this.group);
  }

  /**
   * @returns {boolean} Whether the markers are drawn.
   */
  get shown() {
    return this.group.visible;
  }

  /**
   * @param {boolean} shown Whether to draw the markers.
   */
  setShown(shown) {
    this.group.visible = shown;
    this.#view.requestRender();
  }

  /**
   * Draw an overlay: build the markers that are new or changed, move those that only
   * moved, and remove the ones it no longer has.
   * @param {Object} overlay A marker overlay.
   */
  show(overlay) {
    this.#markers = overlay[MarkerOverlayKey.MARKERS] ?? [];
    this.#version = overlay[MarkerOverlayKey.VERSION] ?? null;
    const kept = new Set();
    for (const marker of this.#markers) {
      const key = markerKey(marker);
      const signature = shapeSignature(marker);
      kept.add(key);
      const drawn = this.#drawn.get(key);
      if (drawn?.signature === signature) {
        applyPose(drawn.holder, marker[MarkerKey.POSE]);
        continue;
      }
      if (drawn) this.#remove(key);
      const holder = this.#builder.build(marker);
      const namespace = namespaceKey(marker[MarkerKey.TOPIC], marker[MarkerKey.NAMESPACE]);
      holder.visible = !this.#hidden.has(namespace);
      this.group.add(holder);
      this.#drawn.set(key, { signature, namespace, holder });
    }
    for (const key of [...this.#drawn.keys()]) if (!kept.has(key)) this.#remove(key);
    this.#applyPlacement();
    this.#view.requestRender();
  }

  /**
   * Move the markers that follow a body to where a state has them; poses for an overlay
   * not drawn yet wait for it.
   * @param {number} version The version of the overlay the poses place.
   * @param {Object<string, Array<number>>} poses Poses by the marker's position among
   *   the overlay's markers.
   */
  place(version, poses) {
    this.#placement = { version, poses };
    if (this.#applyPlacement()) this.#view.requestRender();
  }

  /**
   * @returns {boolean} Whether the newest poses placed the overlay shown.
   */
  #applyPlacement() {
    const { version, poses } = this.#placement;
    if (version === null || version !== this.#version) return false;
    for (const [position, pose] of Object.entries(poses)) {
      const marker = this.#markers[Number(position)];
      const drawn = marker && this.#drawn.get(markerKey(marker));
      if (drawn) applyPose(drawn.holder, pose);
    }
    return true;
  }

  /**
   * Remove every marker, as when the publisher is left.
   */
  clear() {
    this.show({ [MarkerOverlayKey.MARKERS]: [] });
  }

  /**
   * @returns {number} How many markers are drawn.
   */
  get count() {
    return this.#drawn.size;
  }

  /**
   * @returns {Array<MarkerNamespace>} The namespaces of the overlay shown, by topic and
   *   name.
   */
  namespaces() {
    const counts = new Map();
    for (const marker of this.#markers) {
      const key = namespaceKey(marker[MarkerKey.TOPIC], marker[MarkerKey.NAMESPACE]);
      const entry = counts.get(key) ?? { topic: marker[MarkerKey.TOPIC], namespace: marker[MarkerKey.NAMESPACE], count: 0 };
      entry.count += 1;
      counts.set(key, entry);
    }
    return [...counts.entries()]
      .sort(([first], [second]) => first.localeCompare(second))
      .map(([key, { topic, namespace, count }]) => new MarkerNamespace(topic, namespace, count, !this.#hidden.has(key)));
  }

  /**
   * @param {string} topic A topic.
   * @param {string} namespace One of its namespaces.
   * @param {boolean} shown Whether its markers are drawn.
   */
  setNamespaceShown(topic, namespace, shown) {
    const key = namespaceKey(topic, namespace);
    if (shown) this.#hidden.delete(key);
    else this.#hidden.add(key);
    for (const drawn of this.#drawn.values()) if (drawn.namespace === key) drawn.holder.visible = shown;
    this.#view.requestRender();
  }

  /**
   * @param {string} key A drawn marker's key.
   */
  #remove(key) {
    const { holder } = this.#drawn.get(key);
    holder.removeFromParent();
    dispose(holder);
    this.#drawn.delete(key);
  }
}
