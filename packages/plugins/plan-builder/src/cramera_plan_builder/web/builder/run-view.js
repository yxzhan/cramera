/**
 * The run view: what the robot does, what it believes and why it stopped, followed from
 * the events a run reports. Each step shows its state, its attempt and the arm it uses,
 * with the badges of its monitors; the run shows the objects it perceived with their
 * colors, what perception reported for each detection, its safety monitor, the latest
 * camera image a perception step took and a log of every attempt and failure reason. The 3D view beside it shows the live robot.
 */

import {
  MonitorObservation,
  PerceptionImageKey,
  PerceptionReportKey,
  PlanStepKey,
  RunEventKey,
  RunEventKind,
  RunMonitor,
  RunState,
  RunStatusKey,
  StepState,
} from '../../../protocol.js';
import { PerceptionReportsView, PerceptionViewClass, perceptionSentence } from './perception-view.js';

/**
 * The state the page shows a step in that was running or retrying when a person stopped
 * the run; the run reports no such state, its process ended.
 */
export const STOPPED_STEP = 'stopped';

/**
 * The run states of a run a person stopped.
 */
const STOPPED_RUNS = Object.freeze([RunState.STOPPED, RunState.HALT_UNCONFIRMED]);

/**
 * How a person reads each step state.
 */
export const STEP_STATE_TEXTS = Object.freeze({
  [StepState.PENDING]: 'pending',
  [StepState.RUNNING]: 'running',
  [StepState.RETRYING]: 'retrying',
  [StepState.DONE]: 'done',
  [StepState.FAILED]: 'failed',
  [STOPPED_STEP]: 'stopped',
});

/**
 * How a person reads what each monitor observes.
 */
export const MONITOR_TEXTS = Object.freeze({
  [RunMonitor.LOOK_AT]: Object.freeze({
    [MonitorObservation.HOLDS]: 'object in the camera\'s view ✓',
    [MonitorObservation.VIOLATED]: 'object out of the camera\'s view ✗',
  }),
  [RunMonitor.SAFETY]: Object.freeze({
    [MonitorObservation.HOLDS]: 'within the safety limits ✓',
    [MonitorObservation.VIOLATED]: 'a safety limit was broken ✗',
  }),
});

/**
 * The classes the run view's elements carry.
 */
export const RunViewClass = Object.freeze({
  VIEW: 'run-view',
  SUMMARY: 'run-summary',
  BADGES: 'run-badges',
  BADGE: 'monitor-badge',
  STEPS: 'run-steps',
  STEP: 'run-step',
  NUMBER: 'step-number',
  NAME: 'step-name',
  STATE: 'run-step-state',
  ARM: 'run-step-arm',
  ATTEMPT: 'run-step-attempt',
  REASON: 'run-step-reason',
  UNSEEN: 'run-step-unseen',
  PERCEIVED: 'run-perceived',
  OBJECT: 'perceived-object',
  SWATCH: 'color-swatch',
  CAMERA: 'run-camera',
  PERCEPTION: 'run-perception',
  LOG: 'run-log-lines',
  EMPTY: 'empty',
});

// %% progress

/**
 * Where one step of the run stands.
 */
export class StepProgress {
  /**
   * @param {string} identifier The step's identity.
   * @param {string} name The step as a person reads it: its number and label.
   */
  constructor(identifier, name) {
    /** @type {string} */
    this.identifier = identifier;
    /** @type {string} */
    this.name = name;
    /** @type {string} A `StepState` value. */
    this.state = StepState.PENDING;
    /** @type {number|null} */
    this.attempt = null;
    /** @type {number|null} */
    this.attempts = null;
    /** @type {string|null} */
    this.arm = null;
    /** @type {string|null} Why the latest failed attempt failed. */
    this.reason = null;
    /** @type {string|null} How the next attempt differs. */
    this.nextAttempt = null;
    /**
     * What each of its monitors observes last, by monitor and target.
     * @type {Map<string, {monitor: string, target: string|null, observation: string}>}
     */
    this.monitors = new Map();
    /**
     * The `PerceptionReport`s of its detections, in order.
     * @type {Array<Object>}
     */
    this.perceptions = [];
    /**
     * The objects it went ahead with where the plan expected them, without the camera
     * confirming them, as a person reads them, in order.
     * @type {Array<string>}
     */
    this.unseen = [];
  }

  /**
   * @returns {Array<string>} The texts of its monitors' badges.
   */
  badges() {
    return [...this.monitors.values()].map((shown) => monitorText(shown.monitor, shown.observation));
  }
}

/**
 * @param {string} monitor A `RunMonitor` value.
 * @param {string} observation A `MonitorObservation` value.
 * @returns {string} What a badge of the monitor says.
 */
export function monitorText(monitor, observation) {
  return MONITOR_TEXTS[monitor]?.[observation] ?? `${monitor}: ${observation}`;
}

/**
 * @param {string} target The identities of objects a run event names, comma-separated.
 * @returns {string} Them as a person reads them: `red_cube` as `red cube`.
 */
function objectNames(target) {
  return (target ?? '').replaceAll('_', ' ');
}

/**
 * @param {Array<StepProgress>} steps The steps that went ahead without the camera
 *   confirming their objects.
 * @returns {string} One sentence counting and naming them, or '' for none.
 */
export function unseenSentence(steps) {
  if (steps.length === 0) return '';
  const named = steps.map((step) => `${step.name} (${step.unseen.join(', ')})`).join(', ');
  if (steps.length === 1) return `1 step went ahead without the camera confirming its object: ${named}.`;
  return `${steps.length} steps went ahead without the camera confirming their objects: ${named}.`;
}

/**
 * Where a run stands, folded from the events it reported in order.
 */
export class RunProgress {
  /**
   * The run's steps by identity, in the plan's order.
   * @type {Map<string, StepProgress>}
   */
  #steps = new Map();

  /**
   * Whether the run reported any of its steps.
   * @type {boolean}
   */
  #stepReported = false;

  /**
   * @param {Array<{identifier: string, label: string}>} steps The performing robot's
   *   steps, in order.
   * @param {Array<Object>} events The `RunEvent`s the run reported, in order.
   * @param {boolean} stopped Whether a person stopped the run, so a step that was
   *   running or retrying stopped with it.
   */
  constructor(steps, events, stopped = false) {
    steps.forEach((step, index) => this.#steps.set(step.identifier, new StepProgress(step.identifier, `${index + 1}. ${step.label}`)));
    /**
     * The objects steps perceived, each once, in the order they were first perceived,
     * with the step that perceived it last and the color it showed then; the run log
     * keeps every detection.
     * @type {Array<{step: string, target: string, color: string|null}>}
     */
    this.perceived = [];
    /**
     * Every perception report of the run with the name of its step, in order.
     * @type {Array<{name: string, report: Object}>}
     */
    this.perceptions = [];
    /**
     * What the safety monitor observes, or null for a run not held to safety limits.
     * @type {string|null}
     */
    this.safety = null;
    /**
     * The broken safety limit, or null.
     * @type {string|null}
     */
    this.safetyStop = null;
    /**
     * The run log: every event as a person reads it, in order.
     * @type {Array<string>}
     */
    this.log = [];
    for (const event of events) this.#apply(event);
    if (stopped) {
      for (const step of this.#steps.values()) if ([StepState.RUNNING, StepState.RETRYING].includes(step.state)) step.state = STOPPED_STEP;
    }
  }

  /**
   * @param {Array<Object>} steps The performing robot's plan steps.
   * @param {function(Object): string} label Names a step for people.
   * @param {Object} run The `RunStatus` of the demo started last.
   * @returns {RunProgress} The run's progress.
   */
  static of(steps, label, run) {
    return new RunProgress(
      steps.map((step) => ({ identifier: step[PlanStepKey.IDENTIFIER], label: label(step) })),
      run[RunStatusKey.EVENTS] ?? [],
      STOPPED_RUNS.includes(run[RunStatusKey.STATE]),
    );
  }

  /**
   * @returns {Array<StepProgress>} Every step, in the plan's order.
   */
  steps() {
    return [...this.#steps.values()];
  }

  /**
   * @param {string} identifier A step's identity.
   * @returns {StepProgress} Where it stands; a step the plan no longer holds is added.
   */
  step(identifier) {
    if (!this.#steps.has(identifier)) this.#steps.set(identifier, new StepProgress(identifier, identifier));
    return this.#steps.get(identifier);
  }

  /**
   * @returns {StepProgress|null} The step that runs or failed last, which the summary
   *   names.
   */
  current() {
    const steps = this.steps();
    return steps.find((step) => step.state === StepState.FAILED) ?? steps.find((step) => [StepState.RUNNING, StepState.RETRYING, STOPPED_STEP].includes(step.state)) ?? null;
  }

  /**
   * @returns {StepProgress|null} The step the run is at: the one that runs, retries or
   *   failed, else, once the run reported its steps, the first not done; null before.
   */
  underway() {
    if (this.current() !== null || !this.#stepReported) return this.current();
    return this.steps().find((step) => step.state !== StepState.DONE) ?? null;
  }

  /**
   * @returns {Array<StepProgress>} The steps that went ahead where the plan expected
   *   their objects without the camera confirming them, in the plan's order.
   */
  unseenSteps() {
    return this.steps().filter((step) => step.unseen.length > 0);
  }

  /**
   * @returns {string} Where the run stands, and which steps went ahead without the
   *   camera confirming their objects.
   */
  summary() {
    const unseen = unseenSentence(this.unseenSteps());
    return unseen ? `${this.#where()} ${unseen}` : this.#where();
  }

  /**
   * @returns {string} One sentence on where the run stands.
   */
  #where() {
    const steps = this.steps();
    const done = steps.filter((step) => step.state === StepState.DONE).length;
    const count = `${done} of ${steps.length} steps done`;
    const current = this.current();
    if (this.safetyStop !== null) return `Stopped by a safety limit in ${current?.name ?? 'the run'}: ${this.safetyStop}. ${count}.`;
    if (current?.state === StepState.FAILED) return `Aborted at ${current.name}: ${current.reason}. ${count}.`;
    if (current?.state === StepState.RETRYING) return `Retrying ${current.name}: ${current.reason}. ${count}.`;
    if (current?.state === STOPPED_STEP) return `Stopped at ${current.name}. ${count}.`;
    if (current) return `Running ${current.name}. ${count}.`;
    return `${count}.`;
  }

  /**
   * @param {Object} event A `RunEvent`.
   */
  #apply(event) {
    const kind = event[RunEventKey.KIND];
    if (kind === RunEventKind.PERCEPTION) {
      this.#applyPerception(event);
      return;
    }
    if (kind === RunEventKind.STEP) this.#applyStep(event);
    else if (kind === RunEventKind.MONITOR) this.#applyMonitor(event);
    else if (kind === RunEventKind.PERCEIVED) {
      const target = event[RunEventKey.TARGET];
      const color = event[RunEventKey.COLOR] ?? null;
      const seen = { step: event[RunEventKey.STEP], target, color };
      const before = this.perceived.findIndex((earlier) => earlier.target === target);
      if (before < 0) this.perceived.push(seen);
      else this.perceived[before] = seen;
      this.log.push(`${this.#name(event)}: perceived ${objectNames(target)}${color ? ` (${color})` : ''}`);
    } else if (kind === RunEventKind.REGROUNDED) {
      this.log.push(`${this.#name(event)}: ${objectNames(event[RunEventKey.TARGET])} found elsewhere on the table; the step was grounded again where it lies`);
    } else if (kind === RunEventKind.CONTINUED_UNSEEN) {
      const step = this.step(event[RunEventKey.STEP]);
      const names = objectNames(event[RunEventKey.TARGET]);
      if (!step.unseen.includes(names)) step.unseen.push(names);
      // where the step was grounded with them: where the plan was validated, or where they were found once the step was grounded again
      this.log.push(`${this.#name(event)}: ${event[RunEventKey.REASON]}; continuing where the plan expected ${names}`);
    } else if (kind === RunEventKind.RECORD_SAVING) {
      this.log.push('Run over; the robot holds while the run\'s record is saved.');
    } else if (kind === RunEventKind.SAFETY_STOP) {
      this.safety = MonitorObservation.VIOLATED;
      this.safetyStop = event[RunEventKey.CONSTRAINT];
      this.log.push(`Safety stop${event[RunEventKey.STEP] ? ` in ${this.#name(event)}` : ''}: ${this.safetyStop}; the robot holds.`);
    }
  }

  /**
   * @param {Object} event A perception's `RunEvent`.
   */
  #applyPerception(event) {
    const report = event[RunEventKey.PERCEPTION];
    const step = event[RunEventKey.STEP] ? this.step(event[RunEventKey.STEP]) : null;
    step?.perceptions.push(report);
    const name = step?.name ?? 'Run';
    this.perceptions.push({ name, report });
    this.log.push(`${name}: ${report[PerceptionReportKey.SUMMARY]}`);
  }

  /**
   * @param {Object} event A step's `RunEvent`.
   */
  #applyStep(event) {
    this.#stepReported = true;
    const step = this.step(event[RunEventKey.STEP]);
    step.state = event[RunEventKey.STATE];
    step.attempt = event[RunEventKey.ATTEMPT] ?? step.attempt;
    step.attempts = event[RunEventKey.ATTEMPTS] ?? step.attempts;
    step.arm = event[RunEventKey.ARM] ?? step.arm;
    if (step.state === StepState.RUNNING) {
      step.nextAttempt = null;
    }
    if ([StepState.RETRYING, StepState.FAILED].includes(step.state)) {
      step.reason = event[RunEventKey.REASON] ?? null;
      step.nextAttempt = event[RunEventKey.NEXT_ATTEMPT] ?? null;
    }
    if (step.state === StepState.DONE) step.reason = null;
    this.log.push(stepLine(step.name, event));
  }

  /**
   * @param {Object} event A monitor's `RunEvent`.
   */
  #applyMonitor(event) {
    const monitor = event[RunEventKey.MONITOR];
    const observation = event[RunEventKey.OBSERVATION];
    if (monitor === RunMonitor.SAFETY) {
      this.safety = observation;
      this.log.push(monitorText(monitor, observation));
      return;
    }
    const target = event[RunEventKey.TARGET] ?? null;
    if (event[RunEventKey.STEP]) this.step(event[RunEventKey.STEP]).monitors.set(`${monitor}/${target}`, { monitor, target, observation });
    this.log.push(`${this.#name(event)}: ${monitorText(monitor, observation)}${target ? ` (${objectNames(target)})` : ''}`);
  }

  /**
   * @param {Object} event A `RunEvent` naming a step.
   * @returns {string} The step as a person reads it.
   */
  #name(event) {
    return this.step(event[RunEventKey.STEP]).name;
  }
}

/**
 * The line that starts a Python traceback.
 */
export const TRACEBACK_HEADER = 'Traceback (most recent call last):';

/**
 * @param {string} log The output of a run the person stopped.
 * @returns {string} The output with its last traceback, which the stop raised to end the
 *   run, cut to the message of its exception: one plain line instead of the frames.
 */
export function withoutStopTraceback(log) {
  const lines = log.split('\n');
  const header = lines.findLastIndex((line) => line.startsWith(TRACEBACK_HEADER));
  const raised = lines.findIndex((line, index) => index > header && line !== '' && !line.startsWith(' '));
  if (header < 0 || raised < 0) return log;
  const separator = lines[raised].indexOf(': ');
  const message = separator < 0 ? lines[raised] : lines[raised].slice(separator + 2);
  return [...lines.slice(0, header), message, ...lines.slice(raised + 1)].join('\n');
}

/**
 * @param {string} name The step as a person reads it.
 * @param {Object} event The step's `RunEvent`.
 * @returns {string} The event as a line of the run log.
 */
export function stepLine(name, event) {
  const state = event[RunEventKey.STATE];
  const arm = event[RunEventKey.ARM] ? ` with the ${event[RunEventKey.ARM]} arm` : '';
  const attempt = event[RunEventKey.ATTEMPT] ? `attempt ${event[RunEventKey.ATTEMPT]}/${event[RunEventKey.ATTEMPTS]} ` : '';
  const reason = event[RunEventKey.REASON] ? `: ${event[RunEventKey.REASON]}` : '';
  if (state === StepState.PENDING) return `${name}: pending${arm}`;
  if (state === StepState.RUNNING) return `${name}: ${attempt}running${arm}`;
  if (state === StepState.DONE) return `${name}: ${attempt}done${arm}`;
  if (state === StepState.RETRYING) {
    const next = event[RunEventKey.NEXT_ATTEMPT] ? ` Next attempt ${event[RunEventKey.NEXT_ATTEMPT]}.` : '';
    return `${name}: ${attempt}failed, retrying${reason}${next}`;
  }
  return `${name}: ${attempt}failed${reason}`;
}

// %% view

/**
 * Shows a run's progress.
 */
export class RunView {
  /**
   * The view's root.
   * @type {HTMLElement}
   */
  element;

  /**
   * Builds the view's elements.
   * @type {import('./elements.js').Elements}
   */
  #elements;

  /**
   * The parts the view fills.
   * @type {Object<string, HTMLElement>}
   */
  #parts = {};

  /**
   * Shows the run's perception reports.
   * @type {PerceptionReportsView}
   */
  #reports;

  /**
   * @param {import('./elements.js').Elements} elements Builds the view's elements.
   */
  constructor(elements) {
    this.#elements = elements;
    const parts = this.#parts;
    this.element = elements.create('section', RunViewClass.VIEW);
    parts.summary = elements.create('p', RunViewClass.SUMMARY);
    parts.badges = elements.create('div', RunViewClass.BADGES);
    parts.steps = elements.create('ol', RunViewClass.STEPS);
    parts.perceived = elements.create('div', RunViewClass.PERCEIVED);
    parts.camera = elements.create('figure', RunViewClass.CAMERA);
    parts.perception = elements.create('div', RunViewClass.PERCEPTION);
    this.#reports = new PerceptionReportsView(elements);
    parts.log = elements.create('ol', RunViewClass.LOG);
    this.element.append(
      parts.summary,
      parts.badges,
      parts.steps,
      elements.create('h3', '', 'Perceived objects'),
      parts.perceived,
      elements.create('h3', '', 'What perception reported'),
      parts.perception,
      elements.create('h3', '', 'Camera'),
      parts.camera,
      elements.create('h3', '', 'Run log'),
      parts.log,
    );
  }

  /**
   * Show a run.
   * @param {RunProgress} progress Where the run stands.
   * @param {Array<Object>} images The `PerceptionImage`s of the latest perception step,
   *   each with the `url` it is served at.
   */
  render(progress, images = []) {
    const elements = this.#elements;
    const parts = this.#parts;
    parts.summary.textContent = progress.summary();
    parts.badges.replaceChildren(...(progress.safety === null ? [] : [this.#badge(monitorText(RunMonitor.SAFETY, progress.safety), progress.safety)]));
    parts.steps.replaceChildren(...progress.steps().map((step) => this.#step(step)));
    parts.perceived.replaceChildren(
      ...(progress.perceived.length === 0
        ? [elements.create('p', RunViewClass.EMPTY, 'Nothing perceived yet.')]
        : progress.perceived.map((seen) => {
            const chip = elements.create('span', RunViewClass.OBJECT);
            const swatch = elements.create('span', RunViewClass.SWATCH);
            swatch.style.background = seen.color ?? 'transparent';
            chip.dataset.color = seen.color ?? '';
            chip.append(swatch, elements.create('span', '', `${objectNames(seen.target)}${seen.color ? ` (${seen.color})` : ''}`));
            return chip;
          })),
    );
    parts.perception.replaceChildren(...this.#reports.render(progress.perceptions));
    parts.camera.replaceChildren(...this.#camera(images));
    parts.log.replaceChildren(...progress.log.map((line) => elements.create('li', '', line)));
  }

  /**
   * Show no run, so nothing of the run before stands in the view.
   */
  clear() {
    for (const part of Object.values(this.#parts)) part.replaceChildren();
    this.#parts.summary.textContent = '';
  }

  /**
   * @param {StepProgress} step A step of the run.
   * @returns {HTMLElement} Its line: name, state, attempt, arm, monitor badges and why
   *   its latest attempt failed.
   */
  #step(step) {
    const elements = this.#elements;
    const item = elements.create('li', RunViewClass.STEP);
    item.dataset.step = step.identifier;
    item.dataset.state = step.state;
    const state = elements.create('span', RunViewClass.STATE, STEP_STATE_TEXTS[step.state] ?? step.state);
    state.dataset.state = step.state;
    item.append(elements.create('span', RunViewClass.NAME, step.name), state);
    if (step.attempt !== null && (step.attempt > 1 || step.state !== StepState.DONE)) item.append(elements.create('span', RunViewClass.ATTEMPT, `attempt ${step.attempt}/${step.attempts}`));
    if (step.arm) item.append(elements.create('span', RunViewClass.ARM, `${step.arm} arm`));
    for (const shown of step.monitors.values()) item.append(this.#badge(monitorText(shown.monitor, shown.observation), shown.observation));
    if (step.reason && step.state !== StepState.DONE) {
      const next = step.nextAttempt && step.state === StepState.RETRYING ? ` Next attempt ${step.nextAttempt}.` : '';
      item.append(elements.create('p', RunViewClass.REASON, `${step.reason}${next}`));
    }
    if (step.unseen.length > 0) {
      item.dataset.unseen = 'true';
      const names = step.unseen.join(', ');
      const pronoun = names.includes(', ') ? 'them' : 'it';
      item.append(elements.create('p', RunViewClass.UNSEEN, `Went ahead without the camera confirming ${names}: it acted where the plan expected ${pronoun}.`));
    }
    const perceived = step.state === StepState.DONE ? null : perceptionSentence(step.perceptions);
    if (perceived) item.append(elements.create('p', PerceptionViewClass.SENTENCE, `Perception: ${perceived}`));
    return item;
  }

  /**
   * @param {string} text What the badge says.
   * @param {string} observation A `MonitorObservation` value.
   * @returns {HTMLElement} The badge.
   */
  #badge(text, observation) {
    const badge = this.#elements.create('span', RunViewClass.BADGE, text);
    badge.dataset.observation = observation;
    return badge;
  }

  /**
   * @param {Array<Object>} images The latest perception step's images with their URLs.
   * @returns {Array<HTMLElement>} The images with their captions, or a note that none
   *   was taken yet.
   */
  #camera(images) {
    const elements = this.#elements;
    if (images.length === 0) return [elements.create('p', RunViewClass.EMPTY, 'No camera image yet.')];
    const shown = [];
    for (const image of images) {
      const picture = elements.create('img');
      picture.src = image.url;
      picture.alt = image[PerceptionImageKey.TITLE];
      shown.push(picture);
    }
    shown.push(elements.create('figcaption', '', `Latest perception: ${images.map((image) => image[PerceptionImageKey.TITLE]).join(', ')}.`));
    return shown;
  }
}
