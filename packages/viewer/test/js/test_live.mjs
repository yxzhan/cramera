import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EventBus } from '../../src/cramera/web/core/event-bus.js';
import { liveAddressOf, sceneOf, withLive } from '../../src/cramera/web/core/scene-address.js';
import { LiveClient, readAddressOf } from '../../src/cramera/web/live/live-client.js';
import { LiveListener, LiveSession, PollInterval } from '../../src/cramera/web/live/live-session.js';
import { DEFAULT_LIVE_ADDRESS, LiveActions, LiveBadge, LiveBadgeState, LivePanel, offeredText, recordingText } from '../../src/cramera/web/panels/live-panel.js';
import {
  Capability,
  DetectedEventsKey,
  LiveInfoKey,
  LiveRoute,
  LiveSceneKey,
  LiveStateKey,
  RecordingSaveRequestKey,
  RecordingState,
  RecordingStatusKey,
  SceneDestination,
  ViewerEvent,
} from '../../src/cramera/web/protocol.js';
import { FakeDocument } from './support/fake-document.mjs';

const INFO = {
  [LiveInfoKey.PUBLISHER]: 'cramera-bridge 1.0.0',
  [LiveInfoKey.SCENE]: '__live__',
  [LiveInfoKey.BUNDLE_SIGNATURE]: 'world-1',
  [LiveInfoKey.CAPABILITIES]: [Capability.PLAN, Capability.STATECHART, Capability.RECORDING],
};

/**
 * Timers that run only when the test says so.
 */
class ManualTimers {
  constructor() {
    this.intervals = new Map();
    this.next = 1;
  }

  setInterval(callback, milliseconds) {
    const id = this.next++;
    this.intervals.set(id, { callback, milliseconds });
    return id;
  }

  clearInterval(id) {
    this.intervals.delete(id);
  }
}

/**
 * @param {Object<string, *>} answers Answers by route; a function computes one per call.
 * @returns {{client: Object, reads: Array<string>}} A client answering from them.
 */
function fakeClient(answers) {
  const reads = [];
  return {
    reads,
    client: {
      read: async (route) => {
        reads.push(route);
        const answer = typeof answers[route] === 'function' ? answers[route]() : answers[route];
        if (answer instanceof Error) throw answer;
        return answer;
      },
    },
  };
}

function recordingListener() {
  const calls = [];
  const listener = new LiveListener(
    (scene) => calls.push(['scene', scene]),
    (state) => calls.push(['state', state[LiveStateKey.SEQUENCE_NUMBER]]),
    (plan) => calls.push(['plan', plan]),
    (chart) => calls.push(['chart', chart]),
    (status) => calls.push(['recording', status]),
    undefined,
    undefined,
    undefined,
    (detected) => calls.push(['events', detected]),
  );
  return { calls, listener };
}

// %% session

test('a session polls every part at its own interval and stops all of them', () => {
  const timers = new ManualTimers();
  const session = new LiveSession(fakeClient({}).client, timers, new EventBus(), recordingListener().listener);
  session.start();
  assert.deepEqual([...timers.intervals.values()].map((timer) => timer.milliseconds).sort((a, b) => a - b), Object.values(PollInterval).sort((a, b) => a - b));
  session.stop();
  assert.equal(timers.intervals.size, 0);
});

test('a connection is announced once and the world\'s scene shown when its model changes', async () => {
  let signature = 'world-1';
  const { client } = fakeClient({ [LiveRoute.INFO]: () => ({ ...INFO, [LiveInfoKey.BUNDLE_SIGNATURE]: signature }) });
  const bus = new EventBus();
  const connected = [];
  bus.subscribe(ViewerEvent.LIVE_CONNECTED, (info) => connected.push(info));
  const { calls, listener } = recordingListener();
  const session = new LiveSession(client, new ManualTimers(), bus, listener);
  await session.pollInfo();
  await session.pollInfo();
  signature = 'world-2';
  await session.pollInfo();
  assert.equal(connected.length, 1);
  assert.deepEqual(calls, [['scene', '__live__'], ['scene', '__live__']]);
  assert.equal(session.offers(Capability.RECORDING), true);
  assert.equal(session.offers(Capability.TELEOPERATION), false);
});

test('a publisher that stops answering is reported as disconnected', async () => {
  let answering = true;
  const { client } = fakeClient({ [LiveRoute.INFO]: () => (answering ? INFO : new Error('refused')) });
  const bus = new EventBus();
  const disconnected = [];
  bus.subscribe(ViewerEvent.LIVE_DISCONNECTED, () => disconnected.push(true));
  const session = new LiveSession(client, new ManualTimers(), bus, recordingListener().listener);
  await session.pollInfo();
  answering = false;
  await session.pollInfo();
  await session.pollInfo();
  assert.deepEqual(disconnected, [true]);
  assert.equal(session.info, null);
});

test('states apply only once the scene is shown, newest first, never an older one', async () => {
  const sequence = [3, 2, 5];
  const { client } = fakeClient({ [LiveRoute.INFO]: INFO, [LiveRoute.STATE]: () => ({ [LiveStateKey.SEQUENCE_NUMBER]: sequence.shift() }) });
  const { calls, listener } = recordingListener();
  const session = new LiveSession(client, new ManualTimers(), new EventBus(), listener);
  await session.pollState();
  await session.pollInfo();
  await session.pollState();
  session.sceneShown();
  for (let poll = 0; poll < 3; poll++) await session.pollState();
  assert.deepEqual(calls.filter(([kind]) => kind === 'state'), [['state', 3], ['state', 5]]);
});

test('a slow answer is not overtaken by a second request to the same route', async () => {
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const reads = [];
  const client = { read: (route) => { reads.push(route); return route === LiveRoute.INFO ? Promise.resolve(INFO) : slow; } };
  const session = new LiveSession(client, new ManualTimers(), new EventBus(), recordingListener().listener);
  await session.pollInfo();
  session.sceneShown();
  const first = session.pollState();
  await session.pollState();
  release({ [LiveStateKey.SEQUENCE_NUMBER]: 1 });
  await first;
  assert.equal(reads.filter((route) => route === LiveRoute.STATE).length, 1);
});

test('plan, statechart and recording are read only when offered', async () => {
  const offered = fakeClient({ [LiveRoute.INFO]: INFO, [LiveRoute.PLAN]: { p: 1 }, [LiveRoute.STATECHART]: { c: 1 }, [LiveRoute.RECORDING]: { r: 1 } });
  const { calls, listener } = recordingListener();
  const session = new LiveSession(offered.client, new ManualTimers(), new EventBus(), listener);
  await session.pollInfo();
  await session.pollPlan();
  await session.pollRecording();
  assert.deepEqual(calls.slice(1), [['plan', { p: 1 }], ['chart', { c: 1 }], ['recording', { r: 1 }]]);
  const bare = fakeClient({ [LiveRoute.INFO]: { ...INFO, [LiveInfoKey.CAPABILITIES]: [] } });
  const quiet = new LiveSession(bare.client, new ManualTimers(), new EventBus(), recordingListener().listener);
  await quiet.pollInfo();
  await quiet.pollPlan();
  await quiet.pollRecording();
  assert.deepEqual(bare.reads, [LiveRoute.INFO]);
});

test('the detected events are read only when the publisher\'s detectors watch the run', async () => {
  const detected = { [DetectedEventsKey.EVENTS]: [] };
  const watching = fakeClient({ [LiveRoute.INFO]: { ...INFO, [LiveInfoKey.CAPABILITIES]: [Capability.DETECTED_EVENTS] }, [LiveRoute.DETECTED_EVENTS]: detected });
  const { calls, listener } = recordingListener();
  const session = new LiveSession(watching.client, new ManualTimers(), new EventBus(), listener);
  await session.pollInfo();
  await session.pollDetectedEvents();
  assert.deepEqual(calls.slice(1), [['events', detected]]);
  const bare = fakeClient({ [LiveRoute.INFO]: INFO });
  const quiet = new LiveSession(bare.client, new ManualTimers(), new EventBus(), recordingListener().listener);
  await quiet.pollInfo();
  await quiet.pollDetectedEvents();
  assert.deepEqual(bare.reads, [LiveRoute.INFO]);
});

test('the live client reads and posts relative to the publisher address', async () => {
  const requests = [];
  const fetch = async (url, init) => { requests.push({ url, init }); return { ok: true, headers: new Map(), json: async () => ({}) }; };
  const client = new LiveClient(fetch, 'http://127.0.0.1:8765');
  await client.read(LiveRoute.STATE);
  await client.saveRecording({ [RecordingSaveRequestKey.NAME]: 'run' });
  assert.equal(requests[0].url, `http://127.0.0.1:8765${LiveRoute.STATE}`);
  assert.equal(requests[1].url, `http://127.0.0.1:8765${LiveRoute.RECORDING_SAVE}`);
  assert.equal(requests[1].init.method, 'POST');
});

// %% panels

test('the Live panel connects, reports what the publisher offers, and drives the recording', async () => {
  const actions = [];
  const panel = new LivePanel(new FakeDocument(), new LiveActions(
    (address) => actions.push(['connect', address]),
    () => actions.push(['disconnect']),
    async () => actions.push(['stop']),
    async (request) => { actions.push(['save', request]); return { scene: request[RecordingSaveRequestKey.NAME] }; },
    async () => actions.push(['discard']),
  ));
  const [form, state, recording, notice] = panel.panel.element.children;
  assert.equal(form.children[0].value, DEFAULT_LIVE_ADDRESS);
  form.dispatch('submit');
  panel.showConnecting(true, INFO, 'http://127.0.0.1:9000');
  assert.match(state.textContent, /cramera-bridge 1\.0\.0/);
  assert.equal(panel.panel.element.dataset.connected, 'true');
  form.dispatch('submit');
  panel.showRecording({ [RecordingStatusKey.STATE]: RecordingState.RECORDING, [RecordingStatusKey.FRAME_COUNT]: 12, [RecordingStatusKey.DURATION_SECONDS]: 1.5 });
  recording.children[1].dispatch('click');
  panel.showRecording({ [RecordingStatusKey.STATE]: RecordingState.FINALIZED });
  assert.equal(recording.children.length, 1);
  panel.showRecording({ [RecordingStatusKey.STATE]: RecordingState.FINALIZED, [RecordingStatusKey.SCENE_NAME]: '__recording__' });
  const save = recording.children[1];
  save.children[0].value = 'my_run';
  save.children[1].value = SceneDestination.LOCAL;
  save.dispatch('submit');
  await new Promise((resolve) => setTimeout(resolve, 0));
  recording.children[2].dispatch('click');
  assert.deepEqual(actions.map((action) => action[0]), ['connect', 'disconnect', 'stop', 'save', 'discard']);
  assert.deepEqual(actions[3][1], { [RecordingSaveRequestKey.NAME]: 'my_run', [RecordingSaveRequestKey.DESTINATION]: SceneDestination.LOCAL });
  panel.showRecording({ [RecordingStatusKey.STATE]: RecordingState.IDLE });
  assert.match(notice.textContent, /Saved as my_run/);
  panel.showConnecting(false, null);
  assert.equal(recording.children.length, 0);
});

test('the Live panel names what the publisher offers in words', () => {
  assert.equal(offeredText([Capability.PLAN, Capability.OBJECT_PLACEMENT]), 'the running plan, moving objects');
  assert.equal(offeredText([]), 'nothing');
  assert.equal(offeredText(['future_capability']), 'future_capability');
});

test('the recording is told in a sentence that counts its frames', () => {
  const status = (state, frames) => ({ [RecordingStatusKey.STATE]: state, [RecordingStatusKey.FRAME_COUNT]: frames, [RecordingStatusKey.DURATION_SECONDS]: 1.25 });
  assert.equal(recordingText(status(RecordingState.RECORDING, 1)), 'Recording the run: 1 frame, 1.3 s.');
  assert.match(recordingText(status(RecordingState.FINALIZED, 12)), /^The recording ended \(12 frames, 1\.3 s\); writing it down/);
  assert.match(recordingText({ ...status(RecordingState.FINALIZED, 12), [RecordingStatusKey.SCENE_NAME]: '__recording__' }), /^The recording ended \(12 frames, 1\.3 s\); save it/);
  assert.equal(recordingText(status(RecordingState.IDLE, 0)), 'Not recording.');
});

test('the badge waits, shows the live run, says when it ended, and opens the Live panel', () => {
  const document = new FakeDocument();
  const element = document.createElement('button');
  let opened = 0;
  const badge = new LiveBadge(element, () => { opened += 1; });
  assert.equal(element.hidden, true);
  badge.show(LiveBadgeState.CONNECTING, 'http://127.0.0.1:18765');
  assert.equal(element.hidden, false);
  assert.match(element.textContent, /Waiting for 127\.0\.0\.1:18765/);
  badge.show(LiveBadgeState.LIVE);
  assert.equal(element.dataset.state, LiveBadgeState.LIVE);
  assert.match(element.textContent, /LIVE/);
  badge.show(LiveBadgeState.LOST);
  assert.match(element.textContent, /ended/);
  element.dispatch('click');
  assert.equal(opened, 1);
  badge.show(LiveBadgeState.OFF);
  assert.equal(element.hidden, true);
});

// %% addresses

test('a page address can follow a live publisher instead of naming a bundle', () => {
  const live = withLive('http://localhost:8711/?scene=cell', 'http://127.0.0.1:8765');
  assert.equal(liveAddressOf(live.href), 'http://127.0.0.1:8765');
  assert.equal(sceneOf(live.href), null);
  assert.equal(liveAddressOf('http://localhost:8711/'), null);
});

test('every page reads the default publisher through the viewer', () => {
  for (const relay of ['http://127.0.0.1:8711/api/live/', 'http://localhost:18711/api/live/', 'http://192.168.1.23:8711/api/live/']) {
    for (const address of [DEFAULT_LIVE_ADDRESS, 'http://localhost:8765', 'http://[::1]:8765/']) {
      assert.equal(readAddressOf(address, relay), relay);
    }
  }
});

test('a page reads any other publisher at the address it names', () => {
  const relay = 'http://192.168.1.23:8711/api/live/';
  for (const address of ['http://127.0.0.1:18765', 'http://192.168.1.23:8765', 'not an address']) {
    assert.equal(readAddressOf(address, relay), address);
  }
});

test('the live client reads through the relay as from the publisher', async () => {
  const requests = [];
  const fetch = async (url) => { requests.push(url); return { ok: true, headers: new Map(), json: async () => ({}) }; };
  const client = new LiveClient(fetch, 'http://192.168.1.23:8711/api/live/');
  await client.read(LiveRoute.STATE);
  await client.readQuerySteps(4);
  assert.deepEqual(requests, ['http://192.168.1.23:8711/api/live/state', 'http://192.168.1.23:8711/api/live/query_steps?revision=4']);
  assert.equal(client.perceptionImageUrl('a b'), 'http://192.168.1.23:8711/api/live/perception_image?key=a%20b');
});

test('a publisher that names no bundle yet is asked to bundle its world', async () => {
  const info = { ...INFO };
  delete info[LiveInfoKey.SCENE];
  const { client, reads } = fakeClient({ [LiveRoute.INFO]: info, [LiveRoute.LIVE_SCENE]: { [LiveSceneKey.SCENE]: '__live__' } });
  const { calls, listener } = recordingListener();
  const session = new LiveSession(client, new ManualTimers(), new EventBus(), listener);
  await session.pollInfo();
  await session.pollInfo();
  assert.deepEqual(calls, [['scene', '__live__']]);
  assert.deepEqual(reads, [LiveRoute.INFO, LiveRoute.LIVE_SCENE, LiveRoute.INFO]);
});
