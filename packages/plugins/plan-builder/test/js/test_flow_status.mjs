import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PlanStepKey, RunEventKind, RunMode, RunState, RunStatusKey, SafetyStopKey, StepState } from 'cramera-viewer/protocol.js';
import { EMERGENCY_STOP, FlowFacts, FlowRequest, Tone, UNANSWERED_ADVICE_AFTER, cutOffOutcome, flowStatus, runOutcome } from '../../src/cramera_plan_builder/web/builder/flow-status.js';
import { RunProgress, unseenSentence } from '../../src/cramera_plan_builder/web/builder/run-view.js';
import { SafetyBannerState } from '../../src/cramera_plan_builder/web/builder/safety-stop.js';

const STEPS = ['park', 'pick', 'place'].map((identifier) => ({ [PlanStepKey.IDENTIFIER]: identifier }));

/**
 * @param {Object} run A `RunStatus`.
 * @returns {RunProgress} Its progress over three steps.
 */
const progressOf = (run) => RunProgress.of(STEPS, (step) => step[PlanStepKey.IDENTIFIER], run);

/**
 * @param {Object} changes The facts that differ from an idle page.
 * @returns {FlowFacts} The facts.
 */
const facts = (changes = {}) => {
  const run = changes.run ?? { [RunStatusKey.STATE]: RunState.IDLE };
  return new FlowFacts({ run, progress: progressOf(run), ...changes });
};

const step = (identifier, state, extra = {}) => ({ kind: 'step', step: identifier, state, ...extra });
const realRun = (state, events = [], extra = {}) => ({ [RunStatusKey.STATE]: state, [RunStatusKey.MODE]: RunMode.ROBOT, [RunStatusKey.EVENTS]: events, ...extra });

test('an idle page is ready, or says why it cannot run', () => {
  assert.deepEqual([flowStatus(facts()).tone, flowStatus(facts()).text, flowStatus(facts()).phase], [Tone.IDLE, 'Ready.', null]);
  const missing = flowStatus(facts({ missing: ['coraplex'] }));
  assert.equal(missing.tone, Tone.WARNING);
  assert.match(missing.text, /not importable here \(missing coraplex\)/);
});

test('each request waiting for its answer is progress, with what it does', () => {
  const scanning = flowStatus(facts({ request: new FlowRequest(FlowRequest.SCAN) }));
  assert.deepEqual([scanning.tone, scanning.text, scanning.phase], [Tone.PROGRESS, 'Scanning the scene…', FlowRequest.SCAN]);
  assert.equal(flowStatus(facts({ request: new FlowRequest(FlowRequest.VALIDATE) })).text, 'Validating the plan (nothing moves)…');
  assert.equal(flowStatus(facts({ request: new FlowRequest(FlowRequest.START, RunMode.ROBOT), real: true })).text, 'Starting the run on the real robot…');
  assert.equal(flowStatus(facts({ request: new FlowRequest(FlowRequest.START, RunMode.SIMULATE) })).text, 'Starting the run…');
  assert.equal(flowStatus(facts({ stopping: true })).text, 'Stopping the run…');
  assert.equal(flowStatus(facts({ stopping: true })).phase, 'stop');
});

test('a real run names the running step and reminds of the emergency stop; a simulated one says it is simulated', () => {
  const preparing = flowStatus(facts({ run: realRun(RunState.RUNNING), real: true }));
  assert.equal(preparing.text, `Real robot: preparing the run… ${EMERGENCY_STOP}`);
  assert.equal(preparing.tone, Tone.PROGRESS);
  assert.equal(preparing.phase, 'run');
  const running = flowStatus(facts({ run: realRun(RunState.RUNNING, [step('park', StepState.DONE), step('pick', StepState.RUNNING)]), real: true }));
  assert.equal(running.text, `Real robot: running 2. pick (1 of 3 steps done). ${EMERGENCY_STOP}`);
  const retrying = flowStatus(facts({ run: realRun(RunState.RUNNING, [step('park', StepState.RETRYING, { reason: 'no cube seen' })]), real: true }));
  assert.equal(retrying.tone, Tone.WARNING);
  assert.equal(retrying.text, `Real robot: retrying 1. park (0 of 3 steps done): no cube seen. ${EMERGENCY_STOP}`);
  const simulated = flowStatus(facts({ run: { [RunStatusKey.STATE]: RunState.RUNNING, [RunStatusKey.MODE]: RunMode.SIMULATE } }));
  assert.equal(simulated.text, 'Simulation (the real robot does not move): preparing the run…');
});

test('a real run whose steps are all done says it finishes, not that it prepares', () => {
  const finishing = flowStatus(facts({ run: realRun(RunState.RUNNING, ['park', 'pick', 'place'].map((identifier) => step(identifier, StepState.DONE))), real: true }));
  assert.equal(finishing.text, `Real robot: 3 of 3 steps done; finishing the run… ${EMERGENCY_STOP}`);
  assert.equal(finishing.phase, 'run');
});

test('a run saving its record says it finished, saving is progress, a live scene is shown once its world is up', () => {
  const saving = flowStatus(facts({ run: realRun(RunState.SAVING), real: true }));
  assert.deepEqual([saving.tone, saving.text, saving.phase], [Tone.PROGRESS, 'The run on the real robot finished; the robot holds. Saving the run\'s record…', 'saving']);
  const stage = { [RunStatusKey.STATE]: RunState.RUNNING, [RunStatusKey.MODE]: RunMode.STAGE };
  assert.equal(flowStatus(facts({ run: stage })).text, 'Starting the live scene…');
  assert.equal(flowStatus(facts({ run: stage, live: true })).text, 'The live scene runs.');
  assert.equal(flowStatus(facts({ run: stage, live: true })).phase, null);
});

test('the outcome of the latest step stands until something runs, an error included', () => {
  const failed = { tone: Tone.ERROR, text: 'The scene could not be scanned: RoboKudo did not answer.' };
  assert.deepEqual([flowStatus(facts({ outcome: failed })).tone, flowStatus(facts({ outcome: failed })).text], [Tone.ERROR, failed.text]);
  assert.equal(flowStatus(facts({ outcome: failed, request: new FlowRequest(FlowRequest.SCAN) })).text, 'Scanning the scene…');
  const unstopped = { tone: Tone.ERROR, text: 'The run could not be stopped: Failed to fetch. Use the emergency stop if the robot still moves.' };
  assert.equal(flowStatus(facts({ outcome: unstopped, run: realRun(RunState.RUNNING), real: true })).text, unstopped.text);
  const scanned = { tone: Tone.SUCCESS, text: 'Scanned: 2 objects, seen by the camera.' };
  assert.match(flowStatus(facts({ outcome: scanned, run: realRun(RunState.RUNNING), real: true })).text, /^Real robot/);
});

test('a safety stop comes before everything else, then its acknowledgement says what is next', () => {
  const violated = flowStatus(facts({ safety: SafetyBannerState.VIOLATED, stopping: true }));
  assert.equal(violated.tone, Tone.ERROR);
  assert.equal(violated.text, 'Safety stop: the robot holds. Acknowledge it in the red banner to go on.');
  const acknowledged = flowStatus(facts({ safety: SafetyBannerState.ACKNOWLEDGED, revalidate: true }));
  assert.equal(acknowledged.tone, Tone.WARNING);
  assert.equal(acknowledged.text, 'Safety stop acknowledged: validate the plan again, then restart or abort it.');
  assert.equal(flowStatus(facts({ safety: SafetyBannerState.ACKNOWLEDGED })).text, 'Safety stop acknowledged: restart the plan or abort it.');
});

test('a plan builder silent for ten seconds says the platform may have stopped and how to start it', () => {
  const lately = flowStatus(facts({ unanswered: 'Failed to fetch', unansweredFor: UNANSWERED_ADVICE_AFTER - 1 }));
  assert.equal(lately.text, 'The plan builder does not answer (Failed to fetch); retrying.');
  const long = flowStatus(facts({ unanswered: 'Failed to fetch', unansweredFor: UNANSWERED_ADVICE_AFTER, run: realRun(RunState.RUNNING), real: true }));
  assert.equal(long.text, `The plan builder does not answer (Failed to fetch); retrying. The platform may have stopped: run ./aicor start in its folder. ${EMERGENCY_STOP}`);
});

test('a plan builder that does not answer is a warning above the run it may have lost', () => {
  const lost = flowStatus(facts({ unanswered: 'Failed to fetch', run: realRun(RunState.RUNNING), real: true }));
  assert.equal(lost.tone, Tone.WARNING);
  assert.equal(lost.text, `The plan builder does not answer (Failed to fetch); retrying. ${EMERGENCY_STOP}`);
});

test('a run that ended says how: a failure gives its reason, from the failed step or the output', () => {
  const failedStep = realRun(RunState.FAILED, [step('park', StepState.DONE), step('pick', StepState.FAILED, { reason: 'grasp missed' })]);
  assert.deepEqual(runOutcome(failedStep, progressOf(failedStep), true), { tone: Tone.ERROR, text: 'The run on the real robot failed at 2. pick: grasp missed.' });
  const preflight = realRun(RunState.FAILED, [], { [RunStatusKey.LOG]: 'building the world\nPreflight: no /joint_states within 15 s\n\n' });
  assert.deepEqual(runOutcome(preflight, progressOf(preflight), true), { tone: Tone.ERROR, text: 'The run on the real robot failed: Preflight: no /joint_states within 15 s' });
  const silent = realRun(RunState.FAILED);
  assert.equal(runOutcome(silent, progressOf(silent), false).text, 'The run failed; see Run output.');
  const finished = realRun(RunState.FINISHED);
  assert.deepEqual(runOutcome(finished, progressOf(finished), true), { tone: Tone.SUCCESS, text: 'The run on the real robot finished.' });
  const simulated = { [RunStatusKey.STATE]: RunState.FINISHED, [RunStatusKey.MODE]: RunMode.PERFORM };
  assert.equal(runOutcome(simulated, progressOf(simulated), false).text, 'The simulated run finished.');
  assert.equal(runOutcome(realRun(RunState.STOPPED), progressOf(realRun(RunState.STOPPED)), true).tone, Tone.WARNING);
  const saved = realRun(RunState.FINISHED, [{ kind: RunEventKind.RECORD_SAVING }]);
  assert.deepEqual(runOutcome(saved, progressOf(saved), true), { tone: Tone.SUCCESS, text: 'The run on the real robot finished; its record is saved.' });
  const unsaved = realRun(RunState.SAVE_FAILED, [], { [RunStatusKey.LOG]: 'OSError: disk full' });
  assert.deepEqual(runOutcome(unsaved, progressOf(unsaved), true), { tone: Tone.ERROR, text: 'The run on the real robot finished, but saving its record failed: OSError: disk full' });
  const stopped = realRun(RunState.SAFETY_STOPPED, [], { [RunStatusKey.SAFETY_STOP]: { [SafetyStopKey.CONSTRAINT]: 'left arm workspace', [SafetyStopKey.ACKNOWLEDGED]: false } });
  assert.deepEqual(runOutcome(stopped, progressOf(stopped), true), { tone: Tone.ERROR, text: 'A safety stop ended the run: left arm workspace.' });
  assert.equal(runOutcome(realRun(RunState.IDLE), progressOf(realRun(RunState.IDLE)), true), null);
});

test('a finished run whose step went ahead without the camera confirming its object is a notice that names the step', () => {
  const finished = realRun(RunState.FINISHED, [
    step('park', StepState.DONE),
    { kind: RunEventKind.CONTINUED_UNSEEN, step: 'pick', target: 'red_cube', reason: 'red_cube was not seen' },
    step('pick', StepState.DONE),
    step('place', StepState.DONE),
  ]);
  assert.deepEqual(runOutcome(finished, progressOf(finished), true), {
    tone: Tone.WARNING,
    text: 'The run on the real robot finished. 1 step went ahead without the camera confirming its object: 2. pick (red cube).',
  });
});

test('a run that ended otherwise than finished names its steps that went ahead unseen after its own headline', () => {
  const unseen = { kind: RunEventKind.CONTINUED_UNSEEN, step: 'pick', target: 'red_cube', reason: 'red_cube was not seen' };
  const sentence = unseenSentence([progressOf(realRun(RunState.FINISHED, [unseen])).step('pick')]);
  const failed = realRun(RunState.FAILED, [step('park', StepState.DONE), unseen, step('pick', StepState.DONE), step('place', StepState.FAILED, { reason: 'the gripper is empty' })]);
  const stopped = realRun(RunState.STOPPED, [step('park', StepState.DONE), unseen, step('pick', StepState.RUNNING)]);
  const safety = realRun(RunState.SAFETY_STOPPED, [unseen], { [RunStatusKey.SAFETY_STOP]: { [SafetyStopKey.CONSTRAINT]: 'left arm workspace', [SafetyStopKey.ACKNOWLEDGED]: false } });
  for (const run of [failed, stopped, safety]) {
    const seen = { ...run, [RunStatusKey.EVENTS]: run[RunStatusKey.EVENTS].filter((event) => event !== unseen) };
    const headline = runOutcome(seen, progressOf(seen), true);
    const outcome = runOutcome(run, progressOf(run), true);
    assert.deepEqual(outcome, { tone: headline.tone, text: `${headline.text} ${sentence}` });
  }
  assert.equal(sentence, '1 step went ahead without the camera confirming its object: 2. pick (red cube).');
});

test('a run the plan builder lost to its restart says it was cut off and to check the robot', () => {
  const idle = { [RunStatusKey.STATE]: RunState.IDLE };
  for (const before of [RunState.RUNNING, RunState.SAVING]) {
    assert.deepEqual(cutOffOutcome(realRun(before), idle, true), {
      tone: Tone.WARNING,
      text: 'The plan builder restarted while the run on the real robot was going: the run ended there and its record may not be saved. Check where the robot stands before going on.',
    });
  }
  assert.match(cutOffOutcome(realRun(RunState.RUNNING), idle, false).text, /^The plan builder restarted while the run was going/);
  assert.equal(cutOffOutcome(realRun(RunState.FINISHED), idle, true), null);
  assert.equal(cutOffOutcome(realRun(RunState.RUNNING), realRun(RunState.FINISHED), true), null);
  assert.equal(cutOffOutcome({ [RunStatusKey.STATE]: RunState.RUNNING, [RunStatusKey.MODE]: RunMode.STAGE }, idle, true), null);
});

test('a run its robot refused before anything moved says why in one plain line and that the validation holds', () => {
  const reason = 'The real robot is not ready: the arm watchdog is not running (no heartbeat on cramera/arm_watchdog/heartbeat). No motion was started. Run ./aicor start in the platform\'s folder to start it again, then run the plan again.';
  const refused = realRun(RunState.REFUSED, [{ kind: RunEventKind.REFUSED, reason }], { [RunStatusKey.LOG]: `[run] ${reason}` });
  assert.deepEqual(runOutcome(refused, progressOf(refused), true), { tone: Tone.WARNING, text: `${reason} The plan is still validated.` });
  const silent = realRun(RunState.REFUSED);
  assert.deepEqual(runOutcome(silent, progressOf(silent), true), { tone: Tone.WARNING, text: 'The real robot is not ready; no motion was started. See Run output. The plan is still validated.' });
});

test('a stop on the real robot is only said once its halt was confirmed, else it asks for the emergency stop in red', () => {
  const halted = realRun(RunState.STOPPED);
  assert.deepEqual(runOutcome(halted, progressOf(halted), true), { tone: Tone.WARNING, text: 'The run on the real robot was stopped: Giskard ended, both arms were commanded zero velocities and stand still.' });
  const unconfirmed = realRun(RunState.HALT_UNCONFIRMED);
  const outcome = runOutcome(unconfirmed, progressOf(unconfirmed), true);
  assert.equal(outcome.tone, Tone.ERROR);
  assert.match(outcome.text, /could not confirm that the arms stand still/);
  assert.match(outcome.text, /Use the emergency stop now/);
  const simulated = { [RunStatusKey.STATE]: RunState.STOPPED, [RunStatusKey.MODE]: RunMode.PERFORM };
  assert.equal(runOutcome(simulated, progressOf(simulated), false).text, 'The simulated run was stopped.');
});

test('a run names the step it is at as soon as its steps are reported, before one reports running', () => {
  const scheduled = STEPS.map((planned) => step(planned[PlanStepKey.IDENTIFIER], StepState.PENDING));
  const announced = flowStatus(facts({ run: realRun(RunState.RUNNING, scheduled), real: true }));
  assert.equal(announced.text, `Real robot: running 1. park (0 of 3 steps done). ${EMERGENCY_STOP}`);
  const parked = flowStatus(facts({ run: realRun(RunState.RUNNING, [...scheduled, step('park', StepState.DONE)]), real: true }));
  assert.equal(parked.text, `Real robot: running 2. pick (1 of 3 steps done). ${EMERGENCY_STOP}`);
});

test('a validation after an acknowledged safety stop shows its progress as any validation does', () => {
  const validating = flowStatus(facts({ safety: SafetyBannerState.ACKNOWLEDGED, revalidate: true, request: new FlowRequest(FlowRequest.VALIDATE) }));
  const plain = flowStatus(facts({ request: new FlowRequest(FlowRequest.VALIDATE) }));
  assert.deepEqual([validating.tone, validating.text, validating.phase], [plain.tone, plain.text, plain.phase]);
});
