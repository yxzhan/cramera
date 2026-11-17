import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  PerceptionReportKey,
  PlanStepKey,
  ReportedObjectKey,
  ReportedObjectVerdict,
  RunEventKey,
  RunEventKind,
  RunStatusKey,
  StepState,
  SurfaceDepthKey,
} from 'cramera-viewer/protocol.js';
import { Elements } from '../../src/cramera_plan_builder/web/builder/elements.js';
import { PerceptionViewClass, askedText, objectText, perceptionSentence } from '../../src/cramera_plan_builder/web/builder/perception-view.js';
import { RunProgress, RunView, RunViewClass } from '../../src/cramera_plan_builder/web/builder/run-view.js';
import { FakeDocument } from 'cramera-testing/fake-document.mjs';

/**
 * The run of the red cube's pick on the real table on 2026-11-12: the cube was seen
 * 19 cm from where the plan was validated, beside the table top reported as two grey
 * objects, then three table-wide searches found nothing and the step gave up.
 */
const seen = (position, colors, verdict, distance = null) => ({
  [ReportedObjectKey.POSITION]: position,
  [ReportedObjectKey.SIZE]: [0.066, 0.061, 0.022],
  [ReportedObjectKey.COLORS]: colors,
  [ReportedObjectKey.TYPE]: '',
  [ReportedObjectKey.VERDICT]: verdict,
  [ReportedObjectKey.DISTANCE]: distance,
});

const grounded = {
  [PerceptionReportKey.TARGET]: 'red_cube',
  [PerceptionReportKey.TYPE]: 'plannedgraspable',
  [PerceptionReportKey.COLOR]: 'red',
  [PerceptionReportKey.BOX]: [[0.525, 0.03, 0.83], [0.675, 0.18, 0.98]],
  [PerceptionReportKey.TABLE_WIDE]: false,
  [PerceptionReportKey.GROUNDED]: [0.6, 0.105, 0.905],
  [PerceptionReportKey.TOLERANCE]: 0.02,
  [PerceptionReportKey.ANSWERED]: true,
  [PerceptionReportKey.OBJECTS]: [
    seen([0.79, 0.145, 0.905], ['red'], ReportedObjectVerdict.OUTSIDE_BOX, 0.194),
    seen([0.82, 0.3, 0.9], ['grey'], ReportedObjectVerdict.WRONG_COLOR, 0.28),
  ],
  [PerceptionReportKey.SUMMARY]: 'red_cube was seen 19 cm from where the plan was validated, outside the 15 cm box looked in.',
};

const tableWide = {
  [PerceptionReportKey.TARGET]: null,
  [PerceptionReportKey.TYPE]: 'plannedgraspable',
  [PerceptionReportKey.COLOR]: 'red',
  [PerceptionReportKey.BOX]: [[0.0, -0.8, 0.8], [1.18, 0.8, 1.1]],
  [PerceptionReportKey.TABLE_WIDE]: true,
  [PerceptionReportKey.ANSWERED]: true,
  [PerceptionReportKey.OBJECTS]: [],
  [PerceptionReportKey.SUMMARY]: 'A table-wide search found no red object.',
};

const perception = (report) => ({ [RunEventKey.KIND]: RunEventKind.PERCEPTION, [RunEventKey.STEP]: 'step_2', [RunEventKey.PERCEPTION]: report });

const failedRun = () => RunProgress.of(
  [{ [PlanStepKey.IDENTIFIER]: 'step_1' }, { [PlanStepKey.IDENTIFIER]: 'step_2' }],
  (step) => `label of ${step[PlanStepKey.IDENTIFIER]}`,
  {
    [RunStatusKey.EVENTS]: [
      perception(grounded),
      perception(tableWide),
      perception(tableWide),
      perception(tableWide),
      { [RunEventKey.KIND]: RunEventKind.STEP, [RunEventKey.STEP]: 'step_2', [RunEventKey.STATE]: StepState.FAILED, [RunEventKey.ATTEMPT]: 3, [RunEventKey.ATTEMPTS]: 3, [RunEventKey.REASON]: 'red_cube was not detected on the table again' },
    ],
  },
);

const rendered = (progress) => {
  const view = new RunView(new Elements(new FakeDocument()));
  view.render(progress);
  return view.element;
};

const byClass = (root, name) => root.findAll((element) => element.className === name);

test('a step whose detections failed says why in one sentence beside it', () => {
  const view = rendered(failedRun());
  const failed = byClass(view, RunViewClass.STEP)[1];
  assert.equal(
    byClass(failed, PerceptionViewClass.SENTENCE)[0].textContent,
    'Perception: red_cube was seen 19 cm from where the plan was validated, outside the 15 cm box looked in; then a table-wide search found no red object (3 times).',
  );
});

test('every report shows what was asked and every object with why it was not taken', () => {
  const view = rendered(failedRun());
  const reports = byClass(view, PerceptionViewClass.REPORT);
  assert.equal(reports.length, 4);
  assert.equal(byClass(reports[0], PerceptionViewClass.ASKED)[0].textContent, 'Looked for the red cube in the 15 × 15 cm box around (0.60, 0.10, 0.91) m, where the plan was validated; the step allows 2 cm.');
  const objects = byClass(reports[0], PerceptionViewClass.OBJECT);
  assert.deepEqual(objects.map((item) => item.dataset.verdict), [ReportedObjectVerdict.OUTSIDE_BOX, ReportedObjectVerdict.WRONG_COLOR]);
  assert.match(objects[0].innerText, /red, 6\.6 × 6\.1 × 2\.2 cm, at \(0\.79, 0\.14, 0\.91\) m, 19\.4 cm from where the plan was validated: outside the box looked in/);
});

test('an empty answer is shown as empty, with the query', () => {
  const view = rendered(failedRun());
  const last = byClass(view, PerceptionViewClass.REPORT).at(-1);
  assert.equal(last.open, true);
  assert.match(byClass(last, PerceptionViewClass.ASKED)[0].textContent, /on the whole table/);
  assert.match(byClass(last, PerceptionViewClass.EMPTY)[0].textContent, /The answer was empty/);
});

test('the log says what each detection found', () => {
  assert.ok(failedRun().log.includes('2. label of step_2: A table-wide search found no red object.'));
});

test('no report makes no sentence', () => {
  assert.equal(perceptionSentence([]), null);
});

/**
 * The red cube taken where it was validated, reported `depth` metres inside the table.
 */
const sunk = (depth, verdict = ReportedObjectVerdict.ACCEPTED) => ({
  ...seen([0.6, 0.105, 0.905 - depth], ['red'], verdict, 0.004),
  [ReportedObjectKey.INSIDE_SURFACE]: {
    [SurfaceDepthKey.SURFACE]: 'table',
    [SurfaceDepthKey.DEPTH]: depth,
  },
});

test('a cube taken a few millimetres inside the table says it was raised onto it', () => {
  assert.match(objectText(sunk(0.004)), /: taken for the step; reported 4 mm inside the table and raised onto it$/);
});

test('a cube taken centimetres inside the table says it was raised onto it', () => {
  assert.match(objectText(sunk(0.04)), /: taken for the step; reported 40 mm inside the table and raised onto it$/);
});

test('an object not taken says nothing of the table', () => {
  assert.doesNotMatch(objectText(sunk(0.004, ReportedObjectVerdict.WRONG_COLOR)), /inside the table/);
});

/**
 * The pick of the red cube planned again where a table-wide search found it, 19 cm from
 * where the plan was validated, its next detection finding it there.
 */
const plannedAgain = {
  ...grounded,
  [PerceptionReportKey.LOOKED_FOR]: 'the red cube',
  [PerceptionReportKey.TYPE]: 'plannedgraspablefromabove',
  [PerceptionReportKey.BOX]: [[0.715, 0.07, 0.83], [0.865, 0.22, 0.98]],
  [PerceptionReportKey.GROUNDED]: [0.79, 0.145, 0.905],
  [PerceptionReportKey.VALIDATED]: [0.6, 0.105, 0.905],
  [PerceptionReportKey.OBJECTS]: [seen([0.79, 0.145, 0.905], ['red'], ReportedObjectVerdict.ACCEPTED, 0.0)],
};

test('what was asked names the object as the person knows it, never the type checked', () => {
  const asked = askedText(plannedAgain);
  assert.match(asked, /^Looked for the red cube in /);
  assert.doesNotMatch(asked, /planned/i);
});

test('a step planned again says it looked around where the object was last seen', () => {
  assert.equal(
    askedText(plannedAgain),
    'Looked for the red cube in the 15 × 15 cm box around (0.79, 0.14, 0.91) m, where it was last seen (19.4 cm from where the plan was validated); the step allows 2 cm.',
  );
});

test('an object seen by a step planned again is measured from where it was last seen', () => {
  const [cube] = plannedAgain[PerceptionReportKey.OBJECTS];
  assert.match(objectText(cube, plannedAgain), /, 0 cm from where it was last seen: taken for the step$/);
});

test('an object of another kind says so without its type name', () => {
  const bowl = { ...seen([0.6, 0.1, 0.905], ['red'], ReportedObjectVerdict.WRONG_TYPE, 0.005), [ReportedObjectKey.TYPE]: 'PlannedGraspableFromAbove' };
  const text = objectText(bowl, grounded);
  assert.doesNotMatch(text, /planned/i);
  assert.match(text, /: not the kind of object looked for$/);
});
