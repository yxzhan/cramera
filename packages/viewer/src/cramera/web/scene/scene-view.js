/**
 * The 3D view: renderer, camera, orbit controls, ground and render loop, owned
 * together and released together.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { GTAOPass } from 'three/addons/postprocessing/GTAOPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';

import { Bounds } from './bounds.js';

/**
 * The camera's lens and clipping planes unless a scene asks for others.
 */
export const DEFAULT_CAMERA = Object.freeze({ FIELD_OF_VIEW: 45, NEAR: 0.02, FAR: 400 });

/**
 * The exposure of scenes that set none.
 */
export const DEFAULT_EXPOSURE = 1;

/**
 * How much farther than needed to fit the content's bounding sphere the camera stands.
 */
const FRAMING_MARGIN = 1.0;

/**
 * The way the camera looks at content it frames: from the front left, above.
 */
const FRAMING_DIRECTION = Object.freeze([-0.8, -1.1, 1.2]);

/**
 * The size of the ground plane, in metres per side.
 */
const GROUND_SIZE = 200;

/**
 * How dark the ground's shadows are.
 */
const GROUND_SHADOW_OPACITY = 0.22;

/**
 * The longest frame time playback counts, in seconds, so a hidden tab does not jump.
 */
const LONGEST_STEP = 0.1;

// %% errors

/**
 * A browser that cannot create a WebGL context.
 */
export class WebGlUnavailable extends Error {
  constructor() {
    super('This browser could not create a WebGL context for the 3D view.');
  }
}

// %% view

export class SceneView {
  /**
   * The element the canvas fills.
   * @type {HTMLElement}
   */
  #container;

  /**
   * Watches the container's size.
   * @type {ResizeObserver}
   */
  #resizeObserver;

  /**
   * The pending animation frame, or 0.
   * @type {number}
   */
  #animationFrame = 0;

  /**
   * The time of the previous animation frame, in milliseconds.
   * @type {number|null}
   */
  #previousTime = null;

  /**
   * Whether the next animation frame must render.
   * @type {boolean}
   */
  #needsRender = true;

  /**
   * The renderer.
   * @type {THREE.WebGLRenderer}
   */
  renderer;

  /**
   * The scene holding ground, lights and content.
   * @type {THREE.Scene}
   */
  scene = new THREE.Scene();

  /**
   * The group holding the shown bundle's models and objects.
   * @type {THREE.Group}
   */
  content = new THREE.Group();

  /**
   * The camera.
   * @type {THREE.PerspectiveCamera}
   */
  camera;

  /**
   * The orbit controls.
   * @type {OrbitControls}
   */
  controls;

  /**
   * The ground plane, which only shows shadows.
   * @type {THREE.Mesh}
   */
  ground;

  /**
   * The passes drawing ambient occlusion, or null while it is off.
   * @type {EffectComposer|null}
   */
  #composer = null;

  /**
   * Called after each rendered frame, such as labels that follow the camera.
   * @type {Set<function(): void>}
   */
  #renderListeners = new Set();

  /**
   * The scene's background image, or null.
   * @type {THREE.Texture|null}
   */
  #background = null;

  /**
   * @param {HTMLElement} container The element the canvas fills.
   * @throws {WebGlUnavailable} When the browser has no WebGL.
   */
  constructor(container) {
    this.#container = container;
    const canvas = container.ownerDocument.createElement('canvas');
    if (!canvas.getContext('webgl2')) throw new WebGlUnavailable();
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(container.ownerDocument.defaultView.devicePixelRatio);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = DEFAULT_EXPOSURE;
    container.prepend(canvas);
    THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
    this.camera = new THREE.PerspectiveCamera(DEFAULT_CAMERA.FIELD_OF_VIEW, 1, DEFAULT_CAMERA.NEAR, DEFAULT_CAMERA.FAR);
    this.camera.up.set(0, 0, 1);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.zoomToCursor = true;
    this.controls.addEventListener('change', () => this.requestRender());
    this.ground = new THREE.Mesh(
      new THREE.PlaneGeometry(GROUND_SIZE, GROUND_SIZE),
      new THREE.ShadowMaterial({ opacity: GROUND_SHADOW_OPACITY }),
    );
    this.ground.receiveShadow = true;
    this.scene.add(this.ground, this.content);
    this.#resizeObserver = new ResizeObserver(() => this.#resize());
    this.#resizeObserver.observe(container);
    this.#resize();
  }

  /**
   * Render on every animation frame that needs it, and let time pass.
   * @param {function(number): boolean} onTick Called with the seconds since the
   *   previous frame; returns whether the scene changed.
   */
  start(onTick) {
    const loop = (time) => {
      const seconds = this.#previousTime === null ? 0 : Math.min((time - this.#previousTime) / 1000, LONGEST_STEP);
      this.#previousTime = time;
      if (onTick(seconds)) this.#needsRender = true;
      if (this.controls.update()) this.#needsRender = true;
      if (this.#needsRender) {
        this.#needsRender = false;
        if (this.#composer) this.#composer.render();
        else this.renderer.render(this.scene, this.camera);
        for (const listener of this.#renderListeners) listener();
      }
      this.#animationFrame = requestAnimationFrame(loop);
    };
    this.#animationFrame = requestAnimationFrame(loop);
  }

  /**
   * @param {function(): void} listener Called after each rendered frame.
   * @returns {function(): void} Stops calling the listener.
   */
  onRender(listener) {
    this.#renderListeners.add(listener);
    return () => this.#renderListeners.delete(listener);
  }

  /**
   * @returns {{width: number, height: number}} The size of the drawing, in CSS pixels.
   */
  get size() {
    return { width: this.#container.clientWidth, height: this.#container.clientHeight };
  }

  /**
   * Render on the next animation frame.
   */
  requestRender() {
    this.#needsRender = true;
  }

  /**
   * Clear every frame to a solid colour instead of leaving the page behind the canvas
   * showing through, as a video of the canvas needs, or back to see-through.
   * @param {string|null} color A CSS colour, or null for see-through.
   */
  setSolidBackground(color) {
    if (color) this.renderer.setClearColor(new THREE.Color(color), 1);
    else this.renderer.setClearColor(0x000000, 0);
    this.requestRender();
  }

  /**
   * @returns {Bounds|null} The box around the shown content, or null when it is empty.
   */
  contentBounds() {
    const box = new THREE.Box3().setFromObject(this.content);
    if (box.isEmpty()) return null;
    return new Bounds(box.min.toArray(), box.max.toArray());
  }

  /**
   * Look at a box from the front left, above, from far enough to see all of it.
   * @param {Bounds} bounds The box to frame.
   */
  frame(bounds) {
    const center = new THREE.Vector3(...bounds.center());
    const radius = Math.max(Math.hypot(...bounds.size()) / 2, DEFAULT_CAMERA.NEAR * 10);
    const distance = (radius * FRAMING_MARGIN) / Math.sin(THREE.MathUtils.degToRad(this.camera.fov) / 2);
    const direction = new THREE.Vector3(...FRAMING_DIRECTION).normalize();
    this.lookFrom(center.clone().addScaledVector(direction, distance), center);
  }

  /**
   * @param {THREE.Vector3} position Where the camera stands.
   * @param {THREE.Vector3} target What it looks at.
   */
  lookFrom(position, target) {
    this.camera.position.copy(position);
    this.controls.target.copy(target);
    this.controls.update();
    this.requestRender();
  }

  /**
   * Use the lens and clipping a scene asks for; fields it leaves out keep the defaults.
   * @param {number|null} fieldOfView The vertical field of view in degrees.
   * @param {number|null} far The far clipping distance in metres.
   */
  setLens(fieldOfView, far) {
    this.camera.fov = fieldOfView ?? DEFAULT_CAMERA.FIELD_OF_VIEW;
    this.camera.far = far ?? DEFAULT_CAMERA.FAR;
    this.controls.maxDistance = this.camera.far / 2;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }

  /**
   * @param {number|null} exposure The tone-mapping exposure, or null for the default.
   */
  setExposure(exposure) {
    this.renderer.toneMappingExposure = exposure ?? DEFAULT_EXPOSURE;
    this.requestRender();
  }

  /**
   * @param {boolean} shown Whether the ground plane catches shadows.
   */
  showGround(shown) {
    this.ground.visible = shown;
    this.requestRender();
  }

  /**
   * @returns {boolean} Whether ambient occlusion is drawn.
   */
  get ambientOcclusion() {
    return this.#composer !== null;
  }

  /**
   * @param {boolean} enabled Whether to darken creases and contacts by ambient occlusion.
   */
  setAmbientOcclusion(enabled) {
    if (enabled === this.ambientOcclusion) return;
    if (enabled) {
      const size = this.renderer.getSize(new THREE.Vector2());
      this.#composer = new EffectComposer(this.renderer);
      this.#composer.addPass(new RenderPass(this.scene, this.camera));
      this.#composer.addPass(new GTAOPass(this.scene, this.camera, size.x, size.y));
      this.#composer.addPass(new OutputPass());
      this.#composer.setSize(size.x, size.y);
    } else {
      this.#composer.dispose();
      this.#composer = null;
    }
    this.requestRender();
  }

  /**
   * @param {string|null} url The scene's background image, or null for none.
   */
  setBackground(url) {
    this.#background?.dispose();
    this.#background = null;
    this.scene.background = null;
    if (url) {
      new THREE.TextureLoader().loadAsync(url).then((texture) => {
        texture.colorSpace = THREE.SRGBColorSpace;
        this.#background = texture;
        this.scene.background = texture;
        this.requestRender();
      });
    }
    this.requestRender();
  }

  /**
   * Fit the renderer and camera to the container.
   */
  #resize() {
    const width = Math.max(this.#container.clientWidth, 1);
    const height = Math.max(this.#container.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.#composer?.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }

  /**
   * Stop rendering and release the renderer, controls and observer.
   */
  dispose() {
    cancelAnimationFrame(this.#animationFrame);
    this.setAmbientOcclusion(false);
    this.#background?.dispose();
    this.#resizeObserver.disconnect();
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
