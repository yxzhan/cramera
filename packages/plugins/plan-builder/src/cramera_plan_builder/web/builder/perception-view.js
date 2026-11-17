/**
 * What perception reported for each detection of a run, as a person reads it: what was
 * asked, every object that came back with its colors, size and position, which one the
 * step took and why not the others, an empty answer as such, and one sentence per step
 * telling why its detections failed.
 */

import {
  PerceptionReportKey,
  ReportedObjectKey,
  ReportedObjectVerdict,
  SurfaceDepthKey,
} from '../../../protocol.js';

/**
 * How a person reads each verdict on a reported object.
 */
export const VERDICT_TEXTS = Object.freeze({
  [ReportedObjectVerdict.ACCEPTED]: 'taken for the step',
  [ReportedObjectVerdict.TOO_FAR]: 'taken, but too far away for the step to go on',
  [ReportedObjectVerdict.NOT_CHOSEN]: 'would also do, another was taken first',
  [ReportedObjectVerdict.WRONG_COLOR]: 'wrong colour',
  [ReportedObjectVerdict.WRONG_TYPE]: 'not the kind of object looked for',
  [ReportedObjectVerdict.OUTSIDE_BOX]: 'outside the box looked in',
});

/**
 * The classes the perception reports' elements carry.
 */
export const PerceptionViewClass = Object.freeze({
  REPORTS: 'perception-reports',
  REPORT: 'perception-report',
  ASKED: 'perception-asked',
  OBJECTS: 'perception-objects',
  OBJECT: 'perception-object',
  SWATCH: 'color-swatch',
  EMPTY: 'perception-empty',
  SENTENCE: 'run-step-perception',
});

/**
 * @param {number} metres A length.
 * @returns {string} It in centimetres, to a tenth.
 */
const cm = (metres) => `${Math.round(metres * 1000) / 10}`;

/**
 * @param {Array<number>} position A position in metres.
 * @returns {string} It as a person reads it, in metres to the centimetre.
 */
export const at = (position) => `(${position.map((value) => value.toFixed(2)).join(', ')}) m`;

/**
 * Where a step expects its objects until it is planned again.
 */
const VALIDATED_SPOT = 'where the plan was validated';

/**
 * Where a step planned again expects its objects.
 */
const LAST_SEEN_SPOT = 'where it was last seen';

/**
 * @param {Object} report A `PerceptionReport`.
 * @returns {string} Where the step expected the object, which distances are measured
 *   from: where the plan was validated, or, for a step planned again since, where the
 *   object was last seen.
 */
export const spotText = (report) => (report[PerceptionReportKey.VALIDATED] ? LAST_SEEN_SPOT : VALIDATED_SPOT);

/**
 * @param {Object} report A `PerceptionReport`.
 * @returns {string} The object looked for as a person names it, such as "the red cube";
 *   a report recorded before it said so names it from the plan's object or its colour.
 */
export function lookedForText(report) {
  const named = report[PerceptionReportKey.LOOKED_FOR];
  if (named) return named;
  const target = report[PerceptionReportKey.TARGET];
  const color = report[PerceptionReportKey.COLOR];
  if (target) return `the ${target.replaceAll('_', ' ')}`;
  return color ? `a ${color} object` : 'an object';
}

/**
 * @param {string} summary A report's summary.
 * @returns {string} It without its closing full stop.
 */
const clause = (summary) => summary.replace(/\.$/, '');

/**
 * @param {Array<Object>} reports The `PerceptionReport`s of one step, in order.
 * @returns {string|null} Why its detections came to what they did, in one sentence:
 *   each report's summary, a repeated one counted, joined in order; null for none.
 */
export function perceptionSentence(reports) {
  const parts = [];
  for (const report of reports) {
    const summary = clause(report[PerceptionReportKey.SUMMARY] ?? '');
    const last = parts.at(-1);
    if (last && last.summary === summary) last.times += 1;
    else parts.push({ summary, times: 1 });
  }
  if (parts.length === 0) return null;
  const said = parts.map((part, index) => {
    const text = index === 0 ? part.summary : part.summary.charAt(0).toLowerCase() + part.summary.slice(1);
    return part.times > 1 ? `${text} (${part.times} times)` : text;
  });
  return `${said.join('; then ')}.`;
}

/**
 * @param {Object} report A `PerceptionReport`.
 * @returns {string} What it asked, as a person reads it.
 */
export function askedText(report) {
  const box = report[PerceptionReportKey.BOX];
  const size = box ? `${cm(box[1][0] - box[0][0])} × ${cm(box[1][1] - box[0][1])} cm` : '';
  const grounded = report[PerceptionReportKey.GROUNDED];
  const validated = report[PerceptionReportKey.VALIDATED];
  const moved = grounded && validated ? ` (${cm(Math.hypot(grounded[0] - validated[0], grounded[1] - validated[1]))} cm from ${VALIDATED_SPOT})` : '';
  const where = report[PerceptionReportKey.TABLE_WIDE]
    ? 'on the whole table'
    : grounded
      ? `in the ${size} box around ${at(grounded)}, ${spotText(report)}${moved}; the step allows ${cm(report[PerceptionReportKey.TOLERANCE] ?? 0)} cm`
      : `in the ${size} box from ${at(box[0])} to ${at(box[1])}`;
  return `Looked for ${lookedForText(report)} ${where}.`;
}

/**
 * The verdicts of an object the step took.
 */
const TAKEN = new Set([ReportedObjectVerdict.ACCEPTED, ReportedObjectVerdict.TOO_FAR]);

/**
 * @param {Object} seen A `ReportedObject`.
 * @returns {string} How far an object the step took was reported inside the surface it
 *   stands on, which it was raised onto; empty for any other.
 */
export function insideSurfaceText(seen) {
  const inside = seen[ReportedObjectKey.INSIDE_SURFACE];
  if (!inside || !TAKEN.has(seen[ReportedObjectKey.VERDICT])) return '';
  const mm = (metres) => `${Math.max(1, Math.round(metres * 1000))} mm`;
  return `; reported ${mm(inside[SurfaceDepthKey.DEPTH])} inside the ${inside[SurfaceDepthKey.SURFACE].replaceAll('_', ' ')} and raised onto it`;
}

/**
 * @param {Object} seen A `ReportedObject`.
 * @param {Object} report The `PerceptionReport` it came back in, which says where its
 *   distance is measured from.
 * @returns {string} It as a person reads it: colours, size, position, distance, verdict
 *   and how far inside its surface it was reported; the type the perception reported is
 *   left out, a class name no person reads.
 */
export function objectText(seen, report = {}) {
  const colors = (seen[ReportedObjectKey.COLORS] ?? []).join(' and ') || 'no colour';
  const size = seen[ReportedObjectKey.SIZE];
  const extent = size ? `${size.map(cm).join(' × ')} cm` : 'size unknown';
  const distance = seen[ReportedObjectKey.DISTANCE];
  const away = distance === null || distance === undefined ? '' : `, ${cm(distance)} cm from ${spotText(report)}`;
  return `${colors}, ${extent}, at ${at(seen[ReportedObjectKey.POSITION])}${away}: ${VERDICT_TEXTS[seen[ReportedObjectKey.VERDICT]] ?? seen[ReportedObjectKey.VERDICT]}${insideSurfaceText(seen)}`;
}

/**
 * Shows the perception reports of a run.
 */
export class PerceptionReportsView {
  /**
   * Builds the view's elements.
   * @type {import('./elements.js').Elements}
   */
  #elements;

  /**
   * @param {import('./elements.js').Elements} elements Builds the view's elements.
   */
  constructor(elements) {
    this.#elements = elements;
  }

  /**
   * @param {Array<{name: string, report: Object}>} reports Every report of the run with
   *   the name of its step, in order.
   * @returns {Array<HTMLElement>} One block per report, the latest open, or a note that
   *   none came yet.
   */
  render(reports) {
    const elements = this.#elements;
    if (reports.length === 0) return [elements.create('p', 'empty', 'No perception reported yet.')];
    return reports.map(({ name, report }, index) => {
      const block = elements.create('details', PerceptionViewClass.REPORT);
      block.open = index === reports.length - 1;
      block.dataset.answered = String(report[PerceptionReportKey.ANSWERED] !== false);
      const summary = elements.create('summary', '', `${name}: ${report[PerceptionReportKey.SUMMARY]}`);
      block.append(summary, elements.create('p', PerceptionViewClass.ASKED, askedText(report)));
      const objects = report[PerceptionReportKey.OBJECTS] ?? [];
      if (report[PerceptionReportKey.ANSWERED] === false) {
        block.append(elements.create('p', PerceptionViewClass.EMPTY, 'No answer came back.'));
      } else if (objects.length === 0) {
        block.append(elements.create('p', PerceptionViewClass.EMPTY, 'The answer was empty: nothing was reported.'));
      } else {
        const list = elements.create('ul', PerceptionViewClass.OBJECTS);
        for (const seen of objects) {
          const item = elements.create('li', PerceptionViewClass.OBJECT);
          item.dataset.verdict = seen[ReportedObjectKey.VERDICT];
          const swatch = elements.create('span', PerceptionViewClass.SWATCH);
          swatch.style.background = (seen[ReportedObjectKey.COLORS] ?? [])[0] ?? 'transparent';
          item.append(swatch, elements.create('span', '', objectText(seen, report)));
          list.append(item);
        }
        block.append(list);
      }
      return block;
    });
  }
}
