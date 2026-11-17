/**
 * The viewer application: coordinates the scene picker, the 3D view, the timeline,
 * the side panels and the plugins. Each part owns its own elements and cleanup.
 */

import * as THREE from 'three';

import { EventBus } from '../core/event-bus.js';
import { fullscreenOf, layoutOf, liveAddressOf, panelOf, sceneAlone, sceneOf, withLive, withScene } from '../core/scene-address.js';
import { PageStatus } from '../core/page-status.js';
import {
  Capability,
  ExecutionRequestKey,
  LiveInfoKey,
  LiveRoute,
  LiveStateKey,
  RecordingState,
  RecordingStatusKey,
  SceneKey,
  ViewerEvent,
  ViewerRoute,
} from '../protocol.js';
import { PlaybackClock } from '../playback/playback-clock.js';
import { saveVideo, VideoRecording } from '../playback/video-recording.js';
import { LOST_NOTE, TimelineView, WAITING_NOTE } from '../playback/timeline-view.js';
import { DetailsPanel } from '../panels/details-panel.js';
import { PerceptionPanel } from '../panels/perception-panel.js';
import { QueryStepActions, QueryStepsPanel } from '../panels/query-steps-panel.js';
import { ResolutionActions, ResolutionsPanel } from '../panels/resolutions-panel.js';
import { SceneGraphPanel } from '../panels/scene-graph-panel.js';
import { SegmindPanel } from '../panels/segmind-panel.js';
import { PanelHost } from '../panels/panel-host.js';
import { SceneChoice } from '../panels/scene-choice.js';
import { ScenePickerView } from '../panels/scene-picker-view.js';
import { LayerObject, LayersPanel, ViewSwitch } from '../panels/layers-panel.js';
import { QuerySlot } from '../panels/query-slot.js';
import { COLUMNS, PaneSplit, ROWS } from './pane-split.js';
import { CameraFollow } from '../scene/camera-follow.js';
import { CinematicCamera, ShotPlan } from '../scene/cinematic-camera.js';
import { DebugMarkers } from '../scene/debug-markers.js';
import { FrameAxes, framesOf } from '../scene/frame-axes.js';
import { ObjectLabels } from '../scene/object-labels.js';
import { SceneLighting } from '../scene/scene-lighting.js';
import { SceneLoader } from '../scene/scene-loader.js';
import { DEFAULT_LIVE_ADDRESS, LiveClient, readAddressOf } from '../live/live-client.js';
import { LiveListener, LiveSession } from '../live/live-session.js';
import { LiveActions, LiveBadge, LiveBadgeState, LivePanel } from '../panels/live-panel.js';
import { CameraGlide, focusBounds, RegionOutlines } from '../scene/scene-focus.js';
import { SceneHighlights } from '../scene/scene-highlights.js';
import { ScenePicking } from '../scene/scene-picking.js';
import { SceneView } from '../scene/scene-view.js';
import { FirstGestureFullscreen } from './fullscreen.js';
import { KeyboardShortcuts, PlaybackActions } from './keyboard-shortcuts.js';
import { PluginContext, PluginModules } from './plugin-modules.js';
import { ontologyAddress } from './ontology-page.js';
import { ONTOLOGY_ROUTE, SCENE_PAGE, SiteHeader, ThemeSetting } from './site-header.js';

/**
 * The screens on which the columns stack, as `styles/viewer.css` declares them; there
 * the Layers overlay starts folded so it does not cover the 3D view.
 */
const NARROW_SCREEN = '(max-width: 900px)';

/**
 * The style property holding the colour of the 3D view's backdrop.
 */
const STAGE_COLOR = '--stage';

/**
 * The backdrop a filmed video gets when the page names none.
 */
const DEFAULT_BACKDROP = '#050608';

// %% elements

/**
 * The page elements the application fills.
 */
export class PageElements {
  /**
   * @param {Document} document The page.
   */
  constructor(document) {
    /**
     * The page.
     * @type {Document}
     */
    this.document = document;
    /**
     * The 3D view's container.
     * @type {HTMLElement}
     */
    this.stage = document.getElementById('stage');
    /**
     * The message shown over the 3D view.
     * @type {HTMLElement}
     */
    this.stageMessage = document.getElementById('stage-message');
    /**
     * The top bar.
     * @type {HTMLElement}
     */
    this.header = document.getElementById('site-header');
    /**
     * The scene picker.
     * @type {HTMLElement}
     */
    this.picker = document.getElementById('scene-picker');
    /**
     * The Layers overlay of the 3D view.
     * @type {HTMLElement}
     */
    this.layers = document.getElementById('layers');
    /**
     * The badge saying the view follows a live publisher.
     * @type {HTMLElement}
     */
    this.liveBadge = document.getElementById('live-badge');
    /**
     * The button opening the scene alone in a window of its own.
     * @type {HTMLElement}
     */
    this.popOut = document.getElementById('pop-out');
    /**
     * The slot one plugin may fill with its question panel.
     * @type {HTMLElement}
     */
    this.querySlot = document.getElementById('query-slot');
    /**
     * The element holding the scene, the divider and the side column.
     * @type {HTMLElement}
     */
    this.split = document.getElementById('split');
    /**
     * The divider between the scene and the side column.
     * @type {HTMLElement}
     */
    this.divider = document.getElementById('split-divider');
    /**
     * The side column, holding the query slot, a divider and the side panels.
     * @type {HTMLElement}
     */
    this.column = document.getElementById('column');
    /**
     * The divider between the query slot and the side panels.
     * @type {HTMLElement}
     */
    this.rowDivider = document.getElementById('row-divider');
    /**
     * The side panels.
     * @type {HTMLElement}
     */
    this.side = document.getElementById('side-panels');
    /**
     * The playback controls.
     * @type {HTMLElement}
     */
    this.timeline = document.getElementById('timeline');
    /**
     * The status line.
     * @type {HTMLElement}
     */
    this.statusLine = document.getElementById('status-line');
  }
}

// %% application

export class Application {
  /**
   * The page elements.
   * @type {PageElements}
   */
  #elements;

  /**
   * The viewer's routes.
   * @type {import('../core/viewer-api.js').ViewerApi}
   */
  #api;

  /**
   * The page's location, read for the requested scene and updated with the shown one.
   * @type {Location}
   */
  #location;

  /**
   * The browser's history, updated with the shown scene.
   * @type {History}
   */
  #history;

  /**
   * The event bus the parts and plugins talk through.
   * @type {EventBus}
   */
  bus = new EventBus();

  /**
   * How far the page is.
   * @type {PageStatus}
   */
  #status;

  /**
   * The 3D view, once created.
   * @type {SceneView|null}
   */
  #view = null;

  /**
   * The view's lights.
   * @type {SceneLighting}
   */
  #lighting = new SceneLighting();

  /**
   * The shown scene, once loaded.
   * @type {import('../scene/scene-loader.js').LoadedScene|null}
   */
  #scene = null;

  /**
   * The shown scene's clock.
   * @type {PlaybackClock|null}
   */
  #clock = null;

  /**
   * Cancels the scene being loaded when another is chosen.
   * @type {AbortController|null}
   */
  #loading = null;

  /**
   * The timeline.
   * @type {TimelineView}
   */
  #timeline;

  /**
   * The side panels.
   * @type {PanelHost}
   */
  #panels;

  /**
   * The details panel.
   * @type {DetailsPanel}
   */
  #details;

  /**
   * The Scene graph panel.
   * @type {SceneGraphPanel}
   */
  #sceneGraph;

  /**
   * The Perception panel.
   * @type {PerceptionPanel}
   */
  #perception;

  /**
   * The Segmind panel.
   * @type {SegmindPanel}
   */
  #segmind;

  /**
   * The top bar.
   * @type {SiteHeader}
   */
  #header;

  /**
   * The slot one plugin may fill with its question panel.
   * @type {QuerySlot}
   */
  #querySlot;

  /**
   * The Layers overlay, once the 3D view exists.
   * @type {LayersPanel|null}
   */
  #layers = null;

  /**
   * The labels of the free objects, once the 3D view exists.
   * @type {ObjectLabels|null}
   */
  #labels = null;

  /**
   * The axis triads on the scene's frames, once the 3D view exists.
   * @type {FrameAxes|null}
   */
  #axes = null;

  /**
   * The live publisher's debug markers, once the 3D view exists.
   * @type {DebugMarkers|null}
   */
  #markers = null;

  /**
   * Moves the camera with the robot, once the 3D view exists.
   * @type {CameraFollow|null}
   */
  #follow = null;

  /**
   * Films the recording like a movie while it is on, once the 3D view exists.
   * @type {CinematicCamera|null}
   */
  #cinematic = null;

  /**
   * The video being filmed, and whether the cinematic camera was on before, or null.
   * @type {{recording: VideoRecording, cinematic: boolean}|null}
   */
  #filming = null;

  /**
   * Whether the free objects are drawn.
   * @type {boolean}
   */
  #objectsShown = true;

  /**
   * The state of the live publisher's recording, or null when it reported none.
   * @type {string|null}
   */
  #recordingState = null;

  /**
   * Selects links and objects in the 3D view, once it exists.
   * @type {ScenePicking|null}
   */
  #picking = null;

  /**
   * Lights up what answers name, once the 3D view exists.
   * @type {SceneHighlights|null}
   */
  #highlights = null;

  /**
   * Draws the regions answers are about as boxes, once the 3D view exists.
   * @type {RegionOutlines|null}
   */
  #regions = null;

  /**
   * Glides the camera onto what answers are about, once the 3D view exists.
   * @type {CameraGlide|null}
   */
  #glide = null;

  /**
   * The Plan resolution panel.
   * @type {ResolutionsPanel}
   */
  #resolutions;

  /**
   * The Queries panel.
   * @type {QueryStepsPanel}
   */
  #querySteps;

  /**
   * The Live panel.
   * @type {LivePanel}
   */
  #livePanel;

  /**
   * The badge over the 3D view saying whether a live run is followed.
   * @type {LiveBadge}
   */
  #liveBadge;

  /**
   * The connection to a live publisher, or null while none is wanted.
   * @type {LiveSession|null}
   */
  #live = null;

  /**
   * Fetches a URL; `window.fetch` in the browser.
   * @type {function(string, Object=): Promise<Response>}
   */
  #fetch;

  /**
   * Starts and stops the live session's and the panels' timers; `window` in the browser.
   * @type {{setInterval: Function, clearInterval: Function, setTimeout: Function, clearTimeout: Function}}
   */
  #timers;

  /**
   * The scene picker's choice, once the scenes are known.
   * @type {SceneChoice|null}
   */
  #choice = null;

  /**
   * The scene picker, once the scenes are known.
   * @type {ScenePickerView|null}
   */
  #picker = null;

  /**
   * @param {PageElements} elements The page elements.
   * @param {import('../core/viewer-api.js').ViewerApi} api The viewer's routes.
   * @param {Location} location The page's location.
   * @param {History} history The browser's history.
   * @param {function(string, Object=): Promise<Response>} fetch Fetches a URL.
   * @param {{setInterval: Function, clearInterval: Function}} timers Starts and stops timers.
   * @param {Storage|null} storage The browser's local storage, or null where it is refused.
   */
  constructor(elements, api, location, history, fetch, timers, storage) {
    this.#elements = elements;
    this.#fetch = fetch;
    this.#timers = timers;
    this.#api = api;
    this.#location = location;
    this.#history = history;
    this.#status = new PageStatus(elements.document.documentElement, elements.statusLine);
    const window = elements.document.defaultView;
    const theme = new ThemeSetting(elements.document.documentElement, storage);
    theme.apply();
    this.#header = new SiteHeader(elements.header, (route) => api.url(route), theme, SCENE_PAGE, (panel) => this.#panels.show(panel));
    this.#timeline = new TimelineView(elements.timeline, (frame) => this.seek(frame), () => this.saveEpisode(), () => this.toggleVideo());
    this.#panels = new PanelHost(elements.side);
    this.#querySlot = new QuerySlot(elements.querySlot, storage);
    new PaneSplit(elements.split, elements.divider, COLUMNS, storage);
    new PaneSplit(elements.column, elements.rowDivider, ROWS, storage);
    this.#panels.onShow((panel) => this.#header.markCurrent(panel && !this.#panels.tabbed(panel) ? panel : SCENE_PAGE));
    elements.popOut.addEventListener('click', () => window.open(sceneAlone(this.#location.href).href, '_blank', 'popup'));
    this.#details = new DetailsPanel(elements.document);
    this.#sceneGraph = new SceneGraphPanel(elements.document, this.bus, (path) => ontologyAddress(api.url(ONTOLOGY_ROUTE), path));
    this.#perception = new PerceptionPanel(elements.document, this.bus);
    this.#segmind = new SegmindPanel(elements.document, this.bus, (marks) => this.#timeline.showEventMarks(marks));
    this.#livePanel = new LivePanel(
      elements.document,
      new LiveActions(
        (address) => this.connectLive(address),
        () => this.disconnectLive(),
        () => this.#live.client.send(LiveRoute.RECORDING_STOP),
        (request) => this.#live.client.saveRecording(request),
        () => this.#live.client.send(LiveRoute.RECORDING_DISCARD),
        (request) => this.#live.client.send(LiveRoute.MARKER_TOPICS, request),
        (topic, namespace, shown) => this.#markers?.setNamespaceShown(topic, namespace, shown),
      ),
    );
    this.#resolutions = new ResolutionsPanel(
      elements.document,
      this.bus,
      new ResolutionActions((identifier) => this.#live.client.send(LiveRoute.RESOLUTION_EXECUTION, { [ExecutionRequestKey.RESOLUTION]: identifier })),
      timers,
      () => Date.now() / 1000,
    );
    this.#querySteps = new QueryStepsPanel(
      elements.document,
      this.bus,
      new QueryStepActions(
        (request) => this.#live.client.send(LiveRoute.QUERY_STEPS_NEXT, request),
        (request) => this.#live.client.send(LiveRoute.QUERY_STEPS_MODE, request),
      ),
      timers,
      (inspecting) => this.#resolutions.holdScene(inspecting),
    );
    this.#liveBadge = new LiveBadge(elements.liveBadge, () => this.#panels.show(this.#livePanel.panel.identifier));
    this.bus.subscribe(ViewerEvent.LIVE_CONNECTED, (info) => this.#showLiveConnection(info));
    this.bus.subscribe(ViewerEvent.LIVE_DISCONNECTED, () => this.#showLiveConnection(null));
  }

  /**
   * Load the viewer's information and scenes, then show the requested or default scene.
   * @param {function(string): Promise<Object>} importModule Imports a plugin module by URL.
   * @returns {Promise<void>} Settles once the first scene is shown or has failed.
   */
  async start(importModule) {
    this.#status.loading('Loading scenes…');
    const [info, index] = await Promise.all([this.#api.info(), this.#api.scenes()]);
    this.#header.show(info);
    const layout = layoutOf(this.#location.href);
    if (layout) this.#elements.document.documentElement.dataset.layout = layout;
    if (fullscreenOf(this.#location.href)) new FirstGestureFullscreen(this.#elements.document);
    const choice = new SceneChoice(index);
    const requested = sceneOf(this.#location.href);
    if (requested) choice.chooseScene(requested);
    const liveAddress = liveAddressOf(this.#location.href);
    if (!choice.scene && !liveAddress) {
      this.#fail('No scenes found. Record a run or add a scenes directory.');
      return;
    }
    this.#choice = choice;
    this.#picker = new ScenePickerView(this.#elements.picker, choice, (name) => {
      this.disconnectLive();
      this.show(name);
    });
    this.#view = new SceneView(this.#elements.stage);
    this.#view.scene.add(this.#lighting.group);
    this.#labels = new ObjectLabels(this.#elements.stage, this.#view);
    this.#axes = new FrameAxes(this.#view);
    this.#markers = new DebugMarkers(this.#view, this.#elements.document);
    this.#follow = new CameraFollow(this.#view);
    this.#cinematic = new CinematicCamera(this.#view);
    this.#addLayers(this.#view);
    this.#glide = new CameraGlide(this.#view);
    this.#picking = new ScenePicking(this.#view, this.bus, (box, point) => this.#glide.glideOnto(box, point));
    this.#highlights = new SceneHighlights(this.#view);
    this.bus.subscribe(ViewerEvent.ENTITY_HIGHLIGHTED, (keys) => this.#highlights.show(keys ?? []));
    this.#regions = new RegionOutlines(this.#view);
    this.bus.subscribe(ViewerEvent.REGIONS_OUTLINED, (outlines) => this.#regions.show(outlines ?? []));
    this.bus.subscribe(ViewerEvent.FOCUS_REQUESTED, (keys) => this.#glide.glideTo(focusBounds(this.#view.content, this.#regions.group, keys ?? [])));
    this.bus.subscribe(ViewerEvent.ENTITY_SELECTED, (selection) => {
      this.#details.showSelection(selection);
      this.#sceneGraph.reveal(selection);
    });
    this.bus.subscribe(ViewerEvent.SCENE_REQUESTED, (name) => this.showRequested(name));
    this.bus.subscribe(ViewerEvent.FRAME_REQUESTED, (frame) => this.seek(frame));
    this.bus.subscribe(ViewerEvent.LIVE_REQUESTED, (address) => this.connectLive(address ?? DEFAULT_LIVE_ADDRESS));
    this.bus.subscribe(ViewerEvent.PLAYBACK_REQUESTED, (playing) => this.#timeline.setPlaying(playing));
    new KeyboardShortcuts(
      this.#elements.document,
      new PlaybackActions(
        () => this.#timeline.togglePlaying(),
        (frames) => this.seek((this.#clock?.frame ?? 0) + frames),
        (last) => this.seek(last ? (this.#clock?.lastFrame ?? 0) : 0),
        () => this.#glide.glideTo(this.#picking.selectedBounds()),
      ),
    );
    this.#view.start((seconds) => this.#tick(seconds));
    const context = new PluginContext(this.bus, this.#panels, this.#api, this.#view, this.#querySlot, info);
    await new PluginModules(importModule).register(info, context, (module) => this.#api.url(module));
    for (const part of [this.#sceneGraph, this.#perception, this.#segmind, this.#resolutions, this.#querySteps, this.#details, this.#livePanel]) this.#panels.add(part.panel);
    const panel = panelOf(this.#location.href);
    if (panel && this.#panels.identifiers().includes(panel)) this.#panels.show(panel);
    if (liveAddress) this.connectLive(liveAddress);
    else await this.show(choice.scene);
  }

  /**
   * Show a bundle; a scene still loading is cancelled.
   * @param {string} name The bundle's name.
   * @returns {Promise<void>} Settles once the scene is shown, has failed or was replaced.
   */
  async show(name) {
    this.#loading?.abort();
    const loading = new AbortController();
    this.#loading = loading;
    this.#status.loading(`Loading ${name}…`);
    this.bus.publish(ViewerEvent.SCENE_LOADING, name);
    const loader = new SceneLoader(this.#api);
    const progress = (loaded, total) => this.#status.loading(`Loading ${name}: ${loaded} of ${total} files`);
    const loaded = await loader.load(name, this.#view, this.#lighting, loading.signal, progress).catch((error) => {
      if (!loading.signal.aborted) this.#fail(`Could not show ${name}: ${error.message}`);
      return null;
    });
    if (!loaded) return;
    this.#highlights.clear();
    this.#scene?.dispose();
    this.#scene = loaded;
    this.#picking.select(null);
    loaded.objects.group.visible = this.#objectsShown;
    this.#layers.showObjects(loaded.objects.named().map(({ key, name }) => new LayerObject(key, name)));
    this.#labels.show(loaded.objects.named().map(({ key, name }) => ({ name, frame: loaded.objects.holderOf(key) })));
    this.#axes.show(framesOf(loaded));
    this.#follow.reset(loaded.robotPosition());
    this.#layers.refresh();
    this.#clock = new PlaybackClock(loaded.frames.framesPerSecond, loaded.frames.frameCount);
    const segments = loaded.description[SceneKey.SEGMENTS] ?? [];
    this.#cinematic.plan(ShotPlan.of(segments, loaded.frames.framesPerSecond, this.#clock.duration));
    this.#cinematic.setObstacles(loaded.models.environmentModels());
    this.#timeline.show(this.#clock, segments);
    this.#details.show(loaded);
    this.#sceneGraph.show(loaded);
    this.#perception.show(loaded, SceneKey.PLAN_TREES, (file) => this.#api.sceneFileUrl(loaded.name, file));
    this.#segmind.show(loaded);
    if (!this.#live) this.#querySteps.showRecorded(loaded.querySteps);
    this.#rememberScene(name);
    this.#elements.stageMessage.hidden = true;
    const missing = loaded.missingFiles().length;
    this.#status.ready(missing ? `Showing ${name}; ${missing} files could not be loaded (see Details)` : `Showing ${name}`);
    this.bus.publish(ViewerEvent.SCENE_LOADED, loaded);
  }

  /**
   * Show a bundle another part asked for, and let the scene picker show it as chosen.
   * @param {string} name The bundle's name.
   * @returns {Promise<void>} Settles once the scene is shown, has failed or was replaced.
   */
  showRequested(name) {
    if (!this.#choice.chooseScene(name)) {
      this.#fail(`No scenes directory holds a bundle named ${name}.`);
      return Promise.resolve();
    }
    this.#picker.render();
    return this.show(name);
  }

  /**
   * Follow a live publisher: show its world and apply its states as they arrive; a page
   * reads the publisher at the default address through the viewer.
   * @param {string} address The publisher's address.
   */
  connectLive(address) {
    this.disconnectLive();
    const client = new LiveClient(this.#fetch, readAddressOf(address, this.#api.url(ViewerRoute.LIVE_RELAY)));
    this.#live = new LiveSession(
      client,
      this.#timers,
      this.bus,
      new LiveListener(
        (scene) => this.show(scene).then(() => this.#live?.sceneShown()),
        (state) => this.#showLiveState(state),
        (plan) => this.#perception.showLive(plan, (key) => client.perceptionImageUrl(key)), // the questions plugin graphs the live plan and statechart
        () => {},
        (status) => {
          this.#recordingState = status[RecordingStatusKey.STATE];
          this.#livePanel.showRecording(status);
        },
        (resolutions) => this.#resolutions.show(resolutions, this.#live?.offers(Capability.RESOLUTION_EXECUTION) ?? false),
        (steps) => this.#querySteps.show(steps),
        () => this.#querySteps.revision,
        (detected) => this.#segmind.showLive(detected),
        (overlay) => this.#showMarkers(overlay),
        (topics) => this.#livePanel.showMarkerTopics(topics),
      ),
    );
    this.#livePanel.showConnecting(true, null, address);
    this.#liveBadge.show(LiveBadgeState.CONNECTING, address);
    this.#timeline.setLive(true, WAITING_NOTE);
    this.#status.loading(`Waiting for the live publisher at ${address}…`);
    this.#history.replaceState(null, '', withLive(this.#location.href, address));
    this.#live.start();
  }

  /**
   * Stop following the live publisher.
   */
  disconnectLive() {
    if (!this.#live) return;
    this.#live.stop();
    this.#live = null;
    this.#resolutions.stop();
    this.#querySteps.stop();
    this.#markers?.clear();
    this.#livePanel.showMarkerNamespaces([]);
    this.#livePanel.showConnecting(false, null);
    this.#elements.statusLine.dataset.live = 'false';
    this.#showLiveConnection(null);
  }

  /**
   * Stop capturing the live run, so the Live panel offers to name and keep it, and show
   * that panel.
   */
  saveEpisode() {
    if (this.#live && this.#recordingState === RecordingState.RECORDING) this.#live.client.send(LiveRoute.RECORDING_STOP);
    this.#panels.show(this.#livePanel.panel.identifier);
  }

  /**
   * @param {Object} state A live publisher's world state.
   */
  #showLiveState(state) {
    if (!this.#scene) return;
    this.#scene.showState(state);
    this.#markers.place(state[LiveStateKey.MARKERS_VERSION], state[LiveStateKey.MARKER_POSES] ?? {});
    this.#follow.follow(this.#scene.robotPosition());
    this.#picking.update();
    this.#highlights.update();
    this.#view.requestRender();
  }

  /**
   * Draw a live publisher's marker overlay and list its namespaces.
   * @param {Object} overlay The overlay.
   */
  #showMarkers(overlay) {
    this.#markers.show(overlay);
    this.#livePanel.showMarkerNamespaces(this.#markers.namespaces());
  }

  /**
   * @param {Object|null} info The publisher's information, or null when it stopped answering.
   */
  #showLiveConnection(info) {
    const connected = Boolean(this.#live && info);
    this.#timeline.setSaveOffered(connected && (info[LiveInfoKey.CAPABILITIES] ?? []).includes(Capability.RECORDING));
    if (!this.#live) {
      this.#liveBadge.show(LiveBadgeState.OFF);
      this.#timeline.setLive(false);
      return;
    }
    this.#liveBadge.show(connected ? LiveBadgeState.LIVE : LiveBadgeState.LOST);
    this.#timeline.setLive(true, connected ? undefined : LOST_NOTE);
    this.#livePanel.showConnecting(true, info);
    this.#elements.statusLine.dataset.live = String(Boolean(info));
    if (!info) this.#status.ready('The live publisher does not answer; showing the last state.');
  }

  /**
   * Show a frame of the current recording.
   * @param {number} frame The frame.
   */
  seek(frame) {
    if (!this.#clock) return;
    this.#clock.seek(frame);
    this.#cinematic.cut();
    this.#showFrame();
  }

  /**
   * Let playback time pass.
   * @param {number} seconds Wall-clock seconds since the previous call.
   * @returns {boolean} Whether the shown frame changed.
   */
  #tick(seconds) {
    const glided = this.#glide?.advance(seconds) ?? false;
    if (!this.#clock) return glided;
    this.#clock.speed = this.#timeline.speed * this.#cinematic.pace(this.#clock.time);
    if (!this.#clock.advance(seconds)) return glided;
    this.#showFrame();
    if (this.#filming && !this.#clock.playing) this.toggleVideo();
    return true;
  }

  // %% video

  /**
   * Film the recording from its start with the cinematic camera and save the video
   * once it played to its end, or stop filming early and save what was filmed.
   */
  toggleVideo() {
    if (this.#filming) {
      const { recording, cinematic } = this.#filming;
      this.#filming = null;
      this.#timeline.setPlaying(false);
      this.#cinematic.enabled = cinematic;
      this.#layers.refresh();
      this.#timeline.setFilming(false);
      this.#view.setSolidBackground(null);
      const name = this.#scene?.name ?? 'cramera';
      recording.stop().then((video) => saveVideo(this.#elements.document, video, name));
      this.#status.ready(`Filmed ${name}; the video is saved as a download.`);
      return;
    }
    const canvas = this.#view?.renderer.domElement;
    const window = this.#elements.document.defaultView;
    if (!this.#clock || this.#live || !canvas) return;
    if (!VideoRecording.supported(canvas, window)) {
      this.#status.ready('This browser cannot film the 3D view.');
      return;
    }
    const backdrop = window.getComputedStyle(this.#elements.stage).getPropertyValue(STAGE_COLOR).trim();
    this.#view.setSolidBackground(backdrop || DEFAULT_BACKDROP);
    this.#filming = {
      recording: new VideoRecording(canvas, window.MediaRecorder, (listener) => this.#view.onRender(listener)),
      cinematic: this.#cinematic.enabled,
    };
    this.#cinematic.enabled = true;
    this.#layers.refresh();
    this.#timeline.setFilming(true);
    this.seek(0);
    this.#filming.recording.start();
    this.#timeline.setPlaying(true);
  }

  /**
   * Pose the scene for the clock's frame and update the parts that show it.
   */
  #showFrame() {
    const frame = this.#clock.frame;
    this.#scene.showFrame(frame);
    const segment = this.#scene.frames.segmentAt(frame);
    this.#timeline.update(segment);
    const robot = this.#scene.robotPosition();
    const filmed = this.#cinematic.update(this.#clock.time, robot, (key) => this.#objectPosition(key));
    if (filmed) this.#follow.reset(robot);
    else this.#follow.follow(robot);
    this.#picking.update();
    this.#highlights.update();
    this.#view.requestRender();
    this.bus.publish(ViewerEvent.FRAME_CHANGED, frame);
  }

  /**
   * @param {SceneView} view The 3D view the overlay's switches change.
   */
  #addLayers(view) {
    this.#layers = new LayersPanel(
      this.#elements.layers,
      [
        new ViewSwitch('Objects', () => this.#objectsShown, (shown) => this.#showObjects(shown)),
        new ViewSwitch('TF frames', () => this.#axes.shown, (shown) => this.#axes.setShown(shown)),
        new ViewSwitch('ROS markers', () => this.#markers.shown, (shown) => this.#markers.setShown(shown)),
        new ViewSwitch('Object labels', () => this.#labels.shown, (shown) => this.#labels.setShown(shown)),
        new ViewSwitch('Floor shadow', () => view.ground.visible, (shown) => view.showGround(shown)),
        new ViewSwitch('Ambient occlusion', () => view.ambientOcclusion, (enabled) => view.setAmbientOcclusion(enabled)),
        new ViewSwitch('Follow robot', () => this.#follow.following, (following) => { this.#follow.following = following; }),
        new ViewSwitch('Cinematic camera', () => this.#cinematic.enabled, (enabled) => this.#filmWith(enabled)),
      ],
      (key) => this.bus.publish(ViewerEvent.ENTITY_HIGHLIGHTED, [key]),
    );
    this.#layers.setFolded(this.#elements.document.defaultView.matchMedia(NARROW_SCREEN).matches);
  }

  /**
   * @param {boolean} enabled Whether the cinematic camera films the recording.
   */
  #filmWith(enabled) {
    this.#cinematic.enabled = enabled;
    this.#cinematic.cut();
    if (enabled && this.#clock && this.#scene && !this.#live) this.#showFrame();
  }

  /**
   * @param {string} key An object's key.
   * @returns {THREE.Vector3|null} Where the object is in the world now, or null when the
   *   shown scene has no such object.
   */
  #objectPosition(key) {
    const holder = this.#scene?.objects.holderOf(key);
    return holder ? holder.getWorldPosition(new THREE.Vector3()) : null;
  }

  /**
   * @param {boolean} shown Whether the free objects are drawn.
   */
  #showObjects(shown) {
    this.#objectsShown = shown;
    if (this.#scene) this.#scene.objects.group.visible = shown;
    this.#view.requestRender();
  }

  /**
   * Put the shown scene into the page's address, so it can be shared and reloaded.
   * @param {string} name The bundle's name.
   */
  #rememberScene(name) {
    if (this.#live) return;
    this.#history.replaceState(null, '', withScene(this.#location.href, name));
  }

  /**
   * @param {string} message Why the page cannot show its scene.
   */
  #fail(message) {
    this.#status.failed(message);
    this.#elements.stageMessage.textContent = message;
    this.#elements.stageMessage.hidden = false;
    this.bus.publish(ViewerEvent.SCENE_FAILED, message);
  }
}
