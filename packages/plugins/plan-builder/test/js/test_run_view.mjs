import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  MonitorObservation,
  PerceptionImageKey,
  PlanStepKey,
  RunEventKey,
  RunEventKind,
  RunMonitor,
  RunState,
  RunStatusKey,
  StepState,
} from 'cramera-viewer/protocol.js';
import { Elements } from '../../src/cramera_plan_builder/web/builder/elements.js';
import { MONITOR_TEXTS, RunProgress, RunView, RunViewClass, STOPPED_STEP, unseenSentence } from '../../src/cramera_plan_builder/web/builder/run-view.js';
import { FakeDocument } from 'cramera-testing/fake-document.mjs';

/**
 * Event sequences recorded from runs in MuJoCo: the sort template run through the plan
 * builder, a run whose red cube was nudged once detected and whose blue cube was taken
 * away, and a pick a low workspace stopped.
 */
const RECORDED = JSON.parse(readFileSync(new URL('dataset/run_events.json', import.meta.url), 'utf8'));

/**
 * @param {string} name A recorded run.
 * @returns {RunProgress} Its progress, its steps labelled by their identities.
 */
const progressOf = (name) => {
  const recorded = RECORDED[name];
  const steps = recorded.steps.map((identifier) => ({ [PlanStepKey.IDENTIFIER]: identifier }));
  return RunProgress.of(steps, (step) => `label of ${step[PlanStepKey.IDENTIFIER]}`, { [RunStatusKey.EVENTS]: recorded.events });
};

/**
 * @param {RunProgress} progress A run's progress.
 * @param {Array<Object>=} images The camera images shown.
 * @returns {import('cramera-testing/fake-document.mjs').FakeElement} The rendered view.
 */
const rendered = (progress, images = []) => {
  const view = new RunView(new Elements(new FakeDocument()));
  view.render(progress, images);
  return view.element;
};

const byClass = (root, name) => root.findAll((element) => element.className === name);

// %% progress

test('a sorted run ends with every step done and each cube perceived in its color', () => {
  const progress = progressOf('sort');
  assert.ok(progress.steps().every((step) => step.state === StepState.DONE));
  assert.deepEqual(
    progress.perceived.map((seen) => [seen.target, seen.color]),
    [
      ['red_cube', 'red'],
      ['yellow_cube', 'yellow'],
      ['blue_cube', 'blue'],
    ],
  );
  assert.equal(progress.safety, MonitorObservation.HOLDS);
  assert.match(progress.summary(), /^13 of 13 steps done\.$/);
});

test('a step retried after its cube moved is done on its second attempt, with the reason logged', () => {
  const progress = progressOf('retry');
  const pick = progress.step('step_3');
  assert.equal(pick.state, StepState.DONE);
  assert.equal(pick.attempt, 2);
  assert.ok(pick.arm);
  assert.deepEqual(pick.badges(), [MONITOR_TEXTS[RunMonitor.LOOK_AT][MonitorObservation.HOLDS]]);
  assert.ok(progress.log.some((line) => line.startsWith('3. label of step_3: attempt 1/3 failed, retrying: red_cube lies')));
});

test('a step that used up its attempts fails the run and the summary says why', () => {
  const progress = progressOf('retry');
  const pick = progress.step('step_5');
  assert.equal(pick.state, StepState.FAILED);
  assert.equal(pick.attempt, 3);
  assert.match(pick.reason, /blue_cube was not detected on the table again/);
  assert.match(progress.summary(), /^Aborted at 5\. label of step_5: blue_cube was not detected/);
  assert.equal(progress.step('step_6').state, StepState.PENDING);
});

test('a safety stop fails its step, violates the safety monitor and is logged', () => {
  const progress = progressOf('safetyStop');
  assert.equal(progress.step('step_2').state, StepState.FAILED);
  assert.equal(progress.safety, MonitorObservation.VIOLATED);
  assert.equal(progress.safetyStop, 'Left arm workspace');
  assert.match(progress.summary(), /^Stopped by a safety limit in 2\. label of step_2: Left arm workspace\./);
  assert.equal(progress.log.at(-1), 'Safety stop in 2. label of step_2: Left arm workspace; the robot holds.');
});

test('a step grounded again where its cube was found elsewhere says so in one line, with no retry', () => {
  const step = (state) => ({
    [RunEventKey.KIND]: RunEventKind.STEP,
    [RunEventKey.STEP]: 'step_3',
    [RunEventKey.STATE]: state,
    [RunEventKey.ATTEMPT]: 1,
    [RunEventKey.ATTEMPTS]: 3,
  });
  const events = [
    step(StepState.RUNNING),
    { [RunEventKey.KIND]: RunEventKind.REGROUNDED, [RunEventKey.STEP]: 'step_3', [RunEventKey.TARGET]: 'red_cube' },
    step(StepState.RUNNING),
    step(StepState.DONE),
  ];
  const progress = RunProgress.of(
    [{ [PlanStepKey.IDENTIFIER]: 'step_3' }],
    (shown) => `label of ${shown[PlanStepKey.IDENTIFIER]}`,
    { [RunStatusKey.EVENTS]: events },
  );
  assert.equal(progress.step('step_3').state, StepState.DONE);
  assert.equal(progress.step('step_3').attempt, 1);
  assert.ok(progress.log.includes(
    '1. label of step_3: red cube found elsewhere on the table; the step was grounded again where it lies',
  ), progress.log);
});

test('a step acting on an object not seen says so in the log, where it acts', () => {
  const events = [{
    [RunEventKey.KIND]: RunEventKind.CONTINUED_UNSEEN,
    [RunEventKey.STEP]: 'step_2',
    [RunEventKey.TARGET]: 'red_cube',
    [RunEventKey.REASON]: 'red_cube was not seen',
  }];
  const progress = RunProgress.of(
    [{ [PlanStepKey.IDENTIFIER]: 'step_1' }, { [PlanStepKey.IDENTIFIER]: 'step_2' }],
    (shown) => `label of ${shown[PlanStepKey.IDENTIFIER]}`,
    { [RunStatusKey.EVENTS]: events },
  );
  assert.ok(progress.log.includes(
    '2. label of step_2: red_cube was not seen; continuing where the plan expected red cube',
  ), progress.log);
});

test('a finished run whose step went ahead without the camera confirming its object says so in its summary and on the step', () => {
  const step = (identifier, state) => ({ [RunEventKey.KIND]: RunEventKind.STEP, [RunEventKey.STEP]: identifier, [RunEventKey.STATE]: state });
  const events = [
    step('step_1', StepState.DONE),
    { [RunEventKey.KIND]: RunEventKind.CONTINUED_UNSEEN, [RunEventKey.STEP]: 'step_2', [RunEventKey.TARGET]: 'red_cube', [RunEventKey.REASON]: 'RoboKudo did not answer' },
    step('step_2', StepState.DONE),
  ];
  const progress = RunProgress.of(
    [{ [PlanStepKey.IDENTIFIER]: 'step_1' }, { [PlanStepKey.IDENTIFIER]: 'step_2' }],
    (shown) => (shown[PlanStepKey.IDENTIFIER] === 'step_1' ? 'Park arms' : 'Pick red cube'),
    { [RunStatusKey.EVENTS]: events },
  );
  assert.deepEqual(progress.unseenSteps().map((shown) => shown.identifier), ['step_2']);
  assert.equal(progress.summary(), '2 of 2 steps done. 1 step went ahead without the camera confirming its object: 2. Pick red cube (red cube).');
  const view = rendered(progress);
  const [, pick] = byClass(view, RunViewClass.STEP);
  assert.equal(pick.dataset.unseen, 'true');
  assert.deepEqual(byClass(pick, RunViewClass.UNSEEN).map((element) => element.textContent), ['Went ahead without the camera confirming red cube: it acted where the plan expected it.']);
  const [park] = byClass(view, RunViewClass.STEP);
  assert.equal(park.dataset.unseen, undefined);
});

test('several steps that went ahead unseen are counted and named', () => {
  const unseen = (identifier, target) => ({ [RunEventKey.KIND]: RunEventKind.CONTINUED_UNSEEN, [RunEventKey.STEP]: identifier, [RunEventKey.TARGET]: target, [RunEventKey.REASON]: `${target} was not seen` });
  const progress = RunProgress.of(
    ['a', 'b', 'c'].map((identifier) => ({ [PlanStepKey.IDENTIFIER]: identifier })),
    (shown) => `Pick ${shown[PlanStepKey.IDENTIFIER]}`,
    { [RunStatusKey.EVENTS]: [unseen('a', 'red_cube'), unseen('a', 'red_cube'), unseen('c', 'blue_cube')] },
  );
  assert.equal(unseenSentence(progress.unseenSteps()), '2 steps went ahead without the camera confirming their objects: 1. Pick a (red cube), 3. Pick c (blue cube).');
  assert.equal(unseenSentence([]), '');
});

test('the step a person stopped reads as stopped, not running', () => {
  const step = (identifier, state) => ({ [RunEventKey.KIND]: RunEventKind.STEP, [RunEventKey.STEP]: identifier, [RunEventKey.STATE]: state, [RunEventKey.ATTEMPT]: 1, [RunEventKey.ATTEMPTS]: 3 });
  const events = [step('step_1', StepState.DONE), step('step_2', StepState.RUNNING)];
  const steps = ['step_1', 'step_2', 'step_3'].map((identifier) => ({ [PlanStepKey.IDENTIFIER]: identifier }));
  const label = (shown) => `label of ${shown[PlanStepKey.IDENTIFIER]}`;
  for (const state of [RunState.STOPPED, RunState.HALT_UNCONFIRMED]) {
    const progress = RunProgress.of(steps, label, { [RunStatusKey.STATE]: state, [RunStatusKey.EVENTS]: events });
    assert.equal(progress.step('step_2').state, STOPPED_STEP);
    assert.equal(progress.summary(), 'Stopped at 2. label of step_2. 1 of 3 steps done.');
    const view = rendered(progress);
    const stopped = byClass(view, RunViewClass.STEP)[1];
    assert.equal(stopped.dataset.state, STOPPED_STEP);
    assert.equal(byClass(stopped, RunViewClass.STATE)[0].textContent, 'stopped');
  }
  const running = RunProgress.of(steps, label, { [RunStatusKey.STATE]: RunState.RUNNING, [RunStatusKey.EVENTS]: events });
  assert.equal(running.step('step_2').state, StepState.RUNNING);
  assert.equal(running.summary(), 'Running 2. label of step_2. 1 of 3 steps done.');
});

test('every event is a line of the log', () => {
  for (const name of Object.keys(RECORDED)) assert.equal(progressOf(name).log.length, RECORDED[name].events.length);
});

// %% rendering

test('the step list shows each step with its state, arm and attempt', () => {
  const view = rendered(progressOf('retry'));
  const steps = byClass(view, RunViewClass.STEP);
  assert.deepEqual(
    steps.map((step) => [step.dataset.step, step.dataset.state]),
    [
      ['step_1', StepState.DONE],
      ['step_2', StepState.DONE],
      ['step_3', StepState.DONE],
      ['step_4', StepState.DONE],
      ['step_5', StepState.FAILED],
      ['step_6', StepState.PENDING],
    ],
  );
  const [, , pick, , failed] = steps;
  assert.equal(byClass(pick, RunViewClass.ATTEMPT)[0].textContent, 'attempt 2/3');
  assert.match(byClass(pick, RunViewClass.ARM)[0].textContent, /^(left|right) arm$/);
  assert.match(byClass(failed, RunViewClass.REASON)[0].textContent, /was not detected on the table again/);
});

test('monitor badges show the look-at of a step and the safety monitor of the run', () => {
  const retried = rendered(progressOf('retry'));
  const [lookAt] = byClass(retried, RunViewClass.BADGE);
  assert.equal(lookAt.textContent, MONITOR_TEXTS[RunMonitor.LOOK_AT][MonitorObservation.HOLDS]);
  assert.equal(lookAt.dataset.observation, MonitorObservation.HOLDS);
  const stopped = rendered(progressOf('safetyStop'));
  const [safety] = byClass(byClass(stopped, RunViewClass.BADGES)[0], RunViewClass.BADGE);
  assert.equal(safety.textContent, MONITOR_TEXTS[RunMonitor.SAFETY][MonitorObservation.VIOLATED]);
  assert.equal(safety.dataset.observation, MonitorObservation.VIOLATED);
});

test('the perceived cubes show their colors and the log every event', () => {
  const progress = progressOf('sort');
  const view = rendered(progress);
  assert.deepEqual(
    byClass(view, RunViewClass.OBJECT).map((chip) => chip.dataset.color),
    ['red', 'yellow', 'blue'],
  );
  const log = byClass(view, RunViewClass.LOG)[0];
  assert.deepEqual(
    log.children.map((line) => line.textContent),
    progress.log,
  );
});

test('the monitor badges say in plain words what each monitor observes', () => {
  assert.deepEqual(MONITOR_TEXTS, {
    [RunMonitor.LOOK_AT]: {
      [MonitorObservation.HOLDS]: 'object in the camera\'s view ✓',
      [MonitorObservation.VIOLATED]: 'object out of the camera\'s view ✗',
    },
    [RunMonitor.SAFETY]: {
      [MonitorObservation.HOLDS]: 'within the safety limits ✓',
      [MonitorObservation.VIOLATED]: 'a safety limit was broken ✗',
    },
  });
});

test('the run log and the perceived objects name each object as the step cards do', () => {
  const events = [
    { [RunEventKey.KIND]: RunEventKind.PERCEIVED, [RunEventKey.STEP]: 'step_1', [RunEventKey.TARGET]: 'red_cube', [RunEventKey.COLOR]: 'red' },
    { [RunEventKey.KIND]: RunEventKind.MONITOR, [RunEventKey.STEP]: 'step_1', [RunEventKey.MONITOR]: RunMonitor.LOOK_AT, [RunEventKey.TARGET]: 'red_cube', [RunEventKey.OBSERVATION]: MonitorObservation.HOLDS },
    { [RunEventKey.KIND]: RunEventKind.CONTINUED_UNSEEN, [RunEventKey.STEP]: 'step_1', [RunEventKey.TARGET]: 'red_cube, blue_cube', [RunEventKey.REASON]: 'RoboKudo did not answer' },
  ];
  const progress = RunProgress.of(
    [{ [PlanStepKey.IDENTIFIER]: 'step_1' }],
    () => 'Pick',
    { [RunStatusKey.EVENTS]: events },
  );
  assert.deepEqual(progress.log, [
    '1. Pick: perceived red cube (red)',
    `1. Pick: ${MONITOR_TEXTS[RunMonitor.LOOK_AT][MonitorObservation.HOLDS]} (red cube)`,
    '1. Pick: RoboKudo did not answer; continuing where the plan expected red cube, blue cube',
  ]);
  const view = rendered(progress);
  assert.deepEqual(byClass(view, RunViewClass.OBJECT).map((chip) => chip.innerText), ['red cube (red)']);
  assert.deepEqual(byClass(view, RunViewClass.UNSEEN).map((element) => element.textContent), ['Went ahead without the camera confirming red cube, blue cube: it acted where the plan expected them.']);
});

test('the camera panel shows the latest perception images or says none was taken', () => {
  const none = rendered(progressOf('sort'));
  assert.match(byClass(none, RunViewClass.CAMERA)[0].innerText, /No camera image yet/);
  const image = { [PerceptionImageKey.TITLE]: 'camera', [PerceptionImageKey.FILE]: 'node_4_0', url: '/live/perception_image?key=node_4_0' };
  const camera = byClass(rendered(progressOf('sort'), [image]), RunViewClass.CAMERA)[0];
  const [picture] = camera.findAll((element) => element.tagName === 'IMG');
  assert.equal(picture.src, image.url);
  assert.match(camera.innerText, /Latest perception: camera/);
});

test('an object perceived at several steps is listed once, in the color it showed last', () => {
  const perceived = (step, target, color) => ({ [RunEventKey.KIND]: RunEventKind.PERCEIVED, [RunEventKey.STEP]: step, [RunEventKey.TARGET]: target, [RunEventKey.COLOR]: color });
  const events = [perceived('step_1', 'yellow_cube', 'yellow'), perceived('step_2', 'blue_cube', 'blue'), perceived('step_3', 'yellow_cube', 'yellow')];
  const progress = RunProgress.of(
    ['step_1', 'step_2', 'step_3'].map((identifier) => ({ [PlanStepKey.IDENTIFIER]: identifier })),
    () => 'Pick',
    { [RunStatusKey.EVENTS]: events },
  );
  const view = rendered(progress);
  assert.deepEqual(byClass(view, RunViewClass.OBJECT).map((chip) => chip.innerText), ['yellow cube (yellow)', 'blue cube (blue)']);
  assert.equal(progress.log.filter((line) => line.includes('perceived yellow cube')).length, 2);
});
