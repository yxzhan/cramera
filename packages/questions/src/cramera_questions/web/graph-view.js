/**
 * A graph view drawn as SVG: edges, nodes coloured by their legend group, labels, a
 * legend, and the details of the node a person clicks. Dragging pans, the wheel zooms.
 */

import { GraphDetailKey, GraphEdgeKey, GraphNodeKey, GraphViewKey, LegendEntryKey } from '../../protocol.js';
import { placeNodes } from './graph-layout.js';

/**
 * The SVG namespace.
 */
const SVG = 'http://www.w3.org/2000/svg';

/**
 * How many colours the categorical palette of `questions.css` has.
 */
export const PALETTE_SIZE = 8;

/**
 * The radius of a node and the margin around the drawing, in drawing units.
 */
const Size = Object.freeze({ NODE: 9, MARGIN: 40, EDGE: 1.2 });

/**
 * The width a hidden drawing is assumed to have, in CSS pixels, so its nodes are sized
 * before its tab is shown.
 */
const ASSUMED_WIDTH = 700;

/**
 * How much one wheel step zooms.
 */
const ZOOM_STEP = 1.1;

/**
 * How much one press of a zoom button zooms.
 */
const BUTTON_ZOOM_STEP = 1.4;

/**
 * The view's buttons: their text and what they are for.
 */
const CONTROLS = Object.freeze({
  FULLSCREEN: { TEXT: '⛶', LABEL: 'Show the graph over the whole window' },
  ZOOM_IN: { TEXT: '+', LABEL: 'Zoom in' },
  ZOOM_OUT: { TEXT: '−', LABEL: 'Zoom out' },
  FIT: { TEXT: '⤡', LABEL: 'Fit the graph' },
});

// %% view

export class GraphView {
  /**
   * The view's element.
   * @type {HTMLElement}
   */
  element;

  /**
   * The drawing.
   * @type {SVGSVGElement}
   */
  #drawing;

  /**
   * The legend.
   * @type {HTMLElement}
   */
  #legend;

  /**
   * The details of the clicked node.
   * @type {HTMLElement}
   */
  #details;

  /**
   * Called with a clicked node's identity.
   * @type {function(string): void}
   */
  #onSelect;

  /**
   * The drawing's view box: left, top, width, height.
   * @type {Array<number>}
   */
  #box = [0, 0, 1, 1];

  /**
   * Where the drawn nodes are.
   * @type {Array<{x: number, y: number}>}
   */
  #places = [];

  /**
   * The drawn nodes with where they are, so they keep their size on screen at every
   * zoom.
   * @type {Array<{mark: SVGGElement, place: {x: number, y: number}}>}
   */
  #marks = [];

  /**
   * The drawn edges.
   * @type {SVGGElement|null}
   */
  #edges = null;

  /**
   * @param {Document} document The page.
   * @param {function(string): void} onSelect Called with a clicked node's identity.
   */
  constructor(document, onSelect) {
    this.#onSelect = onSelect;
    this.element = document.createElement('div');
    this.element.className = 'graph';
    this.#drawing = document.createElementNS(SVG, 'svg');
    this.#drawing.setAttribute('role', 'img');
    this.#legend = document.createElement('ul');
    this.#legend.className = 'legend';
    this.#details = document.createElement('div');
    this.#details.className = 'graph-details';
    this.element.append(this.#drawing, this.#controls(document), this.#legend, this.#details);
    this.#addNavigation();
  }

  /**
   * Draw a graph view.
   * @param {Object} view A graph view.
   * @param {boolean} [keepFraming=false] Keep the panned and zoomed region, for a view
   *     whose nodes sit where the drawn view's do.
   */
  show(view, keepFraming = false) {
    const document = this.element.ownerDocument;
    const nodes = view[GraphViewKey.NODES] ?? [];
    const groups = (view[GraphViewKey.LEGEND] ?? []).map((entry) => entry[LegendEntryKey.GROUP]);
    const placements = placeNodes(view);
    const edges = document.createElementNS(SVG, 'g');
    for (const edge of view[GraphViewKey.EDGES] ?? []) {
      const from = placements.get(edge[GraphEdgeKey.SOURCE]);
      const to = placements.get(edge[GraphEdgeKey.DESTINATION]);
      if (!from || !to) continue;
      const line = document.createElementNS(SVG, 'line');
      line.setAttribute('class', `graph-edge ${edge[GraphEdgeKey.KIND] ?? ''}`.trim());
      line.setAttribute('x1', from.x);
      line.setAttribute('y1', from.y);
      line.setAttribute('x2', to.x);
      line.setAttribute('y2', to.y);
      edges.append(line);
    }
    const marks = document.createElementNS(SVG, 'g');
    this.#marks = [];
    this.#edges = edges;
    for (const node of nodes) {
      const place = placements.get(node[GraphNodeKey.IDENTIFIER]);
      const mark = document.createElementNS(SVG, 'g');
      mark.setAttribute('class', `graph-node series-${groups.indexOf(node[GraphNodeKey.GROUP]) % PALETTE_SIZE}`);
      mark.setAttribute('transform', `translate(${place.x} ${place.y})`);
      const circle = document.createElementNS(SVG, 'circle');
      circle.setAttribute('r', Size.NODE);
      const label = document.createElementNS(SVG, 'text');
      label.setAttribute('y', Size.NODE * 2.2);
      label.textContent = node[GraphNodeKey.LABEL];
      const title = document.createElementNS(SVG, 'title');
      title.textContent = node[GraphNodeKey.TITLE] ?? node[GraphNodeKey.LABEL];
      mark.append(circle, label, title);
      mark.addEventListener('click', () => {
        this.#showDetails(view, node[GraphNodeKey.IDENTIFIER]);
        this.#onSelect(node[GraphNodeKey.IDENTIFIER]);
      });
      marks.append(mark);
      this.#marks.push({ mark, place });
    }
    this.#drawing.replaceChildren(edges, marks);
    this.#places = [...placements.values()];
    if (keepFraming) this.#applyBox();
    else this.#fit(this.#places);
    this.#showLegend(view[GraphViewKey.LEGEND] ?? []);
    this.#details.replaceChildren();
  }

  /**
   * Remove the drawing, the legend and the details.
   */
  clear() {
    this.#drawing.replaceChildren();
    this.#marks = [];
    this.#edges = null;
    this.#legend.replaceChildren();
    this.#details.replaceChildren();
  }

  /**
   * @param {Array<{x: number, y: number}>} places Every node's place.
   */
  /**
   * @param {Document} document The page.
   * @returns {HTMLElement} The fullscreen, zoom and fit buttons.
   */
  #controls(document) {
    const controls = document.createElement('div');
    controls.className = 'graph-controls';
    const button = (control, action) => {
      const element = document.createElement('button');
      element.type = 'button';
      element.textContent = control.TEXT;
      element.title = control.LABEL;
      element.setAttribute('aria-label', control.LABEL);
      element.addEventListener('click', action);
      return element;
    };
    controls.append(
      button(CONTROLS.FULLSCREEN, () => this.element.classList.toggle('graph-fullscreen')),
      button(CONTROLS.ZOOM_IN, () => this.zoom(1 / BUTTON_ZOOM_STEP)),
      button(CONTROLS.ZOOM_OUT, () => this.zoom(BUTTON_ZOOM_STEP)),
      button(CONTROLS.FIT, () => this.#fit(this.#places)),
    );
    return controls;
  }

  /**
   * Zoom around the middle of the drawing.
   * @param {number} factor How much wider the shown region becomes: below one zooms in.
   */
  zoom(factor) {
    const [left, top, width, height] = this.#box;
    this.#box = [left + (width * (1 - factor)) / 2, top + (height * (1 - factor)) / 2, width * factor, height * factor];
    this.#applyBox();
  }

  #fit(places) {
    const xs = places.map((place) => place.x);
    const ys = places.map((place) => place.y);
    const left = Math.min(0, ...xs) - Size.MARGIN;
    const top = Math.min(0, ...ys) - Size.MARGIN;
    this.#box = [left, top, Math.max(0, ...xs) + Size.MARGIN - left, Math.max(0, ...ys) + Size.MARGIN * 1.5 - top];
    this.#applyBox();
  }

  /**
   * Write the view box to the drawing.
   */
  #applyBox() {
    this.#drawing.setAttribute('viewBox', this.#box.join(' '));
    const scale = Math.max(1, this.#box[2] / (this.#drawing.clientWidth || ASSUMED_WIDTH));
    for (const { mark, place } of this.#marks) mark.setAttribute('transform', `translate(${place.x} ${place.y}) scale(${scale})`);
    this.#edges?.setAttribute('stroke-width', String(Size.EDGE * scale));
  }

  /**
   * @param {Array<Object>} legend The legend entries.
   */
  #showLegend(legend) {
    const document = this.element.ownerDocument;
    this.#legend.replaceChildren(
      ...legend.map((entry, position) => {
        const item = document.createElement('li');
        item.className = `series-${position % PALETTE_SIZE}`;
        item.textContent = entry[LegendEntryKey.LABEL];
        return item;
      }),
    );
  }

  /**
   * @param {Object} view The graph view.
   * @param {string} identity The clicked node's identity.
   */
  #showDetails(view, identity) {
    const document = this.element.ownerDocument;
    const detail = view[GraphViewKey.DETAILS]?.[identity];
    if (!detail) {
      this.#details.replaceChildren();
      return;
    }
    const heading = document.createElement('h2');
    heading.textContent = detail[GraphDetailKey.LABEL];
    const lines = (detail[GraphDetailKey.LINES] ?? []).map((text) => {
      const line = document.createElement('p');
      line.textContent = text;
      return line;
    });
    this.#details.replaceChildren(heading, ...lines);
  }

  /**
   * Pan by dragging and zoom by the wheel.
   */
  #addNavigation() {
    let dragging = null;
    this.#drawing.addEventListener('pointerdown', (event) => {
      dragging = { x: event.clientX, y: event.clientY };
    });
    this.#drawing.addEventListener('pointermove', (event) => {
      if (!dragging) return;
      const scale = this.#box[2] / Math.max(this.#drawing.clientWidth, 1);
      this.#box[0] -= (event.clientX - dragging.x) * scale;
      this.#box[1] -= (event.clientY - dragging.y) * scale;
      dragging = { x: event.clientX, y: event.clientY };
      this.#applyBox();
    });
    this.#drawing.addEventListener('pointerup', () => { dragging = null; });
    this.#drawing.addEventListener('wheel', (event) => {
      event.preventDefault();
      this.zoom(event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP);
    });
  }
}
