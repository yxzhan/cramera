/**
 * One graph tab: the plan, statechart or kinematics graph of the shown
 * scene; clicking a node lights it up in the 3D view. A view holding recorded
 * statecharts follows the playback, drawing the chart of the shown frame.
 */

import { GraphViewKey, GraphViewName, MomentKey, StatechartsKey, ViewerEvent } from '../../protocol.js';
import { GraphView } from './graph-view.js';
import { momentAt, NO_CHART_AT_FRAME, NO_MOMENT, statechartView } from './recorded-statechart.js';

/**
 * The titles of the views, in the order their tabs are offered.
 */
export const VIEW_TITLES = Object.freeze({
  [GraphViewName.PLAN]: 'Plan',
  [GraphViewName.STATECHART]: 'Statechart',
  [GraphViewName.KINEMATICS]: 'Kinematics',
});

// %% panel

export class GraphPanel {
  /**
   * The panel's element.
   * @type {HTMLElement}
   */
  element;

  /**
   * The `GraphViewName` value the panel draws.
   * @type {string}
   */
  view;

  /**
   * The drawn graph.
   * @type {GraphView}
   */
  #graph;

  /**
   * What the panel says instead of a graph.
   * @type {HTMLElement}
   */
  #message;

  /**
   * Cancels loading a view of a scene that was replaced.
   * @type {AbortController|null}
   */
  #loading = null;

  /**
   * The shown scene's recorded statecharts, or null.
   * @type {Object|null}
   */
  #recorded = null;

  /**
   * The moment drawn from the recorded statecharts, to skip redrawing an unchanged one.
   * @type {number|null}
   */
  #moment = null;

  /**
   * The chart of the drawn moment, so a moment of the same chart keeps the framing.
   * @type {number|null}
   */
  #chart = null;

  /**
   * The frame the playback shows.
   * @type {number}
   */
  #frame = 0;

  /**
   * @param {Document} document The page.
   * @param {string} view The `GraphViewName` value the panel draws.
   * @param {import('../../core/event-bus.js').EventBus} bus The application's event bus.
   */
  constructor(document, view, bus) {
    this.view = view;
    this.element = document.createElement('section');
    this.element.className = 'graph-panel';
    this.#message = document.createElement('p');
    this.#message.className = 'graph-empty';
    this.#graph = new GraphView(document, (identity) => bus.publish(ViewerEvent.ENTITY_HIGHLIGHTED, [identity]));
    this.element.append(this.#graph.element, this.#message);
    bus.subscribe(ViewerEvent.FRAME_CHANGED, (frame) => this.showFrame(frame));
  }

  /**
   * Draw the panel's view of a scene.
   * @param {string} scene The shown bundle.
   * @param {Object} client Where the scene's questions are answered.
   * @returns {Promise<void>} Settles once drawn or failed.
   */
  async showScene(scene, client) {
    this.#loading?.abort();
    const loading = new AbortController();
    this.#loading = loading;
    this.#message.textContent = 'Loading…';
    this.#message.hidden = false;
    const graph = await client.graph(scene, this.view, loading.signal).catch((error) => (loading.signal.aborted ? null : { [GraphViewKey.OK]: false, [GraphViewKey.ERROR]: error.message }));
    if (!graph || loading.signal.aborted) return;
    const nodes = graph[GraphViewKey.NODES] ?? [];
    const recorded = graph[GraphViewKey.RECORDED] ?? null;
    this.#graph.clear();
    this.#recorded = null;
    if (graph[GraphViewKey.OK] === false) this.#message.textContent = graph[GraphViewKey.ERROR];
    else if (recorded?.[StatechartsKey.MOMENTS]?.length) {
      this.#recorded = recorded;
      this.#moment = null;
      this.#chart = null;
      this.showFrame(this.#frame);
    } else if (nodes.length === 0) this.#message.textContent = graph[GraphViewKey.EMPTY] ?? 'This view is empty for the scene.';
    else {
      this.#message.hidden = true;
      this.#graph.show(graph);
    }
  }

  /**
   * Follow the playback: draw the recorded chart of the shown frame.
   * @param {number} frame The shown frame.
   */
  showFrame(frame) {
    this.#frame = frame;
    if (!this.#recorded) return;
    const moment = momentAt(this.#recorded, frame);
    if (moment === this.#moment) return;
    this.#moment = moment;
    if (moment === NO_MOMENT) {
      this.#graph.clear();
      this.#chart = null;
      this.#message.textContent = NO_CHART_AT_FRAME;
      this.#message.hidden = false;
      return;
    }
    const view = statechartView(this.#recorded, moment);
    const chart = momentChart(this.#recorded, moment);
    this.#message.hidden = true;
    this.#graph.show(view, chart === this.#chart);
    this.#chart = chart;
  }
}

/**
 * @param {Object} statecharts A recording's statecharts document.
 * @param {number} moment The position of a moment.
 * @returns {number} The position of the moment's chart.
 */
function momentChart(statecharts, moment) {
  return statecharts[StatechartsKey.MOMENTS][moment][MomentKey.CHART];
}
