import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import {
  ConstraintActivation,
  ConstraintOperation,
  ConstraintRequestKey,
  ErrorKey,
  LivePlanNodeKey,
  LiveRoute,
  MonitorSpecificationKey,
  MoveRequestKey,
  PlanBuilderName,
  PlanBuilderRoute,
  PlanNodeReferenceKey,
  PlanRecordKey,
  PlanStepKey,
  RunMode,
  RunRequestKey,
  StepConstraintKey,
  ValidationRequestKey,
  ViewerRoute,
} from 'cramera-viewer/protocol.js';
import { PlanBuilderApi, RequestRefused } from '../../src/cramera_plan_builder/web/builder/builder-api.js';
import { LiveClient, LiveNames } from '../../src/cramera_plan_builder/web/builder/live-client.js';
import { PlanPose } from '../../src/cramera_plan_builder/web/builder/poses.js';
import { FakeFetch, FakeRefusal } from './support/fake-fetch.mjs';

const read = (name) => JSON.parse(readFileSync(new URL(`dataset/${name}`, import.meta.url), 'utf8'));
const VIEWER = 'http://localhost:8711/base/';
const LIVE = 'http://127.0.0.1:18765';
const route = (path) => `${VIEWER}${ViewerRoute.PLUGIN_API.slice(1)}${PlanBuilderName.PLUGIN}/${path}`;

// %% plan builder routes

test('the routes lie below the viewer, also when it is served below a path', () => {
  const api = new PlanBuilderApi(new FakeFetch().fetch, VIEWER);
  assert.equal(api.url(PlanBuilderRoute.CATALOG), route(PlanBuilderRoute.CATALOG));
  assert.equal(api.objectUrl('big knife.stl'), route(`${PlanBuilderRoute.OBJECT_FILES}big%20knife.stl`));
  assert.equal(api.objectUrl('library/objects/cup #2/model.urdf'), route(`${PlanBuilderRoute.OBJECT_FILES}library/objects/cup%20%232/model.urdf`));
  assert.equal(api.robotUrl('pr2'), route(`${PlanBuilderRoute.ROBOT_FILES}pr2/pr2.urdf`));
});

test('a plan is saved as a record, under its identity once it has one', async () => {
  const fake = new FakeFetch({ [route(PlanBuilderRoute.PLANS)]: { [PlanRecordKey.IDENTIFIER]: 'p1' } });
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await api.save({ name: 'x' }, null);
  await api.save({ name: 'x' }, 'p1');
  const [first, second] = fake.sentTo(route(PlanBuilderRoute.PLANS));
  assert.deepEqual(first.body, { [PlanRecordKey.DOCUMENT]: { name: 'x' } });
  assert.equal(second.body[PlanRecordKey.IDENTIFIER], 'p1');
});

test('a run names the plan and what the demo does', async () => {
  const fake = new FakeFetch({ [route(PlanBuilderRoute.RUN)]: {}, [route(PlanBuilderRoute.RUN_STOP)]: {} });
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await api.run({ name: 'x' }, RunMode.PERFORM);
  await api.runStatus();
  await api.stop();
  const [started, read, stopped] = fake.requests;
  assert.deepEqual(started.body, { [RunRequestKey.PLAN]: { name: 'x' }, [RunRequestKey.MODE]: RunMode.PERFORM });
  assert.equal(read.url, route(PlanBuilderRoute.RUN));
  assert.equal(stopped.url, route(PlanBuilderRoute.RUN_STOP));
});

test('a validation names the plan and a robot run may override it', async () => {
  const fake = new FakeFetch({ [route(PlanBuilderRoute.VALIDATE)]: {}, [route(PlanBuilderRoute.RUN)]: {} });
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await api.validate({ name: 'x' });
  await api.run({ name: 'x' }, RunMode.ROBOT, true);
  const [validated, started] = fake.requests;
  assert.deepEqual(validated.body, { [ValidationRequestKey.PLAN]: { name: 'x' } });
  assert.deepEqual(started.body, { [RunRequestKey.PLAN]: { name: 'x' }, [RunRequestKey.MODE]: RunMode.ROBOT, [RunRequestKey.OVERRIDE_VALIDATION]: true });
});

test('every route is asked at its own address', async () => {
  const urls = [PlanBuilderRoute.CATALOG, PlanBuilderRoute.PLANS, `${PlanBuilderRoute.PLAN}p1`, PlanBuilderRoute.CONSTRAINT, PlanBuilderRoute.DEMO, PlanBuilderRoute.DEMOS].map(route);
  const fake = new FakeFetch(Object.fromEntries(urls.map((url) => [url, {}])));
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await api.catalog();
  await api.plans();
  await api.plan('p1');
  await api.compile({});
  await api.demo({});
  await api.writeDemo({});
  assert.deepEqual(fake.requests.map((request) => request.url), urls);
});

test('a refusal carries the reason the server gave, or the status text', async () => {
  const fake = new FakeFetch({
    [route(PlanBuilderRoute.DEMO)]: new FakeRefusal(422, { [ErrorKey.ERROR]: 'needs a target' }),
    [route(PlanBuilderRoute.CATALOG)]: new FakeRefusal(502, null, 'Bad Gateway'),
  });
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await assert.rejects(api.demo({}), (error) => error instanceof RequestRefused && error.status === 422 && error.message === 'needs a target');
  await assert.rejects(api.catalog(), (error) => error.message === 'Bad Gateway');
});

test('a refusal carries the output of the process behind it apart from its reason', async () => {
  const fake = new FakeFetch({
    [route(PlanBuilderRoute.DEMO)]: new FakeRefusal(502, { [ErrorKey.ERROR]: 'The scene could not be scanned: no answer', [ErrorKey.OUTPUT]: 'a warning\nTraceback' }),
    [route(PlanBuilderRoute.CATALOG)]: new FakeRefusal(422, { [ErrorKey.ERROR]: 'needs a target' }),
  });
  const api = new PlanBuilderApi(fake.fetch, VIEWER);
  await assert.rejects(api.demo({}), (error) => error.message === 'The scene could not be scanned: no answer' && error.output === 'a warning\nTraceback');
  await assert.rejects(api.catalog(), (error) => error.output === null);
});

// %% live publisher

test('the live routes lie below the publisher', async () => {
  const urls = [LiveRoute.INFO, LiveRoute.OBJECTS, LiveRoute.CAPTURED_OBJECTS, LiveRoute.ROBOTS, LiveRoute.PLAN, LiveRoute.SURFACES].map((path) => `${LIVE}${path}`);
  const fake = new FakeFetch(Object.fromEntries(urls.map((url) => [url, {}])));
  const live = new LiveClient(fake.fetch, `${LIVE}/`);
  await live.info();
  await live.objects();
  await live.capturedObjects();
  await live.robots();
  await live.plan();
  await live.surfaces();
  assert.deepEqual(fake.requests.map((request) => request.url), urls);
});

test('a move names the object, its position and orientation', async () => {
  const fake = new FakeFetch({ [`${LIVE}${LiveRoute.MOVE}`]: {}, [`${LIVE}${LiveRoute.CONSTRAINT}`]: {} });
  const live = new LiveClient(fake.fetch, LIVE);
  const pose = PlanPose.of(1, 2, 3, Math.PI);
  await live.move('key-milk', pose, true);
  await live.attach({ [ConstraintRequestKey.TEXT]: 'x' });
  const [move, attach] = fake.requests;
  assert.deepEqual(move.body, {
    [MoveRequestKey.OBJECT]: 'key-milk',
    [MoveRequestKey.POSITION]: PlanPose.position(pose),
    [MoveRequestKey.QUATERNION]: PlanPose.orientation(pose),
    [MoveRequestKey.FINAL]: true,
  });
  assert.deepEqual(attach.body, { [ConstraintRequestKey.TEXT]: 'x' });
});

test('live objects and robots are found by the plan identities', () => {
  const world = read('live_world.json');
  assert.deepEqual([...LiveNames.objectKeys(world.objects)], [['milk', 'key-milk']]);
  const robots = LiveNames.robots(world.robots, ['robot_1', 'robot_2']);
  assert.deepEqual([...robots.keys()], ['robot_1']);
  assert.deepEqual(LiveNames.objectKeys({}), new Map());
  assert.deepEqual(LiveNames.robots({}, ['robot_1']), new Map());
});

test('the steps are the children of the live plan root, in order', () => {
  const plan = read('live_plan.json');
  assert.deepEqual(LiveNames.stepNodes(plan).map((node) => node[LivePlanNodeKey.LABEL]), ['ParkArmsAction', 'PickUpAction']);
  assert.deepEqual(LiveNames.stepNodes({}), []);
});

test('only constraints with a monitor are attached, to their steps nodes', () => {
  const plan = read('live_plan.json');
  const monitor = { [MonitorSpecificationKey.TYPE]: 'HeightMonitor', [MonitorSpecificationKey.PARAMETERS]: { tip_link: 'milk' } };
  const steps = [
    { [PlanStepKey.CONSTRAINTS]: [{ [StepConstraintKey.TEXT]: 'look', [StepConstraintKey.SWITCH]: 'look_at_operation_site' }] },
    { [PlanStepKey.CONSTRAINTS]: [{ [StepConstraintKey.TEXT]: 'above', [StepConstraintKey.MONITOR]: monitor }] },
    { [PlanStepKey.CONSTRAINTS]: [{ [StepConstraintKey.TEXT]: 'not yet published', [StepConstraintKey.MONITOR]: monitor }] },
  ];
  const [request, ...others] = LiveNames.constraintRequests(plan, steps);
  assert.deepEqual(others, []);
  assert.equal(request[ConstraintRequestKey.OPERATION], ConstraintOperation.ATTACH_MONITOR);
  assert.equal(request[ConstraintRequestKey.APPLY], ConstraintActivation.NEXT_ACTIVATION);
  assert.equal(request[ConstraintRequestKey.TEXT], 'above');
  assert.equal(request[ConstraintRequestKey.TARGET_PLAN_NODE][PlanNodeReferenceKey.IDENTIFIER], LiveNames.stepNodes(plan)[1][LivePlanNodeKey.IDENTIFIER]);
  assert.deepEqual(request[ConstraintRequestKey.MONITOR], monitor);
});

// %% poses

test('a heading survives a pose and edits keep the other parts', () => {
  const pose = PlanPose.of(1, 2, 3, 0.5);
  assert.ok(Math.abs(PlanPose.yaw(pose) - 0.5) < 1e-12);
  assert.deepEqual(PlanPose.position(PlanPose.moved(pose, [4.12345, 5, 6])), [4.123, 5, 6]);
  assert.deepEqual(PlanPose.orientation(PlanPose.moved(pose, [0, 0, 0])), PlanPose.orientation(pose));
  assert.deepEqual(PlanPose.position(PlanPose.turned(pose, 1)), [1, 2, 3]);
  assert.equal(PlanPose.degrees(PlanPose.radians(90)), 90);
  assert.equal(PlanPose.isPose(pose), true);
  assert.equal(PlanPose.isPose([1, 2, 3]), false);
});
