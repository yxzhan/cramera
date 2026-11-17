import { test } from 'node:test';
import assert from 'node:assert/strict';

import { FirstFraming } from '../../src/cramera_plan_builder/web/builder/plan-scene.js';

// %% framing the robot once its model is loaded

test('the view frames the scene again once the first robot model is loaded', () => {
  const framing = new FirstFraming();
  assert.equal(framing.frameOnModelLoaded(), true);
  assert.equal(framing.frameOnModelLoaded(), false);
});

test('a camera the person moved is not framed again when a robot model is loaded', () => {
  const framing = new FirstFraming();
  framing.notePersonMoved();
  assert.equal(framing.frameOnModelLoaded(), false);
});
