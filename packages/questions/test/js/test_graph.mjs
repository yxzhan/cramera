import { test } from 'node:test';
import assert from 'node:assert/strict';

import { EventBus } from 'cramera-viewer/core/event-bus.js';
import { ViewerApi } from 'cramera-viewer/core/viewer-api.js';
import {
  ChartKey,
  ChartNodeKey,
  GraphDetailKey,
  GraphEdgeKey,
  GraphEdgeKind,
  GraphLayout,
  GraphNodeKey,
  GraphViewKey,
  GraphViewName,
  LegendEntryKey,
  MomentKey,
  QuestionsParameter,
  QuestionsRoute,
  StatechartsKey,
  ViewerEvent,
} from 'cramera-viewer/protocol.js';
import { FakeDocument } from 'cramera-testing/fake-document.mjs';

import { placeNodes } from '../../src/cramera_questions/web/graph-layout.js';
import { GraphPanel, VIEW_TITLES } from '../../src/cramera_questions/web/graph-panel.js';
import { GraphView, PALETTE_SIZE } from '../../src/cramera_questions/web/graph-view.js';
import { QuestionsClient } from '../../src/cramera_questions/web/questions-client.js';
import { LIFE_CYCLES, momentAt, NO_CHART_AT_FRAME, NO_MOMENT, statechartView } from '../../src/cramera_questions/web/recorded-statechart.js';

const BASE = 'https://sunava.github.io/cramera/';

function node(identifier, group) {
  return { [GraphNodeKey.IDENTIFIER]: identifier, [GraphNodeKey.LABEL]: identifier, [GraphNodeKey.GROUP]: group };
}

function edge(source, destination, kind = GraphEdgeKind.PROPERTY) {
  return { [GraphEdgeKey.SOURCE]: source, [GraphEdgeKey.DESTINATION]: destination, [GraphEdgeKey.KIND]: kind };
}

function treeView(layout) {
  return {
    [GraphViewKey.VIEW]: GraphViewName.KINEMATICS,
    [GraphViewKey.LAYOUT]: layout,
    [GraphViewKey.NODES]: [node('base', 'link'), node('left', 'link'), node('right', 'joint')],
    [GraphViewKey.EDGES]: [edge('base', 'left'), edge('base', 'right', GraphEdgeKind.TYPE)],
    [GraphViewKey.LEGEND]: [
      { [LegendEntryKey.GROUP]: 'link', [LegendEntryKey.LABEL]: 'Link' },
      { [LegendEntryKey.GROUP]: 'joint', [LegendEntryKey.LABEL]: 'Joint' },
    ],
    [GraphViewKey.DETAILS]: { base: { [GraphDetailKey.LABEL]: 'base', [GraphDetailKey.GROUP]: 'link', [GraphDetailKey.LINES]: ['mass: 1'] } },
  };
}

function fakeFetch(answers, requested) {
  return async (url, options = {}) => {
    requested.push(String(url));
    if (options.signal?.aborted) throw new DOMException('aborted', 'AbortError');
    const answer = answers.shift();
    return { ok: true, status: 200, json: async () => answer };
  };
}

// %% layout

test('a hierarchy puts children one layer below their parent, centred under it', () => {
  const places = placeNodes(treeView(GraphLayout.HIERARCHY));
  const [base, left, right] = ['base', 'left', 'right'].map((name) => places.get(name));
  assert.ok(left.y > base.y);
  assert.equal(left.y, right.y);
  assert.notEqual(left.x, right.x);
  assert.equal(base.x, (left.x + right.x) / 2);
});

test('a left-to-right hierarchy swaps the axes', () => {
  const down = placeNodes(treeView(GraphLayout.HIERARCHY));
  const across = placeNodes(treeView(GraphLayout.HIERARCHY_LEFT_RIGHT));
  for (const [name, place] of down) assert.deepEqual({ ...across.get(name) }, { x: place.y, y: place.x });
});

test('a free layout places every node, deterministically and apart', () => {
  const view = treeView(GraphLayout.FREE);
  const first = placeNodes(view);
  assert.deepEqual(placeNodes(view), first);
  const places = [...first.values()];
  assert.equal(places.length, 3);
  assert.ok(places.every((place) => Number.isFinite(place.x) && Number.isFinite(place.y)));
  assert.equal(new Set(places.map((place) => `${place.x},${place.y}`)).size, 3);
});

test('a view without nodes places nothing', () => {
  assert.equal(placeNodes({}).size, 0);
});

// %% view

test('the view draws edges, nodes coloured by legend order, and the legend', () => {
  const document = new FakeDocument();
  const graph = new GraphView(document, () => {});
  graph.show(treeView(GraphLayout.HIERARCHY));
  const lines = graph.element.findAll((element) => element.tagName === 'line');
  assert.equal(lines.length, 2);
  assert.ok(lines[1].getAttribute('class').includes(GraphEdgeKind.TYPE));
  const marks = graph.element.findAll((element) => element.getAttribute('class')?.startsWith('graph-node'));
  assert.deepEqual(marks.map((mark) => mark.getAttribute('class')), ['graph-node series-0', 'graph-node series-0', 'graph-node series-1']);
  const legend = graph.element.findAll((element) => element.tagName === 'LI');
  assert.deepEqual(legend.map((item) => item.textContent), ['Link', 'Joint']);
  assert.ok(PALETTE_SIZE >= legend.length);
});

test('clicking a node shows its details and reports its identity', () => {
  const document = new FakeDocument();
  const chosen = [];
  const graph = new GraphView(document, (identity) => chosen.push(identity));
  graph.show(treeView(GraphLayout.HIERARCHY));
  const [base] = graph.element.findAll((element) => element.getAttribute('class')?.startsWith('graph-node'));
  base.dispatch('click');
  assert.deepEqual(chosen, ['base']);
  assert.match(graph.element.innerText, /mass: 1/);
});

// %% controls

test('the buttons zoom around the middle, fit the graph again and fill the window', () => {
  const graph = new GraphView(new FakeDocument(), () => {});
  graph.show(treeView(GraphLayout.HIERARCHY));
  const drawing = graph.element.children[0];
  const fitted = drawing.getAttribute('viewBox').split(' ').map(Number);
  const [fullscreen, zoomIn, zoomOut, fit] = graph.element.children[1].children;
  zoomIn.dispatch('click');
  const zoomed = drawing.getAttribute('viewBox').split(' ').map(Number);
  assert.ok(zoomed[2] < fitted[2]);
  assert.ok(Math.abs(zoomed[0] + zoomed[2] / 2 - (fitted[0] + fitted[2] / 2)) < 1e-9);
  zoomOut.dispatch('click');
  zoomOut.dispatch('click');
  assert.ok(Number(drawing.getAttribute('viewBox').split(' ')[2]) > fitted[2]);
  fit.dispatch('click');
  assert.deepEqual(drawing.getAttribute('viewBox').split(' ').map(Number), fitted);
  fullscreen.dispatch('click');
  assert.equal(graph.element.classList.contains('graph-fullscreen'), true);
});

// %% panel

test('a tab draws its own view of the shown scene from the scene\'s client', async () => {
  const document = new FakeDocument();
  const requested = [];
  const api = new ViewerApi(fakeFetch([treeView(GraphLayout.HIERARCHY)], requested), BASE);
  const bus = new EventBus();
  const highlighted = [];
  bus.subscribe(ViewerEvent.ENTITY_HIGHLIGHTED, (identities) => highlighted.push(identities));
  const panel = new GraphPanel(document, GraphViewName.KINEMATICS, bus);
  assert.deepEqual(Object.keys(VIEW_TITLES), [GraphViewName.PLAN, GraphViewName.STATECHART, GraphViewName.KINEMATICS]);
  await panel.showScene('pr2-kitchen', new QuestionsClient(api));
  const url = new URL(requested[0]);
  assert.ok(url.pathname.endsWith(QuestionsRoute.GRAPH));
  assert.equal(url.searchParams.get(QuestionsParameter.SCENE), 'pr2-kitchen');
  assert.equal(url.searchParams.get(QuestionsParameter.VIEW), GraphViewName.KINEMATICS);
  const [base] = panel.element.findAll((element) => element.getAttribute('class')?.startsWith('graph-node'));
  base.dispatch('click');
  assert.deepEqual(highlighted, [['base']]);
});

test('a tab says why its view cannot be drawn, or that it is empty', async () => {
  const document = new FakeDocument();
  const answers = [
    { [GraphViewKey.OK]: false, [GraphViewKey.ERROR]: 'krrood is not installed' },
    { [GraphViewKey.OK]: true, [GraphViewKey.NODES]: [], [GraphViewKey.EMPTY]: 'This scene has no plan.' },
  ];
  const client = new QuestionsClient(new ViewerApi(fakeFetch(answers, []), BASE));
  const panel = new GraphPanel(document, GraphViewName.PLAN, new EventBus());
  await panel.showScene('pr2-kitchen', client);
  assert.match(panel.element.innerText, /krrood is not installed/);
  await panel.showScene('pr2-kitchen', client);
  assert.match(panel.element.innerText, /This scene has no plan\./);
  assert.equal(panel.element.findAll((element) => element.tagName === 'circle').length, 0);
});

test('a scene shown while another loads replaces it', async () => {
  let release;
  const slow = { graph: () => new Promise((resolve) => { release = resolve; }) };
  const fast = { graph: async () => ({ [GraphViewKey.NODES]: [], [GraphViewKey.EMPTY]: 'second' }) };
  const panel = new GraphPanel(new FakeDocument(), GraphViewName.PLAN, new EventBus());
  const first = panel.showScene('first', slow);
  await panel.showScene('second', fast);
  release({ [GraphViewKey.NODES]: [], [GraphViewKey.EMPTY]: 'first' });
  await first;
  assert.match(panel.element.innerText, /second/);
});

// %% recorded statecharts

function chartNode(identifier, name, parent = null, className = name) {
  return { [ChartNodeKey.IDENTIFIER]: identifier, [ChartNodeKey.NAME]: name, [ChartNodeKey.CLASS_NAME]: className, [ChartNodeKey.PARENT]: parent };
}

function moment(chart, lifeCycles) {
  return { [MomentKey.CHART]: chart, [MomentKey.LIFE_CYCLES]: lifeCycles, [MomentKey.OBSERVATIONS]: lifeCycles.map(() => 'UNKNOWN') };
}

/**
 * Two charts: a sequence of a reach and a grasp, then a lone place; frame 0 runs no chart.
 */
function recordedStatecharts() {
  return {
    [StatechartsKey.CHARTS]: [
      { [ChartKey.TITLE]: 'PickUp', [ChartKey.NODES]: [chartNode('n0', 'Sequence'), chartNode('n1', 'Reach', 'n0'), chartNode('n2', 'Grasp', 'n0')], [ChartKey.EDGES]: [] },
      { [ChartKey.TITLE]: 'Place', [ChartKey.NODES]: [chartNode('m0', 'Place')], [ChartKey.EDGES]: [] },
    ],
    [StatechartsKey.MOMENTS]: [
      moment(0, ['RUNNING', 'RUNNING', 'NOT_STARTED']),
      moment(0, ['RUNNING', 'SUCCEEDED', 'RUNNING']),
      moment(1, ['RUNNING']),
    ],
    [StatechartsKey.FRAMES]: [NO_MOMENT, 0, 0, 1, 2],
  };
}

function recordedView() {
  return { [GraphViewKey.OK]: true, [GraphViewKey.VIEW]: GraphViewName.STATECHART, [GraphViewKey.NODES]: [], [GraphViewKey.EMPTY]: 'none', [GraphViewKey.RECORDED]: recordedStatecharts() };
}

function drawnNodes(panel) {
  return panel.element.findAll((element) => element.getAttribute('class')?.startsWith('graph-node'));
}

function emptyMessage(panel) {
  return panel.element.findAll((element) => element.className === 'graph-empty')[0];
}

test('a frame names its moment, or none past the recording or without a chart', () => {
  const statecharts = recordedStatecharts();
  assert.deepEqual([0, 1, 3, 4, 99].map((frame) => momentAt(statecharts, frame)), [NO_MOMENT, 0, 1, 2, NO_MOMENT]);
});

test('a moment is drawn as its chart\'s tree, each node coloured by its life cycle', () => {
  const view = statechartView(recordedStatecharts(), 1);
  const nodes = view[GraphViewKey.NODES];
  assert.deepEqual(nodes.map((node) => [node[GraphNodeKey.LABEL], node[GraphNodeKey.GROUP]]), [['Sequence', 'RUNNING'], ['Reach', 'SUCCEEDED'], ['Grasp', 'RUNNING']]);
  assert.deepEqual(view[GraphViewKey.EDGES].map((edge) => [edge[GraphEdgeKey.SOURCE], edge[GraphEdgeKey.DESTINATION]]), [['n0', 'n1'], ['n0', 'n2']]);
  assert.equal(view[GraphViewKey.LAYOUT], GraphLayout.HIERARCHY);
  assert.deepEqual(view[GraphViewKey.LEGEND].map((entry) => entry[LegendEntryKey.GROUP]), LIFE_CYCLES);
  assert.deepEqual(view[GraphViewKey.DETAILS].n1[GraphDetailKey.LINES], ['Reach', 'life cycle: SUCCEEDED', 'observation: UNKNOWN']);
});

test('CRAM\'s internal nodes are folded into the node above them, which counts them', () => {
  const statecharts = {
    [StatechartsKey.CHARTS]: [{
      [ChartKey.TITLE]: 'Reach',
      [ChartKey.NODES]: [
        chartNode('a', 'Avoidance'),
        chartNode('t0', 'task 0', 'a', '_AvoidanceTask'),
        chartNode('d0', 'data 0', 't0', '_AvoidanceHasData'),
        chartNode('t1', 'task 1', 'a', '_AvoidanceTask'),
        chartNode('o', 'orphan', null, '_Loose'),
      ],
      [ChartKey.EDGES]: [],
    }],
    [StatechartsKey.MOMENTS]: [moment(0, ['RUNNING', 'RUNNING', 'PAUSED', 'FAILED', 'RUNNING'])],
    [StatechartsKey.FRAMES]: [0],
  };
  const view = statechartView(statecharts, 0);
  assert.deepEqual(view[GraphViewKey.NODES].map((node) => node[GraphNodeKey.IDENTIFIER]), ['a', 'o']);
  assert.deepEqual(view[GraphViewKey.EDGES], []);
  assert.equal(view[GraphViewKey.DETAILS].a[GraphDetailKey.LINES].at(-1), 'internal nodes: 1 RUNNING, 1 PAUSED, 1 FAILED');
});

test('a life cycle the legend does not know gets a row of its own', () => {
  const statecharts = recordedStatecharts();
  statecharts[StatechartsKey.MOMENTS][2] = moment(1, ['DONE']);
  const legend = statechartView(statecharts, 2)[GraphViewKey.LEGEND].map((entry) => entry[LegendEntryKey.GROUP]);
  assert.deepEqual(legend, [...LIFE_CYCLES, 'DONE']);
});

test('a saved scene\'s statechart tab draws the chart of the shown frame and follows the playback', async () => {
  const bus = new EventBus();
  const panel = new GraphPanel(new FakeDocument(), GraphViewName.STATECHART, bus);
  await panel.showScene('robocasa-run', { graph: async () => recordedView() });
  assert.equal(drawnNodes(panel).length, 0);
  assert.equal(emptyMessage(panel).textContent, NO_CHART_AT_FRAME);
  bus.publish(ViewerEvent.FRAME_CHANGED, 1);
  assert.equal(emptyMessage(panel).hidden, true);
  const reach = () => drawnNodes(panel)[1].findAll((element) => element.tagName === 'title')[0].textContent;
  assert.equal(drawnNodes(panel).length, 3);
  assert.equal(reach(), 'Reach: RUNNING · UNKNOWN');
  bus.publish(ViewerEvent.FRAME_CHANGED, 3);
  assert.equal(reach(), 'Reach: SUCCEEDED · UNKNOWN');
  bus.publish(ViewerEvent.FRAME_CHANGED, 4);
  assert.equal(drawnNodes(panel).length, 1);
  bus.publish(ViewerEvent.FRAME_CHANGED, 0);
  assert.equal(drawnNodes(panel).length, 0);
  assert.equal(emptyMessage(panel).hidden, false);
});

test('a saved scene opened at a later frame draws that frame\'s chart', async () => {
  const bus = new EventBus();
  const panel = new GraphPanel(new FakeDocument(), GraphViewName.STATECHART, bus);
  bus.publish(ViewerEvent.FRAME_CHANGED, 4);
  await panel.showScene('robocasa-run', { graph: async () => recordedView() });
  assert.equal(drawnNodes(panel).length, 1);
});

test('a scene without recorded statecharts still says its view is empty', async () => {
  const bus = new EventBus();
  const panel = new GraphPanel(new FakeDocument(), GraphViewName.STATECHART, bus);
  await panel.showScene('first', { graph: async () => recordedView() });
  await panel.showScene('second', { graph: async () => ({ [GraphViewKey.NODES]: [], [GraphViewKey.EMPTY]: 'no chart' }) });
  bus.publish(ViewerEvent.FRAME_CHANGED, 1);
  assert.equal(drawnNodes(panel).length, 0);
  assert.equal(emptyMessage(panel).textContent, 'no chart');
});
