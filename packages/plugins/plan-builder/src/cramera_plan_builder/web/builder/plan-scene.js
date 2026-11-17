/**
 * The builder's 3D view: the environment bundle as the viewer shows it, and over it the
 * plan's robots as their models, at rest or in the posture a live world reports, objects and the edited robot's targets as
 * markers that are picked by a click and moved or turned with handles, the robot
 * profile's zones on the table, and a map of the world's axes.
 *
 * A free-place marker slides over the surface below it and turns about the vertical
 * only; while it is dragged, the page tells whether an arm may reach it, which tints it
 * green or red.
 */

import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';
import URDFLoader from 'urdf-loader';

import { PerceptionReportKey, PlannedObjectKey, PlannedRobotKey, PlanStepKey, ReportedObjectKey, ReportedObjectVerdict } from '../../../protocol.js';
import { Bounds } from '../../../scene/bounds.js';
import { MeshLoading } from '../../../scene/mesh-loading.js';
import { SceneLighting } from '../../../scene/scene-lighting.js';
import { SceneLoader } from '../../../scene/scene-loader.js';
import { SceneView } from '../../../scene/scene-view.js';
import { PlanPose } from './poses.js';
import { SceneAxes } from './scene-axes.js';

/**
 * What a marker stands for.
 */
export const MarkerKind = Object.freeze({ ROBOT: 'robot', OBJECT: 'object', TARGET: 'target' });

/**
 * What the handles change.
 */
export const TransformMode = Object.freeze({ MOVE: 'translate', TURN: 'rotate' });

/**
 * What separates the namespaces of a live joint's prefixed name from the joint's name.
 */
const JOINT_NAMESPACE_SEPARATOR = '/';

/**
 * The suffix of an object library model's description.
 */
const DESCRIPTION_SUFFIX = '.urdf';

/**
 * The region shown on an empty floor, in metres.
 */
const FLOOR_REGION = new Bounds([-1, -1, 0], [3, 3, 1.5]);

/**
 * The sizes of the markers, in metres.
 */
const MarkerSize = Object.freeze({
  ROBOT_RADIUS: 0.35,
  ROBOT_HEIGHT: 0.04,
  ARROW_LENGTH: 0.3,
  ARROW_RADIUS: 0.08,
  TARGET_RADIUS: 0.22,
  TARGET_TUBE: 0.025,
  PLACEHOLDER: 0.06,
  LABEL_LIFT: 0.3,
});

/**
 * The height of a robot's name label as a share of the view's height; the label keeps
 * its size on the screen however far the camera is.
 */
const LABEL_SCREEN_HEIGHT = 0.035;

/**
 * How a robot's name label is drawn: the canvas's font size and padding in pixels, and
 * its colours.
 */
const RobotLabel = Object.freeze({
  FONT_SIZE: 48,
  PADDING: 16,
  FONT: '600 48px system-ui, sans-serif',
  TEXT: '#f8fafc',
  BACKGROUND: 'rgba(15, 23, 42, 0.82)',
  EDITED_BACKGROUND: 'rgba(13, 148, 136, 0.92)',
});

/**
 * The colours of the markers.
 */
const MarkerColor = Object.freeze({
  ROBOT: 0x0f766e,
  EDITED_ROBOT: 0x14b8a6,
  OBJECT: 0xd6dde6,
  TARGET: 0x7c3aed,
  PUT_DOWN: 0xf59e0b,
  SELECTION: 0x0ea5e9,
  REACHABLE: 0x16a34a,
  UNREACHABLE: 0xdc2626,
  ZONE: 0x0284c7,
  SEARCH_BOX: 0xf59e0b,
  GROUNDED: 0xffffff,
  TAKEN: 0x16a34a,
  TOO_FAR: 0xdc2626,
  LEFT: 0x94a3b8,
});

/**
 * The radius, in metres, of the mark where the plan was validated with an object.
 */
const GROUNDED_RADIUS = 0.012;

/**
 * The size, in metres, a reported object is drawn at at least along each axis.
 */
const SMALLEST_REPORTED = 0.01;

/**
 * How see-through a reported object is.
 */
const REPORTED_OPACITY = 0.45;

/**
 * How see-through a zone is.
 */
const ZONE_OPACITY = 0.18;

/**
 * How see-through a put-down target is.
 */
const TARGET_OPACITY = 0.45;

/**
 * How see-through a robot is whose mirrored posture is not live.
 */
const STALE_OPACITY = 0.35;

/**
 * Segments of the round markers.
 */
const SEGMENTS = 32;

/**
 * How far a pointer may travel between press and release for a click, in pixels.
 */
const CLICK_DISTANCE = 5;

/**
 * The primary mouse button.
 */
const PRIMARY_BUTTON = 0;

/**
 * The events of the handles and the canvas the view listens to.
 */
const ViewEvent = Object.freeze({
  DRAGGING_CHANGED: 'dragging-changed',
  CHANGED: 'change',
  OBJECT_CHANGED: 'objectChange',
  POINTER_DOWN: 'pointerdown',
  POINTER_UP: 'pointerup',
  CAMERA_MOVE_STARTED: 'start',
});

/**
 * The key a marker's selection is kept under in its object's user data.
 */
const SELECTION_DATA = 'planSelection';

// %% selection

/**
 * A marker the person picked: a robot, an object, or a pose field of a step.
 */
export class Selection {
  /**
   * @param {string} kind A `MarkerKind` value.
   * @param {string} identifier The identity of the robot, object or step.
   * @param {string|null=} field The pose field of a step's target, else null.
   */
  constructor(kind, identifier, field = null) {
    /**
     * What the marker stands for.
     * @type {string}
     */
    this.kind = kind;
    /**
     * The identity of the robot, object or step.
     * @type {string}
     */
    this.identifier = identifier;
    /**
     * The pose field of a step's target, else null.
     * @type {string|null}
     */
    this.field = field;
  }

  /**
   * @param {Selection|null} other Another selection.
   * @returns {boolean} Whether both pick the same marker.
   */
  equals(other) {
    return other !== null && other.key === this.key;
  }

  /**
   * @returns {string} The marker's key among all markers.
   */
  get key() {
    return this.field === null ? `${this.kind}:${this.identifier}` : `${this.kind}:${this.identifier}:${this.field}`;
  }
}

/**
 * Whether the view frames the scene once more when the first robot model is loaded: the
 * view is framed before the model is there, so it would show the disc standing in for
 * the robot, close up, unless the person moved the camera meanwhile.
 */
export class FirstFraming {
  /**
   * Whether the scene was framed on a loaded model, or the person moved the camera.
   * @type {boolean}
   */
  #settled = false;

  /**
   * Note that the person moved the camera, which no loaded model reframes then.
   */
  notePersonMoved() {
    this.#settled = true;
  }

  /**
   * @returns {boolean} Whether the view frames the scene now that a robot model is
   *   loaded: for the first model only, and only while the camera was not moved.
   */
  frameOnModelLoaded() {
    const frame = !this.#settled;
    this.#settled = true;
    return frame;
  }
}

/**
 * How an object's marker is coloured: a library model in the colours its URDF gives it,
 * unless the plan or a perception names one colour for the object, and a put-down target
 * see-through.
 */
export class ObjectPaint {
  /**
   * @param {string} model The object model's identifier.
   * @param {string|null} color The object's colour, or null for the model's own.
   * @param {boolean} ghost Whether the marker is a see-through put-down target.
   */
  constructor(model, color, ghost) {
    /**
     * Whether the model keeps the materials its URDF describes.
     * @type {boolean}
     */
    this.ownColors = !ghost && color === null && model.toLowerCase().endsWith(DESCRIPTION_SUFFIX);
    /**
     * Whether the marker is a see-through put-down target, which casts no shadow.
     * @type {boolean}
     */
    this.ghost = ghost;
    /**
     * The material of every mesh not in its own colours, and of the placeholder.
     * @type {THREE.MeshStandardMaterial}
     */
    this.material = new THREE.MeshStandardMaterial({
      color: ghost ? MarkerColor.PUT_DOWN : (color ?? MarkerColor.OBJECT),
      transparent: ghost,
      opacity: ghost ? TARGET_OPACITY : 1,
    });
  }

  /**
   * Colour a copy of the model's meshes; a mesh in its own colours gets a material of its
   * own, so tinting one marker leaves the other copies alone.
   * @param {THREE.Object3D} copy The copy.
   */
  paint(copy) {
    copy.traverse((child) => {
      if (!child.isMesh) return;
      child.material = this.ownColors ? child.material.clone() : this.material;
      child.castShadow = !this.ghost;
    });
  }
}

/**
 * What the view reports to the page.
 */
export class PlanSceneListeners {
  /**
   * @param {function(Selection|null): void} onPick Called when a click picks a marker or
   *   nothing.
   * @param {function(Selection, Array<number>): void} onPlace Called when a handle drag
   *   ends, with the marker's new pose.
   * @param {function(Error): void} onError Called when the environment or a mesh cannot
   *   be shown.
   * @param {function(Selection, Array<number>): ({pose: Array<number>, reachable: (boolean|null)}|null)=} onMove
   *   Called while a free-place marker is dragged, with its pose; answers where it
   *   rests and whether an arm may reach it, or null to leave it as dragged.
   */
  constructor(onPick, onPlace, onError, onMove = () => null) {
    /**
     * Called when a click picks a marker or nothing.
     * @type {function(Selection|null): void}
     */
    this.onPick = onPick;
    /**
     * Called when a handle drag ends.
     * @type {function(Selection, Array<number>): void}
     */
    this.onPlace = onPlace;
    /**
     * Called when the environment or a mesh cannot be shown.
     * @type {function(Error): void}
     */
    this.onError = onError;
    /**
     * Called while a free-place marker is dragged.
     * @type {function(Selection, Array<number>): ({pose: Array<number>, reachable: (boolean|null)}|null)}
     */
    this.onMove = onMove;
  }
}

/**
 * What the page lays over the plan's markers: the zones on the table and whether an
 * arm may reach each free-place marker.
 */
export class SceneOverlay {
  /**
   * @param {Array<{identifier: string, label: string, corners: Array<Array<number>>}>} zones
   *   The zones' corners in world coordinates.
   * @param {Map<string, boolean|null>} reachable Whether an arm may reach each
   *   free-place marker, by its selection's key; null while it is not known.
   * @param {Object|null} perception The `PerceptionReport` whose box, validated spot
   *   and reported objects are drawn, or null for none.
   */
  constructor(zones = [], reachable = new Map(), perception = null) {
    /**
     * The zones' corners in world coordinates.
     * @type {Array<{identifier: string, label: string, corners: Array<Array<number>>}>}
     */
    this.zones = zones;
    /**
     * Whether an arm may reach each free-place marker, by its selection's key.
     * @type {Map<string, boolean|null>}
     */
    this.reachable = reachable;
    /**
     * The perception report drawn, or null.
     * @type {Object|null}
     */
    this.perception = perception;
  }
}

// %% view

export class PlanScene {
  /**
   * The viewer's 3D view.
   * @type {SceneView}
   */
  #view;

  /**
   * The lights of the view.
   * @type {SceneLighting}
   */
  #lighting = new SceneLighting();

  /**
   * Loads the environment bundle.
   * @type {SceneLoader}
   */
  #loader;

  /**
   * The shown environment, or null.
   * @type {import('../../../scene/scene-loader.js').LoadedScene|null}
   */
  #environment = null;

  /**
   * Cancels loading an environment another one replaced.
   * @type {AbortController|null}
   */
  #loading = null;

  /**
   * The markers, above the environment.
   * @type {THREE.Group}
   */
  #markers = new THREE.Group();

  /**
   * Each marker by its selection's key.
   * @type {Map<string, THREE.Object3D>}
   */
  #byKey = new Map();

  /**
   * The zones on the table, below the markers.
   * @type {THREE.Group}
   */
  #zones = new THREE.Group();

  /**
   * The zones shown, as JSON, so they are drawn again only when they change.
   * @type {string}
   */
  #zoneSignature = '';

  /**
   * The box a detection looked in, where the plan was validated with its object and
   * every object perception reported for it.
   * @type {THREE.Group}
   */
  #perception = new THREE.Group();

  /**
   * The perception report drawn, as JSON, so it is drawn again only when it changes.
   * @type {string}
   */
  #perceptionSignature = 'null';

  /**
   * Each object model's mesh once loaded, by model, copied for every object.
   * @type {Map<string, Promise<THREE.Object3D>>}
   */
  #meshes = new Map();

  /**
   * The URL of an object model's mesh.
   * @type {function(string): string}
   */
  #objectUrl;

  /**
   * Each robot model's links at rest once loaded, by model, copied for every robot.
   * @type {Map<string, Promise<THREE.Object3D>>}
   */
  #robotModels = new Map();

  /**
   * Whether a loaded robot model frames the view once more.
   * @type {FirstFraming}
   */
  #firstFraming = new FirstFraming();

  /**
   * The URL of a robot model's URDF.
   * @type {function(string): string}
   */
  #robotUrl;

  /**
   * What the view reports.
   * @type {PlanSceneListeners}
   */
  #listeners;

  /**
   * The world's axes at the origin and in the view's corner.
   * @type {SceneAxes}
   */
  #axes;

  /**
   * The handles on the selected marker.
   * @type {TransformControls}
   */
  #handles;

  /**
   * The selected marker, or null.
   * @type {Selection|null}
   */
  #selection = null;

  /**
   * What the handles change.
   * @type {string}
   */
  #mode = TransformMode.MOVE;

  /**
   * Where the pointer was pressed, or null.
   * @type {{x: number, y: number, onHandle: boolean}|null}
   */
  #press = null;

  /**
   * The canvas listeners, to remove them again.
   * @type {Array<[string, function(Event): void]>}
   */
  #canvasListeners = [];

  /**
   * @param {Document} document A document.
   * @returns {boolean} Whether its browser can show the 3D view.
   */
  static isAvailable(document) {
    return Boolean(document.createElement('canvas').getContext?.('webgl2'));
  }

  /**
   * @param {HTMLElement} container The element the view fills.
   * @param {import('../../../core/viewer-api.js').ViewerApi} api The viewer's routes.
   * @param {function(string): string} objectUrl The URL of an object model's mesh.
   * @param {function(string): string} robotUrl The URL of a robot model's URDF.
   * @param {PlanSceneListeners} listeners What the view reports.
   */
  constructor(container, api, objectUrl, robotUrl, listeners) {
    this.#objectUrl = objectUrl;
    this.#robotUrl = robotUrl;
    this.#listeners = listeners;
    this.#loader = new SceneLoader(api);
    this.#view = new SceneView(container);
    this.#view.scene.add(this.#lighting.group, this.#zones, this.#perception, this.#markers);
    this.#axes = new SceneAxes(this.#view, container.ownerDocument);
    this.#showFloor();
    this.#view.controls.addEventListener(ViewEvent.CAMERA_MOVE_STARTED, () => this.#firstFraming.notePersonMoved());
    this.#handles = new TransformControls(this.#view.camera, this.#view.renderer.domElement);
    this.#handles.addEventListener(ViewEvent.DRAGGING_CHANGED, (event) => {
      this.#view.controls.enabled = !event.value;
      if (!event.value) this.#placed();
    });
    this.#handles.addEventListener(ViewEvent.CHANGED, () => this.#view.requestRender());
    this.#handles.addEventListener(ViewEvent.OBJECT_CHANGED, () => this.#moved());
    this.#view.scene.add(this.#handles.getHelper());
    this.#listenOnCanvas(ViewEvent.POINTER_DOWN, (event) => {
      this.#press = { x: event.clientX, y: event.clientY, onHandle: this.#handles.axis !== null };
    });
    this.#listenOnCanvas(ViewEvent.POINTER_UP, (event) => this.#pick(event));
    this.#view.start(() => false);
  }

  /**
   * Show an environment bundle, or an empty floor.
   * @param {string|null} name The bundle's name, or null.
   * @returns {Promise<void>} Settles once the environment is shown or failed.
   */
  async showEnvironment(name) {
    this.#loading?.abort();
    this.#environment?.dispose();
    this.#environment = null;
    if (name === null) {
      this.#view.content.clear();
      this.#showFloor();
      return;
    }
    const loading = (this.#loading = new AbortController());
    const loaded = await this.#loader.load(name, this.#view, this.#lighting, loading.signal, () => {}).catch((error) => {
      if (!loading.signal.aborted) this.#listeners.onError(error);
      return null;
    });
    if (loaded && !loading.signal.aborted) this.#environment = loaded;
  }

  /**
   * Show the plan's robots and objects, and the edited robot's targets; poses the live
   * world reports win over the plan's while it runs.
   * @param {import('./plan-state.js').PlanState} state The plan.
   * @param {{objects: Map<string, Array<number>>, robots: Map<string, Array<number>>, postures: Map<string, Object<string, number>>=, colors: Map<string, string>=, stale: Set<string>=}=} live
   *   The live world's poses by the plan's identities, each robot's joint positions by
   *   prefixed joint name, the colors objects were perceived in, and the robots whose
   *   posture is not live, which are greyed.
   * @param {SceneOverlay=} overlay The zones and whether each free-place marker is
   *   reached.
   */
  show(state, live = { objects: new Map(), robots: new Map() }, overlay = new SceneOverlay()) {
    const postures = live.postures ?? new Map();
    const colors = live.colors ?? new Map();
    const stale = live.stale ?? new Set();
    this.#showZones(overlay.zones);
    this.#showPerception(overlay.perception);
    const wanted = new Map();
    const want = (selection, pose, signature, grounded, build, slides = false) => wanted.set(selection.key, { selection, pose, signature, grounded, build, slides });
    const edited = state.activeRobot()?.[PlannedRobotKey.IDENTIFIER];
    for (const robot of state.robots()) {
      const identifier = robot[PlannedRobotKey.IDENTIFIER];
      const isEdited = identifier === edited;
      const color = isEdited ? MarkerColor.EDITED_ROBOT : MarkerColor.ROBOT;
      const label = robot[PlannedRobotKey.LABEL];
      const model = robot[PlannedRobotKey.MODEL];
      want(new Selection(MarkerKind.ROBOT, identifier), live.robots.get(identifier) ?? robot[PlannedRobotKey.POSE], `${model}${color}${label}`, true, () => this.#robotMarker(model, color, label, isEdited));
    }
    for (const item of state.objects()) {
      const identifier = item[PlannedObjectKey.IDENTIFIER];
      const model = item[PlannedObjectKey.MODEL];
      const color = colors.get(identifier) ?? item[PlannedObjectKey.COLOR] ?? null;
      want(new Selection(MarkerKind.OBJECT, identifier), live.objects.get(identifier) ?? item[PlannedObjectKey.POSE], `${model}${color}`, false, () => this.#objectMarker(model, color, false));
    }
    for (const step of state.steps()) {
      for (const target of state.targets(step)) {
        const selection = new Selection(MarkerKind.TARGET, step[PlanStepKey.IDENTIFIER], target.field);
        const putDown = state.objects().find((item) => item[PlannedObjectKey.IDENTIFIER] === target.object);
        const slides = Boolean(target.marker);
        if (!putDown) {
          want(selection, target.pose, `${MarkerKind.TARGET}${slides}`, slides, () => this.#targetMarker(), slides);
          continue;
        }
        const model = putDown[PlannedObjectKey.MODEL];
        want(selection, target.pose, `${model}${slides}`, slides, () => this.#objectMarker(model, null, true), slides);
      }
    }
    for (const [key, marker] of this.#byKey) {
      if (wanted.get(key)?.signature === marker.userData.signature) continue;
      this.#markers.remove(marker);
      this.#byKey.delete(key);
    }
    for (const [key, entry] of wanted) {
      if (!this.#byKey.has(key)) {
        const marker = entry.build();
        marker.userData[SELECTION_DATA] = entry.selection;
        marker.userData.signature = entry.signature;
        marker.userData.grounded = entry.grounded;
        marker.userData.slides = entry.slides;
        this.#byKey.set(key, marker);
        this.#markers.add(marker);
      }
      const marker = this.#byKey.get(key);
      if (!(this.#handles.dragging && this.#handles.object === marker)) {
        this.#pose(marker, entry.pose);
        if (entry.slides) PlanScene.#tint(marker, overlay.reachable.get(key) ?? null);
      }
      if (entry.selection.kind === MarkerKind.ROBOT) {
        marker.userData.posture = postures.get(entry.selection.identifier) ?? {};
        marker.userData.stale = stale.has(entry.selection.identifier);
        PlanScene.#bend(marker);
        PlanScene.#grey(marker);
      }
    }
    this.select(this.#selection && this.#byKey.has(this.#selection.key) ? this.#selection : null);
    this.#view.requestRender();
  }

  /**
   * @param {Selection|null} selection The marker to put the handles on, or null.
   */
  select(selection) {
    this.#selection = selection;
    const marker = selection && this.#byKey.get(selection.key);
    if (!marker) {
      this.#handles.detach();
      this.#view.requestRender();
      return;
    }
    if (this.#handles.object !== marker) this.#handles.attach(marker);
    this.#applyMode();
  }

  /**
   * @param {string} mode A `TransformMode` value.
   */
  setMode(mode) {
    this.#mode = mode;
    this.#applyMode();
  }

  /**
   * Look at the shown environment, or at the markers on an empty floor.
   */
  frame() {
    const bounds = this.#view.contentBounds() ?? this.#markerBounds() ?? FLOOR_REGION;
    this.#view.frame(bounds);
  }

  /**
   * Look at the plan's markers.
   */
  frameMarkers() {
    this.#view.frame(this.#markerBounds() ?? FLOOR_REGION);
  }

  /**
   * Stop rendering and release the view, the handles and the environment.
   */
  dispose() {
    this.#loading?.abort();
    this.#environment?.dispose();
    for (const [type, listener] of this.#canvasListeners) this.#view.renderer.domElement.removeEventListener(type, listener);
    this.#handles.detach();
    this.#handles.dispose();
    this.#axes.dispose();
    this.#view.dispose();
  }

  // %% markers

  /**
   * @param {string} model The robot model's name.
   * @param {number} color The disc's colour.
   * @param {string} label The robot's name.
   * @param {boolean} edited Whether its plan is the one edited.
   * @returns {THREE.Group} A robot: its model at rest with its name above it; a disc
   *   with an arrow along its heading until the model is loaded, and for a model whose
   *   description is not installed.
   */
  #robotMarker(model, color, label, edited) {
    const material = new THREE.MeshStandardMaterial({ color });
    const disc = new THREE.Mesh(new THREE.CylinderGeometry(MarkerSize.ROBOT_RADIUS, MarkerSize.ROBOT_RADIUS, MarkerSize.ROBOT_HEIGHT, SEGMENTS), material);
    disc.rotation.x = Math.PI / 2;
    disc.position.z = MarkerSize.ROBOT_HEIGHT / 2;
    const arrow = this.#arrow(material);
    arrow.position.z = MarkerSize.ROBOT_HEIGHT;
    const name = this.#nameLabel(label, edited);
    const group = new THREE.Group();
    group.add(disc, arrow, name);
    // a robot whose description is not installed keeps its disc, which is no error
    this.#robotModel(model).then(
      (links) => {
        const copy = links.clone();
        copy.traverse((child) => {
          child.castShadow = child.isMesh;
        });
        group.remove(disc, arrow);
        group.add(copy);
        group.userData.model = copy;
        PlanScene.#bend(group);
        group.userData.greyed = false;
        PlanScene.#grey(group);
        name.position.z = new THREE.Box3().setFromObject(copy).max.z + MarkerSize.LABEL_LIFT;
        if (this.#firstFraming.frameOnModelLoaded()) this.frame();
        this.#view.requestRender();
      },
      () => {},
    );
    return group;
  }

  /**
   * Put a robot's model into the posture its marker holds: each joint the posture
   * names, by its name after the robot's namespace, that the model has.
   * @param {THREE.Object3D} marker A robot's marker.
   */
  static #bend(marker) {
    const model = marker.userData.model;
    if (!model?.setJointValue) return;
    for (const [name, position] of Object.entries(marker.userData.posture ?? {})) {
      const joint = name.split(JOINT_NAMESPACE_SEPARATOR).at(-1);
      if (model.joints?.[joint]) model.setJointValue(joint, position);
    }
  }

  /**
   * Grey a robot whose marker says its posture is not live, see-through and colourless,
   * and give it its own materials back once it is live again.
   * @param {THREE.Object3D} marker A robot's marker.
   */
  static #grey(marker) {
    const stale = Boolean(marker.userData.stale);
    if (Boolean(marker.userData.greyed) === stale) return;
    marker.userData.greyed = stale;
    marker.traverse((child) => {
      if (!child.isMesh) return;
      if (!stale) {
        if (!child.userData.liveMaterial) return;
        for (const material of [child.material].flat()) material.dispose();
        child.material = child.userData.liveMaterial;
        delete child.userData.liveMaterial;
        return;
      }
      child.userData.liveMaterial ??= child.material;
      const grey = (material) => {
        const copy = material.clone();
        copy.transparent = true;
        copy.opacity = STALE_OPACITY;
        copy.color?.setScalar((copy.color.r + copy.color.g + copy.color.b) / 3);
        return copy;
      };
      child.material = Array.isArray(child.material) ? child.material.map(grey) : grey(child.material);
    });
  }

  /**
   * @param {string} model A robot model's name.
   * @returns {Promise<THREE.Object3D>} Its links as its URDF describes them at rest,
   *   loaded once.
   */
  #robotModel(model) {
    if (!this.#robotModels.has(model)) {
      const manager = new THREE.LoadingManager();
      this.#robotModels.set(model, PlanScene.#described(this.#robotUrl(model), manager, new MeshLoading(manager, () => {})));
    }
    return this.#robotModels.get(model);
  }

  /**
   * @param {string} text A robot's name.
   * @param {boolean} edited Whether its plan is the one edited.
   * @returns {THREE.Sprite} The name, facing the camera and drawn over the environment
   *   so a robot inside furniture is still named.
   */
  #nameLabel(text, edited) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    context.font = RobotLabel.FONT;
    canvas.width = Math.ceil(context.measureText(text).width) + 2 * RobotLabel.PADDING;
    canvas.height = RobotLabel.FONT_SIZE + 2 * RobotLabel.PADDING;
    context.font = RobotLabel.FONT;
    context.fillStyle = edited ? RobotLabel.EDITED_BACKGROUND : RobotLabel.BACKGROUND;
    context.beginPath();
    context.roundRect(0, 0, canvas.width, canvas.height, canvas.height / 2);
    context.fill();
    context.fillStyle = RobotLabel.TEXT;
    context.textBaseline = 'middle';
    context.fillText(text, RobotLabel.PADDING, canvas.height / 2);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, sizeAttenuation: false }));
    sprite.renderOrder = 1;
    sprite.scale.set((LABEL_SCREEN_HEIGHT * canvas.width) / canvas.height, LABEL_SCREEN_HEIGHT, 1);
    sprite.position.z = MarkerSize.LABEL_LIFT;
    return sprite;
  }

  /**
   * @returns {THREE.Group} A pose target: a ring with an arrow along the heading.
   */
  #targetMarker() {
    const material = new THREE.MeshStandardMaterial({ color: MarkerColor.TARGET });
    const ring = new THREE.Mesh(new THREE.TorusGeometry(MarkerSize.TARGET_RADIUS, MarkerSize.TARGET_TUBE, SEGMENTS / 2, SEGMENTS), material);
    ring.position.z = MarkerSize.TARGET_TUBE;
    const group = new THREE.Group();
    group.add(ring, this.#arrow(material));
    return group;
  }

  /**
   * @param {THREE.Material} material The arrow's material.
   * @returns {THREE.Mesh} An arrow head pointing along x.
   */
  #arrow(material) {
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(MarkerSize.ARROW_RADIUS, MarkerSize.ARROW_LENGTH, SEGMENTS), material);
    arrow.rotation.z = -Math.PI / 2;
    arrow.position.x = MarkerSize.ROBOT_RADIUS;
    return arrow;
  }

  /**
   * @param {string} model The object model's identifier.
   * @param {string|null} color The object's colour, or null for the default.
   * @param {boolean} ghost Whether the marker is a see-through put-down target.
   * @returns {THREE.Group} The object, a small box until its mesh is loaded.
   */
  #objectMarker(model, color, ghost) {
    const paint = new ObjectPaint(model, color, ghost);
    const group = new THREE.Group();
    const placeholder = new THREE.Mesh(new THREE.BoxGeometry(MarkerSize.PLACEHOLDER, MarkerSize.PLACEHOLDER, MarkerSize.PLACEHOLDER), paint.material);
    group.add(placeholder);
    this.#mesh(model)
      .then((mesh) => {
        const copy = mesh.clone();
        paint.paint(copy);
        group.remove(placeholder);
        group.add(copy);
        this.#view.requestRender();
      })
      .catch((error) => this.#listeners.onError(error));
    return group;
  }

  /**
   * @param {string} model An object model's identifier.
   * @returns {Promise<THREE.Object3D>} Its mesh, or a library model's links as its URDF
   *   describes them at rest, loaded once.
   */
  #mesh(model) {
    if (!this.#meshes.has(model)) {
      const manager = new THREE.LoadingManager();
      const loading = new MeshLoading(manager, () => {});
      const url = this.#objectUrl(model);
      this.#meshes.set(model, model.toLowerCase().endsWith(DESCRIPTION_SUFFIX) ? PlanScene.#described(url, manager, loading) : loading.load(url));
    }
    return this.#meshes.get(model);
  }

  /**
   * @param {string} url A library model's URDF.
   * @param {THREE.LoadingManager} manager Tracks the loader's requests.
   * @param {MeshLoading} loading Loads the meshes the URDF names.
   * @returns {Promise<THREE.Object3D>} The model's links, once every mesh they name
   *   arrived: the loader hands the links over before, and markers copy them.
   */
  static #described(url, manager, loading) {
    const meshesLoaded = new Promise((resolve) => {
      manager.onLoad = resolve;
    });
    const loader = new URDFLoader(manager);
    loader.parseCollision = false;
    loader.loadMeshCb = (meshUrl, _manager, material, done) => {
      loading.load(meshUrl, material).then((mesh) => done(mesh), (error) => done(null, error));
    };
    return loader.loadAsync(url).then((model) => meshesLoaded.then(() => model));
  }

  /**
   * @param {THREE.Object3D} marker A marker.
   * @param {Array<number>} pose Its pose.
   */
  #pose(marker, pose) {
    marker.position.set(...PlanPose.position(pose));
    marker.quaternion.set(...PlanPose.orientation(pose));
  }

  /**
   * @returns {Bounds|null} The box around the markers, or null without any.
   */
  #markerBounds() {
    const box = new THREE.Box3().setFromObject(this.#markers);
    return box.isEmpty() ? null : new Bounds(box.min.toArray(), box.max.toArray());
  }

  /**
   * Light an empty floor and look at it.
   */
  #showFloor() {
    this.#lighting.showStudio(this.#view.renderer, this.#view.scene, FLOOR_REGION);
    this.#view.frame(FLOOR_REGION);
  }

  // %% handles

  /**
   * Let the handles move or turn the selection; robots stay on the floor, and every
   * marker turns about the vertical only.
   */
  #applyMode() {
    const grounded = this.#handles.object?.userData.grounded ?? true;
    this.#handles.setMode(this.#mode);
    this.#handles.showX = this.#mode === TransformMode.MOVE;
    this.#handles.showY = this.#mode === TransformMode.MOVE;
    this.#handles.showZ = this.#mode === TransformMode.TURN || !grounded;
    this.#view.requestRender();
  }

  /**
   * Rest a dragged free-place marker where the page says, and tint it by whether an arm
   * may reach it.
   */
  #moved() {
    const marker = this.#handles.object;
    if (!marker?.userData.slides || !this.#selection) return;
    const pose = [...marker.position.toArray(), ...marker.quaternion.toArray()];
    const answer = this.#listeners.onMove(this.#selection, pose);
    if (!answer) return;
    this.#pose(marker, answer.pose);
    PlanScene.#tint(marker, answer.reachable);
    this.#view.requestRender();
  }

  /**
   * @param {THREE.Object3D} marker A free-place marker.
   * @param {boolean|null} reachable Whether an arm may reach it, or null while it is not
   *   known, which keeps the marker's own colour.
   */
  static #tint(marker, reachable) {
    marker.traverse((child) => {
      if (!child.isMesh) return;
      child.userData.ownColor ??= child.material.color.getHex();
      const color = reachable === null ? child.userData.ownColor : reachable ? MarkerColor.REACHABLE : MarkerColor.UNREACHABLE;
      child.material.color.setHex(color);
    });
  }

  /**
   * Draw the zones on the table, each a see-through rectangle with its outline.
   * @param {Array<{identifier: string, label: string, corners: Array<Array<number>>}>} zones
   *   The zones' corners in world coordinates.
   */
  #showZones(zones) {
    const signature = JSON.stringify(zones);
    if (signature === this.#zoneSignature) return;
    this.#zoneSignature = signature;
    this.#zones.clear();
    for (const zone of zones) {
      const corners = zone.corners.map((corner) => new THREE.Vector3(...corner));
      const area = new THREE.BufferGeometry().setFromPoints([corners[0], corners[1], corners[2], corners[0], corners[2], corners[3]]);
      const fill = new THREE.Mesh(area, new THREE.MeshBasicMaterial({ color: MarkerColor.ZONE, transparent: true, opacity: ZONE_OPACITY, side: THREE.DoubleSide, depthWrite: false }));
      const outline = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(corners), new THREE.LineBasicMaterial({ color: MarkerColor.ZONE }));
      fill.name = zone.identifier;
      this.#zones.add(fill, outline);
    }
    this.#view.requestRender();
  }

  /**
   * Draw what a detection looked in and what came back: the box as orange edges, where
   * the step expected the object (where the plan was validated, or where the object was
   * last seen once the step was planned again) as a white ball, and every reported
   * object as a box of its size in its first colour, edged green when the step took it,
   * red when it took it too far from there, grey otherwise.
   * @param {Object|null} report A `PerceptionReport`, or null to draw nothing.
   */
  #showPerception(report) {
    const signature = JSON.stringify(report);
    if (signature === this.#perceptionSignature) return;
    this.#perceptionSignature = signature;
    this.#perception.clear();
    if (report) {
      const [low, high] = report[PerceptionReportKey.BOX];
      this.#perception.add(PlanScene.#edgedBox(low, high, MarkerColor.SEARCH_BOX));
      const grounded = report[PerceptionReportKey.GROUNDED];
      if (grounded) {
        const ball = new THREE.Mesh(new THREE.SphereGeometry(GROUNDED_RADIUS), new THREE.MeshBasicMaterial({ color: MarkerColor.GROUNDED }));
        ball.position.set(...grounded);
        this.#perception.add(ball);
      }
      for (const seen of report[PerceptionReportKey.OBJECTS] ?? []) {
        const centre = seen[ReportedObjectKey.POSITION];
        const size = (seen[ReportedObjectKey.SIZE] ?? [0, 0, 0]).map((extent) => Math.max(extent, SMALLEST_REPORTED));
        const low = centre.map((value, axis) => value - size[axis] / 2);
        const high = centre.map((value, axis) => value + size[axis] / 2);
        const verdict = seen[ReportedObjectKey.VERDICT];
        const edge = verdict === ReportedObjectVerdict.ACCEPTED ? MarkerColor.TAKEN : verdict === ReportedObjectVerdict.TOO_FAR ? MarkerColor.TOO_FAR : MarkerColor.LEFT;
        const body = new THREE.Mesh(new THREE.BoxGeometry(...size), new THREE.MeshBasicMaterial({ color: new THREE.Color((seen[ReportedObjectKey.COLORS] ?? [])[0] ?? 'white'), transparent: true, opacity: REPORTED_OPACITY, depthWrite: false }));
        body.position.set(...centre);
        this.#perception.add(body, PlanScene.#edgedBox(low, high, edge));
      }
    }
    this.#view.requestRender();
  }

  /**
   * @param {Array<number>} low A box's lowest corner.
   * @param {Array<number>} high Its highest corner.
   * @param {number} color The colour of its edges.
   * @returns {THREE.LineSegments} The box's edges.
   */
  static #edgedBox(low, high, color) {
    const size = high.map((value, axis) => value - low[axis]);
    const edges = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...size)), new THREE.LineBasicMaterial({ color }));
    edges.position.set(...low.map((value, axis) => value + size[axis] / 2));
    return edges;
  }

  /**
   * Report the dragged marker's new pose.
   */
  #placed() {
    const marker = this.#handles.object;
    if (!marker || !this.#selection) return;
    const pose = [...marker.position.toArray(), ...marker.quaternion.toArray()].map(PlanPose.rounded);
    this.#listeners.onPlace(this.#selection, pose);
  }

  /**
   * @param {string} type An event type.
   * @param {function(Event): void} listener Its listener on the canvas.
   */
  #listenOnCanvas(type, listener) {
    this.#view.renderer.domElement.addEventListener(type, listener);
    this.#canvasListeners.push([type, listener]);
  }

  /**
   * Select the marker a click hits, or nothing for a click beside every marker.
   * @param {PointerEvent} event The pointer's release.
   */
  #pick(event) {
    const press = this.#press;
    this.#press = null;
    if (!press || press.onHandle || event.button !== PRIMARY_BUTTON) return;
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > CLICK_DISTANCE) return;
    const canvas = this.#view.renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2(((event.clientX - canvas.left) / canvas.width) * 2 - 1, 1 - ((event.clientY - canvas.top) / canvas.height) * 2);
    const ray = new THREE.Raycaster();
    ray.setFromCamera(pointer, this.#view.camera);
    const hit = ray.intersectObjects([...this.#byKey.values()], true)[0];
    let current = hit?.object ?? null;
    while (current && current.userData[SELECTION_DATA] === undefined) current = current.parent;
    const selection = current ? current.userData[SELECTION_DATA] : null;
    this.select(selection);
    this.#listeners.onPick(selection);
  }
}
