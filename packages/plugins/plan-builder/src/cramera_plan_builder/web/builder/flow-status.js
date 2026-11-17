/**
 * The status area of the builder page: one line saying where the flow stands (idle,
 * scanning, validating, starting, running a step, stopping, saving the record, finished,
 * failed, safety-stopped), in one wording and one colour per kind: progress, success,
 * warning or error. A safety stop comes first; the outcome of the latest step stands
 * until the person starts something new or a run ends, and an error stands above a
 * running plan.
 */

import { RunEventKey, RunEventKind, RunMode, RunState, RunStatusKey, SafetyStopKey, StepState } from '../../../protocol.js';
import { SafetyBannerState } from './safety-stop.js';
import { unseenSentence } from './run-view.js';

/**
 * How the status area colours a status.
 */
export const Tone = Object.freeze({
  IDLE: 'idle',
  PROGRESS: 'progress',
  SUCCESS: 'success',
  WARNING: 'warning',
  ERROR: 'error',
});

/**
 * The reminder every status of a run on the real robot carries.
 */
export const EMERGENCY_STOP = 'Keep a hand on the emergency stop.';

/**
 * What the status of a simulated run starts with, by its run mode.
 */
const SIMULATED = 'Simulation (the real robot does not move)';

/**
 * A request the page waits for, during which no other validation, scan or run starts.
 */
export class FlowRequest {
  static SCAN = 'scan';
  static VALIDATE = 'validate';
  static START = 'start';

  /**
   * @param {string} kind What is asked: `FlowRequest.SCAN`, `VALIDATE` or `START`.
   * @param {string|null} mode The `RunMode` a start asks for, else null.
   */
  constructor(kind, mode = null) {
    /** @type {string} */
    this.kind = kind;
    /** @type {string|null} */
    this.mode = mode;
  }
}

/**
 * How long, in milliseconds, the plan builder may not answer before the status says that
 * the platform may have stopped and how to start it.
 */
export const UNANSWERED_ADVICE_AFTER = 10000;

/**
 * What the status adds once the plan builder did not answer for a while.
 */
const START_ADVICE = 'The platform may have stopped: run ./aicor start in its folder.';

/**
 * What decides the status: the page's state at one moment.
 */
export class FlowFacts {
  /**
   * @param {Object} parts `run` (the `RunStatus`), `progress` (its `RunProgress`), `real`
   *   (whether the run on the robot moves the real robot), `live` (whether a run's world
   *   is published), `request` (the `FlowRequest` waited for, or null), `stopping`,
   *   `unanswered` (why the plan builder did not answer the latest poll, or null),
   *   `unansweredFor` (how long, in milliseconds, it has not answered),
   *   `safety` (a `SafetyBannerState`), `revalidate` (whether an acknowledged stop's
   *   plan needs a new validation before it restarts), `outcome` (`{tone, text}` of the
   *   latest step, or null) and `missing` (the CRAM packages the workspace lacks).
   */
  constructor(parts) {
    this.run = parts.run;
    this.progress = parts.progress;
    this.real = parts.real ?? false;
    this.live = parts.live ?? false;
    this.request = parts.request ?? null;
    this.stopping = parts.stopping ?? false;
    this.unanswered = parts.unanswered ?? null;
    this.unansweredFor = parts.unansweredFor ?? 0;
    this.safety = parts.safety ?? SafetyBannerState.HIDDEN;
    this.revalidate = parts.revalidate ?? false;
    this.outcome = parts.outcome ?? null;
    this.missing = parts.missing ?? [];
  }

  /**
   * @returns {boolean} Whether the run started last moves the real robot.
   */
  realRun() {
    return this.real && this.run[RunStatusKey.MODE] === RunMode.ROBOT;
  }
}

/**
 * What the status area shows.
 */
export class FlowStatus {
  /**
   * @param {string} tone A `Tone` value.
   * @param {string} text The status.
   * @param {string|null} phase What the page waits for, whose elapsed time is shown, or
   *   null when it waits for nothing.
   */
  constructor(tone, text, phase = null) {
    /** @type {string} */
    this.tone = tone;
    /** @type {string} */
    this.text = text;
    /** @type {string|null} */
    this.phase = phase;
  }
}

/**
 * @param {Object} run A `RunStatus`.
 * @returns {string} The last line of its output that holds text, or ''.
 */
function lastLine(run) {
  return (run[RunStatusKey.LOG] ?? '').split('\n').map((line) => line.trim()).filter(Boolean).at(-1) ?? '';
}

/**
 * @param {FlowFacts} facts The page's state.
 * @returns {FlowStatus} The status of a running plan.
 */
function runningStatus(facts) {
  const real = facts.realRun();
  const prefix = real ? 'Real robot' : SIMULATED;
  const suffix = real ? ` ${EMERGENCY_STOP}` : '';
  const steps = facts.progress.steps();
  const done = steps.filter((step) => step.state === StepState.DONE).length;
  const current = facts.progress.underway();
  if (current?.state === StepState.RETRYING) return new FlowStatus(Tone.WARNING, `${prefix}: retrying ${current.name} (${done} of ${steps.length} steps done): ${current.reason}.${suffix}`, 'run');
  if (current) return new FlowStatus(Tone.PROGRESS, `${prefix}: running ${current.name} (${done} of ${steps.length} steps done).${suffix}`, 'run');
  if (steps.length > 0 && done === steps.length) return new FlowStatus(Tone.PROGRESS, `${prefix}: ${done} of ${steps.length} steps done; finishing the run…${suffix}`, 'run');
  return new FlowStatus(Tone.PROGRESS, `${prefix}: preparing the run…${suffix}`, 'run');
}

/**
 * @param {FlowRequest} request A request the page waits for.
 * @param {boolean} real Whether a run on the robot moves the real robot.
 * @returns {string} What the page says while it waits.
 */
function requestText(request, real) {
  if (request.kind === FlowRequest.SCAN) return 'Scanning the scene…';
  if (request.kind === FlowRequest.VALIDATE) return 'Validating the plan (nothing moves)…';
  if (request.mode === RunMode.STAGE) return 'Starting the live scene…';
  return request.mode === RunMode.ROBOT && real ? 'Starting the run on the real robot…' : 'Starting the run…';
}

/**
 * @param {FlowFacts} facts The page's state.
 * @returns {FlowStatus} Where the flow stands.
 */
export function flowStatus(facts) {
  if (facts.safety === SafetyBannerState.VIOLATED) return new FlowStatus(Tone.ERROR, 'Safety stop: the robot holds. Acknowledge it in the red banner to go on.');
  if (facts.safety === SafetyBannerState.ACKNOWLEDGED && facts.request === null) {
    return new FlowStatus(Tone.WARNING, facts.revalidate ? 'Safety stop acknowledged: validate the plan again, then restart or abort it.' : 'Safety stop acknowledged: restart the plan or abort it.');
  }
  if (facts.stopping) return new FlowStatus(Tone.PROGRESS, 'Stopping the run…', 'stop');
  if (facts.request) return new FlowStatus(Tone.PROGRESS, requestText(facts.request, facts.real), facts.request.kind);
  // an error, such as a stop that failed, stays above the run it concerns
  if (facts.outcome?.tone === Tone.ERROR) return new FlowStatus(Tone.ERROR, facts.outcome.text);
  const state = facts.run[RunStatusKey.STATE];
  const staged = facts.run[RunStatusKey.MODE] === RunMode.STAGE;
  if (facts.unanswered !== null) {
    const reminder = facts.realRun() && [RunState.RUNNING, RunState.SAVING].includes(state) ? ` ${EMERGENCY_STOP}` : '';
    const advice = facts.unansweredFor >= UNANSWERED_ADVICE_AFTER ? ` ${START_ADVICE}` : '';
    return new FlowStatus(Tone.WARNING, `The plan builder does not answer (${facts.unanswered}); retrying.${advice}${reminder}`);
  }
  if (state === RunState.RUNNING && !staged) return runningStatus(facts);
  if (state === RunState.SAVING) return new FlowStatus(Tone.PROGRESS, `${runName(facts.run, facts.real)} finished; the robot holds. Saving the run's record…`, 'saving');
  if (state === RunState.RUNNING && !facts.live) return new FlowStatus(Tone.PROGRESS, 'Starting the live scene…', 'stage');
  if (facts.outcome) return new FlowStatus(facts.outcome.tone, facts.outcome.text);
  if (state === RunState.RUNNING) return new FlowStatus(Tone.IDLE, 'The live scene runs.');
  if (facts.missing.length > 0) return new FlowStatus(Tone.WARNING, `The CRAM workspace is not importable here (missing ${facts.missing.join(', ')}); plans can be composed and saved, not run.`);
  return new FlowStatus(Tone.IDLE, 'Ready.');
}

/**
 * @param {Object} run The `RunStatus` of a run that just ended.
 * @param {import('./run-view.js').RunProgress} progress Its progress.
 * @param {boolean} real Whether a run on the robot moves the real robot.
 * @returns {{tone: string, text: string}|null} How it ended, which stands as the
 *   outcome until the next step, followed by the steps that went ahead without the
 *   camera confirming their objects; null for a state that is no end.
 */
export function runOutcome(run, progress, real) {
  const outcome = runEnd(run, progress, real);
  const unseen = unseenSentence(progress.unseenSteps());
  if (outcome === null || !unseen) return outcome;
  // a step that went ahead without the camera confirming its object is a notice, not a success
  const tone = outcome.tone === Tone.SUCCESS ? Tone.WARNING : outcome.tone;
  return { tone, text: `${outcome.text} ${unseen}` };
}

/**
 * @param {Object} run The `RunStatus` of a run that just ended.
 * @param {import('./run-view.js').RunProgress} progress Its progress.
 * @param {boolean} real Whether a run on the robot moves the real robot.
 * @returns {{tone: string, text: string}|null} How it ended; null for a state that is
 *   no end.
 */
function runEnd(run, progress, real) {
  const state = run[RunStatusKey.STATE];
  const mode = run[RunStatusKey.MODE];
  const name = runName(run, real);
  if (state === RunState.FINISHED) return { tone: Tone.SUCCESS, text: savedRecord(run) ? `${name} finished; its record is saved.` : `${name} finished.` };
  if (state === RunState.STOPPED && mode === RunMode.ROBOT && real) return { tone: Tone.WARNING, text: `${name} was stopped: Giskard ended, both arms were commanded zero velocities and stand still.` };
  if (state === RunState.STOPPED) return { tone: Tone.WARNING, text: `${name} was stopped.` };
  if (state === RunState.HALT_UNCONFIRMED) return { tone: Tone.ERROR, text: `${name} was stopped, but the plan builder could not confirm that the arms stand still and Giskard ended; see Run output. Use the emergency stop now.` };
  if (state === RunState.SAFETY_STOPPED) return { tone: Tone.ERROR, text: `A safety stop ended the run: ${run[RunStatusKey.SAFETY_STOP]?.[SafetyStopKey.CONSTRAINT] ?? 'see Run output'}.` };
  if (state === RunState.REFUSED) {
    const refusal = (run[RunStatusKey.EVENTS] ?? []).find((event) => event[RunEventKey.KIND] === RunEventKind.REFUSED)?.[RunEventKey.REASON];
    return { tone: Tone.WARNING, text: `${refusal ?? 'The real robot is not ready; no motion was started. See Run output.'} The plan is still validated.` };
  }
  if (state === RunState.SAVE_FAILED) return { tone: Tone.ERROR, text: `${name} finished, but saving its record failed: ${lastLine(run) || 'see Run output.'}` };
  if (state !== RunState.FAILED) return null;
  const failed = progress.steps().find((step) => step.state === StepState.FAILED);
  if (failed) return { tone: Tone.ERROR, text: `${name} failed at ${failed.name}: ${failed.reason ?? 'see Run output'}.` };
  const line = lastLine(run);
  return { tone: Tone.ERROR, text: line ? `${name} failed: ${line}` : `${name} failed; see Run output.` };
}

/**
 * @param {Object} run A `RunStatus`.
 * @param {boolean} real Whether a run on the robot moves the real robot.
 * @returns {string} What the status area calls the run, at the start of a sentence.
 */
function runName(run, real) {
  const mode = run[RunStatusKey.MODE];
  if (mode === RunMode.STAGE) return 'The live scene';
  if (mode === RunMode.ROBOT) return real ? 'The run on the real robot' : 'The run';
  return 'The simulated run';
}

/**
 * @param {Object} run The `RunStatus` of a run that finished.
 * @returns {boolean} Whether it saved its record once its motions were over, which it
 *   reported, so its record is saved now.
 */
function savedRecord(run) {
  return (run[RunStatusKey.EVENTS] ?? []).some((event) => event[RunEventKey.KIND] === RunEventKind.RECORD_SAVING);
}

/**
 * @param {Object} before The `RunStatus` the plan builder reported before.
 * @param {Object} run The `RunStatus` it reports now.
 * @param {boolean} real Whether a run on the robot moves the real robot.
 * @returns {{tone: string, text: string}|null} That a run which was going is gone, as the
 *   plan builder knows no run after it restarted; null when no run was cut off so.
 */
export function cutOffOutcome(before, run, real) {
  const going = [RunState.RUNNING, RunState.SAVING].includes(before[RunStatusKey.STATE]) && before[RunStatusKey.MODE] !== RunMode.STAGE;
  if (!going || run[RunStatusKey.STATE] !== RunState.IDLE) return null;
  const name = before[RunStatusKey.MODE] === RunMode.ROBOT && real ? 'the run on the real robot' : 'the run';
  return { tone: Tone.WARNING, text: `The plan builder restarted while ${name} was going: the run ended there and its record may not be saved. Check where the robot stands before going on.` };
}
