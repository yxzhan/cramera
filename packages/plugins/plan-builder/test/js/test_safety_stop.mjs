import { test } from 'node:test';
import assert from 'node:assert/strict';

import { RunState, RunStatusKey, SafetyStopKey } from 'cramera-viewer/protocol.js';
import { SafetyBannerState, SafetyStopBanner } from '../../src/cramera_plan_builder/web/builder/safety-stop.js';

/**
 * @param {boolean} acknowledged Whether a person acknowledged the stop.
 * @returns {Object} The `RunStatus` of a demo a safety limit stopped.
 */
const stopped = (acknowledged) => ({
  [RunStatusKey.STATE]: RunState.SAFETY_STOPPED,
  [RunStatusKey.SAFETY_STOP]: { [SafetyStopKey.CONSTRAINT]: 'Left arm workspace', [SafetyStopKey.ACKNOWLEDGED]: acknowledged },
});

test('a run without a safety stop shows no banner and blocks nothing', () => {
  const banner = new SafetyStopBanner();
  const finished = { [RunStatusKey.STATE]: RunState.FINISHED };
  assert.equal(banner.state(finished), SafetyBannerState.HIDDEN);
  assert.equal(banner.blocksRuns(finished), false);
});

test('an unacknowledged stop names the broken limit and blocks runs', () => {
  const banner = new SafetyStopBanner();
  assert.equal(banner.state(stopped(false)), SafetyBannerState.VIOLATED);
  assert.equal(banner.blocksRuns(stopped(false)), true);
  assert.equal(banner.message(stopped(false)), 'Safety constraint violated: Left arm workspace');
});

test('an acknowledged stop offers restart or abort until one is chosen', () => {
  const banner = new SafetyStopBanner();
  assert.equal(banner.state(stopped(true)), SafetyBannerState.ACKNOWLEDGED);
  assert.equal(banner.blocksRuns(stopped(true)), false);
  banner.settle();
  assert.equal(banner.state(stopped(true)), SafetyBannerState.HIDDEN);
});

test('settling a stop never hides the stop of a later run', () => {
  const banner = new SafetyStopBanner();
  banner.settle();
  banner.reset();
  assert.equal(banner.state(stopped(true)), SafetyBannerState.ACKNOWLEDGED);
  assert.equal(banner.state(stopped(false)), SafetyBannerState.VIOLATED);
});

test('the banner says how the limit was broken, as the run log wrote it', () => {
  const constraint = 'left arm joint velocity: tracy/left_shoulder_pan_joint moved at 0.230 rad/s, faster than its limit 0.200 rad/s';
  const run = {
    [RunStatusKey.STATE]: RunState.SAFETY_STOPPED,
    [RunStatusKey.SAFETY_STOP]: { [SafetyStopKey.CONSTRAINT]: constraint, [SafetyStopKey.ACKNOWLEDGED]: false },
  };
  assert.equal(new SafetyStopBanner().message(run), `Safety constraint violated: ${constraint}`);
});
