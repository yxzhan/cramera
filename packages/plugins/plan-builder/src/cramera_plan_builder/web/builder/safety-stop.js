/**
 * The banner a safety stop shows: the broken limit until a person acknowledges it, then
 * the choice between restarting the plan from its beginning and aborting. Nothing
 * resumes on its own, and no run or validation starts before the acknowledgement.
 */

import { RunStatusKey, SafetyStopKey } from '../../../protocol.js';

/**
 * What the banner shows.
 */
export const SafetyBannerState = Object.freeze({
  /** No safety stop is to be dealt with. */
  HIDDEN: 'hidden',
  /** A safety limit stopped the run and nobody acknowledged it yet. */
  VIOLATED: 'violated',
  /** The stop was acknowledged; the plan is restarted from its beginning or aborted. */
  ACKNOWLEDGED: 'acknowledged',
});

/**
 * Follows the safety stop of the demo started last, as the plan builder reports it.
 */
export class SafetyStopBanner {
  /**
   * Whether the person chose to restart or abort after acknowledging the stop shown.
   */
  #settled = false;

  /**
   * @param {Object} run The `RunStatus` of the demo started last.
   * @returns {string} A `SafetyBannerState` value.
   */
  state(run) {
    const stop = run[RunStatusKey.SAFETY_STOP];
    if (!stop) return SafetyBannerState.HIDDEN;
    if (!stop[SafetyStopKey.ACKNOWLEDGED]) return SafetyBannerState.VIOLATED;
    return this.#settled ? SafetyBannerState.HIDDEN : SafetyBannerState.ACKNOWLEDGED;
  }

  /**
   * @param {Object} run The `RunStatus` of the demo started last.
   * @returns {boolean} Whether no run or validation may start: a stop awaits its
   *   acknowledgement.
   */
  blocksRuns(run) {
    return this.state(run) === SafetyBannerState.VIOLATED;
  }

  /**
   * @param {Object} run The `RunStatus` of a demo a safety stop cancelled.
   * @returns {string} What the banner says.
   */
  message(run) {
    return `Safety constraint violated: ${run[RunStatusKey.SAFETY_STOP][SafetyStopKey.CONSTRAINT]}`;
  }

  /**
   * The person restarted the plan or aborted it, so the acknowledged stop is dealt with.
   */
  settle() {
    this.#settled = true;
  }

  /**
   * A new demo started, whose own stop, if any, is to be dealt with anew.
   */
  reset() {
    this.#settled = false;
  }
}
