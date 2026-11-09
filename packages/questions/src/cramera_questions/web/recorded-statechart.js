/**
 * The motion statechart a recording was ticking at a frame, as a graph view: the
 * chart's nodes as a tree under their parents, each coloured by the life cycle the
 * recording stored for it at that moment. CRAM's internal nodes, such as the
 * collision avoidance's one task per link, are folded into the nearest node drawn
 * above them, whose details count them by life cycle.
 */

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
  StatechartsKey,
} from '../../protocol.js';

/**
 * The moment position of a frame without a chart state.
 */
export const NO_MOMENT = -1;

/**
 * What the tab says at a frame no motion statechart runs at.
 */
export const NO_CHART_AT_FRAME = 'No motion statechart runs at this frame.';

/**
 * The life cycles in legend order, which picks their colours; life cycles not listed
 * follow in the order they are met.
 */
export const LIFE_CYCLES = Object.freeze(['RUNNING', 'PAUSED', 'NOT_STARTED', 'FAILED', 'SUCCEEDED']);

/**
 * How the class name of an internal node starts.
 */
const INTERNAL_PREFIX = '_';

// %% moments

/**
 * @param {Object} statecharts A recording's statecharts document.
 * @param {number} frame A trajectory frame.
 * @returns {number} The position of the moment shown at the frame, or `NO_MOMENT`.
 */
export function momentAt(statecharts, frame) {
  const moment = statecharts[StatechartsKey.FRAMES]?.[frame] ?? NO_MOMENT;
  return statecharts[StatechartsKey.MOMENTS]?.[moment] ? moment : NO_MOMENT;
}

/**
 * @param {Object} statecharts A recording's statecharts document.
 * @param {number} moment The position of a moment, not `NO_MOMENT`.
 * @returns {Object} The graph view of the moment's chart in the moment's states.
 */
export function statechartView(statecharts, moment) {
  const state = statecharts[StatechartsKey.MOMENTS][moment];
  const chart = statecharts[StatechartsKey.CHARTS][state[MomentKey.CHART]];
  const chartNodes = chart[ChartKey.NODES] ?? [];
  const lifeCycles = state[MomentKey.LIFE_CYCLES] ?? [];
  const observations = state[MomentKey.OBSERVATIONS] ?? [];
  const byIdentity = new Map(chartNodes.map((chartNode) => [chartNode[ChartNodeKey.IDENTIFIER], chartNode]));
  const drawnAbove = (chartNode) => {
    let parent = byIdentity.get(chartNode[ChartNodeKey.PARENT]);
    while (parent && isInternal(parent)) parent = byIdentity.get(parent[ChartNodeKey.PARENT]);
    return parent ?? null;
  };
  const folded = new Map();
  chartNodes.forEach((chartNode, position) => {
    const holder = isInternal(chartNode) ? drawnAbove(chartNode) : null;
    if (!holder) return;
    const counts = folded.get(holder[ChartNodeKey.IDENTIFIER]) ?? new Map();
    counts.set(lifeCycles[position], (counts.get(lifeCycles[position]) ?? 0) + 1);
    folded.set(holder[ChartNodeKey.IDENTIFIER], counts);
  });
  const nodes = [];
  const edges = [];
  const details = {};
  chartNodes.forEach((chartNode, position) => {
    if (isInternal(chartNode) && drawnAbove(chartNode)) return;
    const identity = chartNode[ChartNodeKey.IDENTIFIER];
    const name = chartNode[ChartNodeKey.NAME];
    const lifeCycle = lifeCycles[position];
    const observation = observations[position];
    const parent = drawnAbove(chartNode);
    nodes.push({
      [GraphNodeKey.IDENTIFIER]: identity,
      [GraphNodeKey.LABEL]: name,
      [GraphNodeKey.GROUP]: lifeCycle,
      [GraphNodeKey.TITLE]: `${name}: ${lifeCycle} · ${observation}`,
      [GraphNodeKey.STATUS]: lifeCycle,
      [GraphNodeKey.KIND]: chartNode[ChartNodeKey.CLASS_NAME],
      [GraphNodeKey.PARENT]: parent?.[ChartNodeKey.IDENTIFIER] ?? null,
    });
    const lines = [chartNode[ChartNodeKey.CLASS_NAME], `life cycle: ${lifeCycle}`, `observation: ${observation}`];
    const counts = folded.get(identity);
    if (counts) lines.push(`internal nodes: ${[...counts].map(([folding, count]) => `${count} ${folding}`).join(', ')}`);
    details[identity] = { [GraphDetailKey.LABEL]: name, [GraphDetailKey.GROUP]: lifeCycle, [GraphDetailKey.LINES]: lines };
    if (parent) edges.push({ [GraphEdgeKey.SOURCE]: parent[ChartNodeKey.IDENTIFIER], [GraphEdgeKey.DESTINATION]: identity, [GraphEdgeKey.KIND]: GraphEdgeKind.PROPERTY });
  });
  return {
    [GraphViewKey.OK]: true,
    [GraphViewKey.VIEW]: GraphViewName.STATECHART,
    [GraphViewKey.LAYOUT]: GraphLayout.HIERARCHY,
    [GraphViewKey.NODES]: nodes,
    [GraphViewKey.EDGES]: edges,
    [GraphViewKey.DETAILS]: details,
    [GraphViewKey.LEGEND]: legendOf(lifeCycles),
  };
}

/**
 * @param {Object} chartNode A chart node.
 * @returns {boolean} Whether it is one of CRAM's internal nodes.
 */
function isInternal(chartNode) {
  return (chartNode[ChartNodeKey.CLASS_NAME] ?? '').startsWith(INTERNAL_PREFIX);
}

/**
 * @param {Array<string>} lifeCycles The life cycles of a moment's nodes.
 * @returns {Array<Object>} One legend row per known life cycle, then one per other life
 *     cycle the moment has.
 */
function legendOf(lifeCycles) {
  const groups = [...LIFE_CYCLES, ...new Set(lifeCycles.filter((lifeCycle) => !LIFE_CYCLES.includes(lifeCycle)))];
  return groups.map((group) => ({ [LegendEntryKey.GROUP]: group, [LegendEntryKey.LABEL]: group.replaceAll('_', ' ').toLowerCase() }));
}
