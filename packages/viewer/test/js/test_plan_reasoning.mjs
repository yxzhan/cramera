import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import * as THREE from 'three';

import { EventBus } from '../../src/cramera/web/core/event-bus.js';
import { LiveListener, LiveSession } from '../../src/cramera/web/live/live-session.js';
import { askingChain, heldText, LINGER_MILLISECONDS, QueryStepActions, QueryStepsPanel, shownStep } from '../../src/cramera/web/panels/query-steps-panel.js';
import { KNOWLEDGE_SOURCES, rankedOutcomes, reasoningView, ruleState } from '../../src/cramera/web/panels/reasoning-view.js';
import { answeredParts, FRESH_SECONDS, planLineView, PlanReading, ResolutionActions, ResolutionsPanel } from '../../src/cramera/web/panels/resolutions-panel.js';
import {
  Capability,
  HeldStepKey,
  KnowledgeSource,
  LiveInfoKey,
  LiveRoute,
  OutcomeProbabilityKey,
  OutlineFitKey,
  PerceptionReasoningKey,
  PlanResolutionKey,
  PlanResolutionsKey,
  PresentationKey,
  QueryStepKey,
  QueryStepsKey,
  QueryVariableKey,
  ReasoningKey,
  RegionOutlineKey,
  ResolvedPartKey,
  RuleReasoningKey,
  SamplingReasoningKey,
  SceneHighlightKey,
  StepAdvanceKey,
  SteppingRequestKey,
  VariableRole,
  ViewerEvent,
} from '../../src/cramera/web/protocol.js';
import { CameraGlide, easedProgress, focusBounds, GlideSetting, RegionOutlines } from '../../src/cramera/web/scene/scene-focus.js';
import { FakeDocument } from './support/fake-document.mjs';

/**
 * @param {string} path A document of the protocol's test dataset.
 * @returns {Object} The document, read anew so a test may change it.
 */
function protocolDocument(path) {
  return JSON.parse(readFileSync(new URL(`../../../protocol/test/dataset/${path}`, import.meta.url), 'utf8'));
}

const RESOLUTIONS = () => protocolDocument('live/resolutions.json');
const QUERY_STEPS = () => protocolDocument('live/query_steps.json');

/**
 * Timers that fire only when the test says so.
 */
class ManualTimers {
  constructor() {
    this.pending = new Map();
    this.next = 1;
  }

  setTimeout(callback, milliseconds) {
    const id = this.next++;
    this.pending.set(id, { callback, milliseconds });
    return id;
  }

  clearTimeout(id) {
    this.pending.delete(id);
  }

  /**
   * Fire the timers pending now, in the order they were set.
   * @returns {Array<number>} How long each fired timer was set for.
   */
  fire() {
    const due = [...this.pending.values()];
    this.pending.clear();
    for (const { callback } of due) callback();
    return due.map(({ milliseconds }) => milliseconds);
  }
}

/**
 * @param {EventBus} bus A bus.
 * @returns {Array<Array>} Every scene event published on it, as `[event, payload]`.
 */
function sceneEvents(bus) {
  const published = [];
  for (const event of [ViewerEvent.ENTITY_HIGHLIGHTED, ViewerEvent.REGIONS_OUTLINED, ViewerEvent.FOCUS_REQUESTED]) {
    bus.subscribe(event, (payload) => published.push([event, payload]));
  }
  return published;
}

/**
 * @param {Object} highlight What the scene shows of an answer.
 * @returns {Array<Array>} The scene events showing it.
 */
function eventsShowing(highlight) {
  const events = [
    [ViewerEvent.ENTITY_HIGHLIGHTED, highlight[SceneHighlightKey.KEYS]],
    [ViewerEvent.REGIONS_OUTLINED, highlight[SceneHighlightKey.REGIONS]],
  ];
  if (highlight[SceneHighlightKey.FRAMED].length) events.push([ViewerEvent.FOCUS_REQUESTED, highlight[SceneHighlightKey.FRAMED]]);
  return events;
}

/**
 * The scene events clearing what was shown.
 */
const CLEARED = Object.freeze([
  [ViewerEvent.ENTITY_HIGHLIGHTED, []],
  [ViewerEvent.REGIONS_OUTLINED, []],
]);

// %% regions and the camera

/**
 * @returns {Object} Just enough of a scene view for the region boxes and the glide.
 */
function fakeView() {
  const camera = new THREE.PerspectiveCamera();
  camera.position.set(2, 0, 1);
  const controls = { target: new THREE.Vector3(0, 0, 1) };
  return {
    scene: new THREE.Scene(),
    camera,
    controls,
    renders: 0,
    requestRender() {
      this.renders += 1;
    },
    lookFrom(position, target) {
      camera.position.copy(position);
      controls.target.copy(target);
    },
  };
}

const OUTLINE = Object.freeze({
  [RegionOutlineKey.IDENTIFIER]: 'drop_zone',
  [RegionOutlineKey.POSE]: [1.1, 0.0, 0.95, 0.0, 0.0, 0.0, 1.0],
  [RegionOutlineKey.SIZE]: [0.2, 0.1, 0.05],
});

test('a region is drawn as a box of its size where its outline is', () => {
  const outlines = new RegionOutlines(fakeView());
  outlines.show([OUTLINE]);
  const [holder] = outlines.group.children;
  assert.equal(holder.name, OUTLINE[RegionOutlineKey.IDENTIFIER]);
  assert.deepEqual(holder.position.toArray(), OUTLINE[RegionOutlineKey.POSE].slice(0, 3));
  const { width, height, depth } = holder.children[0].geometry.parameters;
  assert.deepEqual([width, height, depth], OUTLINE[RegionOutlineKey.SIZE]);
});

test('showing no regions removes the boxes drawn before', () => {
  const outlines = new RegionOutlines(fakeView());
  outlines.show([OUTLINE]);
  outlines.show([]);
  assert.equal(outlines.group.children.length, 0);
});

test('a glide eases in and out between its ends', () => {
  assert.deepEqual([easedProgress(0), easedProgress(0.5), easedProgress(1)], [0, 0.5, 1]);
});

test('the camera glides onto a region and stops there', () => {
  const view = fakeView();
  const outlines = new RegionOutlines(view);
  outlines.show([OUTLINE]);
  const glide = new CameraGlide(view);
  const box = focusBounds(new THREE.Group(), outlines.group, [OUTLINE[RegionOutlineKey.IDENTIFIER]]);
  assert.equal(glide.glideTo(box, 1), true);
  glide.advance(0.5);
  assert.equal(glide.gliding, true);
  glide.advance(0.5);
  assert.equal(glide.gliding, false);
  assert.deepEqual(view.controls.target.toArray(), box.getCenter(new THREE.Vector3()).toArray());
});

test('nothing to glide onto starts no glide', () => {
  const view = fakeView();
  const glide = new CameraGlide(view);
  assert.equal(glide.glideTo(focusBounds(new THREE.Group(), new THREE.Group(), ['missing'])), false);
  assert.equal(glide.advance(1), false);
});

test('a double click on a small body glides near enough to frame it', () => {
  const view = fakeView();
  const glide = new CameraGlide(view);
  const box = new THREE.Box3(new THREE.Vector3(0.5, 0.3, 0.9), new THREE.Vector3(0.55, 0.35, 0.95));
  glide.glideOnto(box, new THREE.Vector3(0.5, 0.32, 0.92));
  glide.advance(GlideSetting.SECONDS);
  assert.deepEqual(view.controls.target.toArray(), box.getCenter(new THREE.Vector3()).toArray());
  assert.ok(Math.abs(view.camera.position.distanceTo(view.controls.target) - GlideSetting.NEAREST) < 1e-9);
});

test('a double click on a body too big to frame from here glides onto the point hit, as far away as before', () => {
  const view = fakeView();
  const glide = new CameraGlide(view);
  const point = new THREE.Vector3(-1, 3, 0);
  glide.glideOnto(new THREE.Box3(new THREE.Vector3(-4, -4, 0), new THREE.Vector3(4, 4, 0.1)), point);
  glide.advance(GlideSetting.SECONDS);
  assert.deepEqual(view.controls.target.toArray(), point.toArray());
  assert.deepEqual(view.camera.position.toArray(), [1, 3, 0]);
});

test('a double click on what belongs to no body glides onto the point hit', () => {
  const view = fakeView();
  const glide = new CameraGlide(view);
  glide.glideOnto(new THREE.Box3(), new THREE.Vector3(0, 1, 1));
  glide.advance(GlideSetting.SECONDS);
  assert.deepEqual(view.controls.target.toArray(), [0, 1, 1]);
  assert.deepEqual(view.camera.position.toArray(), [2, 1, 1]);
});

// %% reasoning

/**
 * @param {Object} reasoning A backend's reasoning.
 * @param {Array<string>} answers Its answers.
 * @returns {Object} The drawing.
 */
const drawn = (reasoning, answers = []) => reasoningView(new FakeDocument(), reasoning, answers, [], 1000);

test('each kind of reasoning is drawn as its source', () => {
  const parts = RESOLUTIONS()[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.PARTS];
  for (const part of parts) {
    const reasoning = part[ResolvedPartKey.REASONING];
    assert.equal(drawn(reasoning).dataset.source, reasoning[ReasoningKey.SOURCE]);
  }
});

test('perception lists every candidate with how well it fits', () => {
  const reasoning = RESOLUTIONS()[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.PARTS][0][ResolvedPartKey.REASONING];
  const rows = drawn(reasoning).findAll((element) => element.tagName === 'LI');
  assert.deepEqual(
    rows.map((row) => row.className),
    reasoning[PerceptionReasoningKey.CANDIDATES].map((candidate) => (candidate[OutlineFitKey.SELECTED] ? 'answers fades-in' : 'rejected fades-in')),
  );
});

test('a rule cascade marks how far each rule was taken', () => {
  const reasoning = RESOLUTIONS()[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.PARTS][3][ResolvedPartKey.REASONING];
  const rules = drawn(reasoning).findAll((element) => (element.className ?? '').startsWith('rule '));
  assert.deepEqual(
    rules.map((rule) => rule.className),
    reasoning[RuleReasoningKey.RULES].map((rule) => `rule ${ruleState(rule)} fades-in`),
  );
});

test('sampled outcomes rank by the answers, else by their probability', () => {
  const reasoning = RESOLUTIONS()[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.PARTS][2][ResolvedPartKey.REASONING];
  assert.deepEqual(rankedOutcomes(reasoning, ['BACK']), ['BACK']);
  const byProbability = [...reasoning[SamplingReasoningKey.PRIOR]].sort((first, second) => second[OutcomeProbabilityKey.PROBABILITY] - first[OutcomeProbabilityKey.PROBABILITY]);
  assert.deepEqual(rankedOutcomes(reasoning, []), byProbability.map((outcome) => outcome[OutcomeProbabilityKey.OUTCOME]));
});

test('a backend that does not say how it reasoned shows its evidence', () => {
  const view = reasoningView(new FakeDocument(), null, [], ['outline fits 0.93'], 1000);
  assert.equal(view.innerText, 'outline fits 0.93');
});

test('every kind of knowledge has its symbol', () => {
  assert.deepEqual(Object.keys(KNOWLEDGE_SOURCES).sort(), Object.values(KnowledgeSource).sort());
});

// %% plan resolutions

/**
 * @param {Object} resolutions The published resolutions.
 * @returns {number} A time in seconds just after its last part was answered.
 */
function justAfter(resolutions) {
  const parts = resolutions[PlanResolutionsKey.RESOLUTIONS].at(-1)[PlanResolutionKey.PARTS];
  return parts.at(-1)[ResolvedPartKey.ANSWERED_AT] + 1;
}

function openResolutions(now) {
  const bus = new EventBus();
  const timers = new ManualTimers();
  const executed = [];
  const panel = new ResolutionsPanel(new FakeDocument(), bus, new ResolutionActions((identifier) => executed.push(identifier)), timers, () => now);
  return { panel, timers, executed, published: sceneEvents(bus) };
}

test('a plan line marks its open parts as stated and fills them as resolved', () => {
  const resolution = RESOLUTIONS()[PlanResolutionsKey.RESOLUTIONS][0];
  const line = resolution[PlanResolutionKey.RESOLVED][0];
  const document = new FakeDocument();
  const answered = answeredParts(resolution, 1);
  const [part] = answered.values();
  assert.equal(planLineView(document, line, new Map(), PlanReading.STATED).innerText, 'piece = …');
  assert.equal(planLineView(document, line, answered, PlanReading.STATED).innerText, 'piece = ✓');
  assert.equal(planLineView(document, line, answered, PlanReading.RESOLVED).innerText, `piece = ${part[ResolvedPartKey.ANSWERS][0]}`);
});

test('a fresh resolution plays one part after the other, lighting up what each is about', () => {
  const resolutions = RESOLUTIONS();
  const { panel, timers, published } = openResolutions(justAfter(resolutions));
  panel.show(resolutions, false);
  const resolution = resolutions[PlanResolutionsKey.RESOLUTIONS][0];
  assert.equal(panel.playing, resolution[PlanResolutionKey.IDENTIFIER]);
  assert.deepEqual(timers.fire(), [resolutions[PlanResolutionsKey.PRESENTATION][PresentationKey.BEFORE]]);
  assert.deepEqual(published, eventsShowing(resolution[PlanResolutionKey.PARTS][0][ResolvedPartKey.HIGHLIGHT]));
  for (let part = 1; part <= resolution[PlanResolutionKey.PARTS].length; part += 1) timers.fire();
  assert.deepEqual(timers.fire(), [resolutions[PlanResolutionsKey.PRESENTATION][PresentationKey.AFTER]]);
  assert.equal(panel.playing, null);
  assert.deepEqual(published.slice(-CLEARED.length), CLEARED);
});

test('a resolution answered long ago is not played on its own', () => {
  const resolutions = RESOLUTIONS();
  const { panel } = openResolutions(justAfter(resolutions) + FRESH_SECONDS);
  panel.show(resolutions, false);
  assert.equal(panel.playing, null);
});

test('no resolution plays while another part shows the scene', () => {
  const resolutions = RESOLUTIONS();
  const { panel } = openResolutions(justAfter(resolutions));
  panel.holdScene(true);
  panel.show(resolutions, false);
  assert.equal(panel.playing, null);
  panel.holdScene(false);
  assert.equal(panel.playing, resolutions[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.IDENTIFIER]);
});

test('a resolved plan is carried out only where the publisher offers it', () => {
  const resolutions = RESOLUTIONS();
  const old = justAfter(resolutions) + FRESH_SECONDS;
  const carryOut = (panel) => panel.panel.element.findAll((element) => element.textContent === 'Carry it out');
  const withoutExecution = openResolutions(old);
  withoutExecution.panel.show(resolutions, false);
  assert.deepEqual(carryOut(withoutExecution.panel), []);
  const { panel, executed } = openResolutions(old);
  panel.show(resolutions, true);
  carryOut(panel)[0].click();
  assert.deepEqual(executed, [resolutions[PlanResolutionsKey.RESOLUTIONS][0][PlanResolutionKey.IDENTIFIER]]);
});

// %% query steps

function openSteps() {
  const bus = new EventBus();
  const timers = new ManualTimers();
  const requests = [];
  const inspecting = [];
  const panel = new QueryStepsPanel(
    new FakeDocument(),
    bus,
    new QueryStepActions(
      (request) => requests.push(['advance', request]),
      (request) => requests.push(['stepping', request]),
    ),
    timers,
    (shown) => inspecting.push(shown),
  );
  return { panel, timers, requests, inspecting, published: sceneEvents(bus) };
}

test('the step shown is the one picked, else the one the process is held at', () => {
  const steps = QUERY_STEPS();
  const held = steps[QueryStepsKey.HELD][HeldStepKey.NUMBER];
  assert.equal(shownStep(steps, null)[QueryStepKey.NUMBER], held);
  assert.equal(shownStep(steps, 0)[QueryStepKey.NUMBER], 0);
  assert.equal(shownStep({ ...steps, [QueryStepsKey.HELD]: null }, null), null);
});

test('a nested query is asked inside the queries that asked it, outermost first', () => {
  const steps = QUERY_STEPS()[QueryStepsKey.STEPS];
  assert.deepEqual(askingChain(steps, steps[1]), [steps[0]]);
  assert.deepEqual(askingChain(steps, steps[0]), []);
});

test('a variable says what it holds, or how far its query looked', () => {
  const [sought, given, rangesOver] = QUERY_STEPS()[QueryStepsKey.STEPS][0][QueryStepKey.VARIABLES];
  assert.equal(heldText(sought, true), sought[QueryVariableKey.VALUES].join(', '));
  assert.equal(heldText(given, true), given[QueryVariableKey.VALUES].join(', '));
  assert.equal(heldText(rangesOver, true), `${rangesOver[QueryVariableKey.LOOKED_AT]} looked at`);
  assert.equal(heldText({ [QueryVariableKey.NAME]: 'x', [QueryVariableKey.ROLE]: VariableRole.SOUGHT }, true), 'no answer');
});

test('a held process is inspected and moved on from the step the viewer saw', () => {
  const steps = QUERY_STEPS();
  const { panel, requests, inspecting } = openSteps();
  panel.show(steps);
  assert.equal(panel.inspecting, true);
  assert.deepEqual(inspecting, [true]);
  assert.equal(panel.next.disabled, false);
  panel.advance();
  assert.deepEqual(requests, [['advance', { [StepAdvanceKey.HELD]: steps[QueryStepsKey.HELD] }]]);
});

test('an answered step lights up what its answers are about', () => {
  const steps = QUERY_STEPS();
  const { panel, published } = openSteps();
  panel.show(steps);
  panel.pick(0);
  assert.deepEqual(published.slice(-3), eventsShowing(steps[QueryStepsKey.STEPS][0][QueryStepKey.HIGHLIGHT]));
});

test('steps the viewer shows already change nothing', () => {
  const steps = QUERY_STEPS();
  const { panel } = openSteps();
  panel.show(steps);
  panel.show({ [QueryStepsKey.REVISION]: steps[QueryStepsKey.REVISION], [QueryStepsKey.UNCHANGED]: true, [QueryStepsKey.STEPS]: [] });
  assert.equal(panel.revision, steps[QueryStepsKey.REVISION]);
  assert.equal(panel.published, steps);
});

test('running to the end stops holding the process and closes the inspector', () => {
  const { panel, requests, inspecting } = openSteps();
  panel.show(QUERY_STEPS());
  panel.runToTheEnd();
  assert.deepEqual(requests, [['stepping', { [SteppingRequestKey.STEPPING]: false, [SteppingRequestKey.HOLDS_NESTED]: panel.nested.checked }]]);
  assert.deepEqual(inspecting, [true, false]);
});

test('the inspector lingers on a step the process carried on from, then closes', () => {
  const steps = QUERY_STEPS();
  const { panel, timers } = openSteps();
  panel.show(steps);
  panel.show({ ...steps, [QueryStepsKey.REVISION]: steps[QueryStepsKey.REVISION] + 1, [QueryStepsKey.HELD]: null });
  assert.equal(panel.inspecting, true);
  assert.deepEqual(timers.fire(), [LINGER_MILLISECONDS]);
  assert.equal(panel.inspecting, false);
});

test('a recording is stepped through one query after the other without a process', () => {
  const steps = { ...QUERY_STEPS(), [QueryStepsKey.HELD]: null, [QueryStepsKey.STEPPING]: false };
  const numbers = steps[QueryStepsKey.STEPS].map((step) => step[QueryStepKey.NUMBER]);
  const { panel, requests, inspecting } = openSteps();
  panel.showRecorded(steps);
  assert.equal(panel.recorded, true);
  assert.equal(panel.stepping.parentElement.hidden, true);
  assert.equal(panel.next.disabled, false);
  const shown = numbers.map(() => {
    panel.advance();
    return panel.picked;
  });
  assert.deepEqual(shown, numbers);
  assert.equal(panel.next.disabled, true);
  assert.deepEqual(requests, []);
  assert.deepEqual(inspecting, [true]);
});

test('a scene without recorded queries clears the steps shown before', () => {
  const { panel } = openSteps();
  panel.showRecorded(QUERY_STEPS());
  panel.showRecorded(null);
  assert.equal(panel.published, null);
  assert.equal(panel.recorded, false);
  assert.equal(panel.next.disabled, true);
  assert.equal(panel.stepping.parentElement.hidden, false);
});

test('a running process brings back the controls that hold it', () => {
  const { panel } = openSteps();
  panel.showRecorded(QUERY_STEPS());
  panel.show(QUERY_STEPS());
  assert.equal(panel.recorded, false);
  assert.equal(panel.stepping.parentElement.hidden, false);
});

// %% polling the publisher

test('resolutions and query steps are read only where the publisher offers them', async () => {
  const shown = [];
  const revisions = [];
  const steps = QUERY_STEPS();
  const client = {
    read: async (route) => (route === LiveRoute.RESOLUTIONS ? RESOLUTIONS() : null),
    readQuerySteps: async (revision) => {
      revisions.push(revision);
      return steps;
    },
  };
  const listener = new LiveListener(
    () => {},
    () => {},
    () => {},
    () => {},
    () => {},
    (resolutions) => shown.push(['resolutions', resolutions]),
    (published) => shown.push(['steps', published]),
    () => steps[QueryStepsKey.REVISION],
  );
  const session = new LiveSession(client, { setInterval: () => 0, clearInterval: () => {} }, new EventBus(), listener);
  session.info = { [LiveInfoKey.CAPABILITIES]: [] };
  await session.pollResolutions();
  await session.pollQuerySteps();
  assert.deepEqual(shown, []);
  session.info = { [LiveInfoKey.CAPABILITIES]: [Capability.RESOLUTIONS, Capability.QUERY_STEPS] };
  await session.pollResolutions();
  await session.pollQuerySteps();
  assert.deepEqual(shown, [
    ['resolutions', RESOLUTIONS()],
    ['steps', steps],
  ]);
  assert.deepEqual(revisions, [steps[QueryStepsKey.REVISION]]);
});
