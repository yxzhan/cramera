/**
 * The Live panel: connect to a running CRAM process, see what it offers, stop, save or
 * discard its recording, and choose the ROS topics and namespaces of its debug markers.
 */

import {
  Capability,
  LiveInfoKey,
  MarkerTopicRequestKey,
  MarkerTopicsKey,
  RecordingSaveRequestKey,
  RecordingState,
  RecordingStatusKey,
  SavedSceneKey,
  SceneDestination,
} from '../protocol.js';
import { DEFAULT_LIVE_ADDRESS } from '../live/live-client.js';
import { SidePanel } from './panel-host.js';

export { DEFAULT_LIVE_ADDRESS };

/**
 * How people read each capability a publisher offers.
 */
export const CAPABILITY_LABELS = Object.freeze({
  [Capability.PLAN]: 'the running plan',
  [Capability.STATECHART]: 'the motion statechart',
  [Capability.TRANSFORMS]: 'frames',
  [Capability.MARKERS]: 'markers',
  [Capability.QUESTIONS]: 'questions',
  [Capability.RECORDING]: 'recording',
  [Capability.TELEOPERATION]: 'teleoperation',
  [Capability.CONSTRAINTS]: 'constraints',
  [Capability.OBJECT_PLACEMENT]: 'moving objects',
  [Capability.ROBOT_SELECTION]: 'choosing the robot',
  [Capability.DETECTED_EVENTS]: 'segmind’s events',
});

/**
 * @param {Array<string>} capabilities The `Capability` values a publisher offers.
 * @returns {string} What it offers, in words, or 'nothing'.
 */
export function offeredText(capabilities) {
  return capabilities.map((capability) => CAPABILITY_LABELS[capability] ?? capability).join(', ') || 'nothing';
}

/**
 * @param {Object} status A recording's status.
 * @returns {boolean} Whether the ended recording's bundle is written, so that a save
 *   takes this run and not an older bundle.
 */
export function isWritten(status) {
  return Boolean(status[RecordingStatusKey.SCENE_NAME]);
}

/**
 * @param {Object} status A recording's status.
 * @returns {string} What the recording is doing, in a sentence.
 */
export function recordingText(status) {
  const frames = status[RecordingStatusKey.FRAME_COUNT] ?? 0;
  const size = `${frames} ${frames === 1 ? 'frame' : 'frames'}, ${(status[RecordingStatusKey.DURATION_SECONDS] ?? 0).toFixed(1)} s`;
  const state = status[RecordingStatusKey.STATE];
  if (state === RecordingState.RECORDING) return `Recording the run: ${size}.`;
  if (state === RecordingState.FINALIZED && !isWritten(status)) return `The recording ended (${size}); writing it down, then it can be saved.`;
  if (state === RecordingState.FINALIZED) return `The recording ended (${size}); save it under a name to replay it, or discard it.`;
  return 'Not recording.';
}

// %% badge

/**
 * What the badge over the 3D view says about the live publisher.
 */
export const LiveBadgeState = Object.freeze({
  OFF: 'off',
  CONNECTING: 'connecting',
  LIVE: 'live',
  LOST: 'lost',
});

/**
 * The badge over the 3D view: whether the view follows a live run, waits for one, or
 * shows the last state of one that ended; clicking it opens the Live panel.
 */
export class LiveBadge {
  /**
   * The badge.
   * @type {HTMLElement}
   */
  element;

  /**
   * @param {HTMLElement} element The badge element.
   * @param {function(): void} onOpen Opens the Live panel.
   */
  constructor(element, onOpen) {
    this.element = element;
    element.addEventListener('click', () => onOpen());
    this.show(LiveBadgeState.OFF);
  }

  /**
   * @param {string} state A `LiveBadgeState` value.
   * @param {string=} address The publisher's address, shown while connecting.
   */
  show(state, address = '') {
    this.element.dataset.state = state;
    this.element.hidden = state === LiveBadgeState.OFF;
    this.element.textContent = LiveBadge.text(state, address);
    this.element.title = state === LiveBadgeState.OFF ? '' : 'Open the Live panel';
  }

  /**
   * @param {string} state A `LiveBadgeState` value.
   * @param {string} address The publisher's address.
   * @returns {string} What the badge says.
   */
  static text(state, address) {
    if (state === LiveBadgeState.CONNECTING) return `◌ Waiting for ${address.replace(/^https?:\/\//, '') || 'the live publisher'}…`;
    if (state === LiveBadgeState.LIVE) return '● LIVE';
    if (state === LiveBadgeState.LOST) return '■ Live run ended · showing its last state';
    return '';
  }
}

// %% actions

/**
 * What the panel's controls do.
 */
export class LiveActions {
  /**
   * @param {function(string): void} connect Connects to a publisher address.
   * @param {function(): void} disconnect Ends the connection.
   * @param {function(): Promise<Object>} stopRecording Ends the recording.
   * @param {function(Object): Promise<Object>} saveRecording Saves the ended recording.
   * @param {function(): Promise<Object>} discardRecording Throws the ended recording away.
   * @param {function(Object): Promise<Object>} changeMarkerTopic Starts or stops reading
   *   a marker topic, answering with the topics read and on offer.
   * @param {function(string, string, boolean): void} showMarkerNamespace Draws or hides
   *   the markers of a topic's namespace.
   */
  constructor(connect, disconnect, stopRecording, saveRecording, discardRecording, changeMarkerTopic = async () => null, showMarkerNamespace = () => {}) {
    /**
     * Connects to a publisher address.
     * @type {function(string): void}
     */
    this.connect = connect;
    /**
     * Ends the connection.
     * @type {function(): void}
     */
    this.disconnect = disconnect;
    /**
     * Ends the recording.
     * @type {function(): Promise<Object>}
     */
    this.stopRecording = stopRecording;
    /**
     * Saves the ended recording.
     * @type {function(Object): Promise<Object>}
     */
    this.saveRecording = saveRecording;
    /**
     * Throws the ended recording away.
     * @type {function(): Promise<Object>}
     */
    this.discardRecording = discardRecording;
    /**
     * Starts or stops reading a marker topic.
     * @type {function(Object): Promise<Object>}
     */
    this.changeMarkerTopic = changeMarkerTopic;
    /**
     * Draws or hides the markers of a topic's namespace.
     * @type {function(string, string, boolean): void}
     */
    this.showMarkerNamespace = showMarkerNamespace;
  }
}

// %% marker controls

/**
 * What the markers section says when it has no namespace to list.
 */
export const MarkerNote = Object.freeze({
  NO_ROS: 'This process reads no ROS markers: it runs without ROS.',
  NO_TOPIC: 'Read a topic to draw its markers.',
  NO_MARKERS: 'No markers on the topics read yet.',
});

// %% panel

export class LivePanel {
  /**
   * The panel.
   * @type {SidePanel}
   */
  panel;

  /**
   * What the controls do.
   * @type {LiveActions}
   */
  #actions;

  /**
   * The publisher's address.
   * @type {HTMLInputElement}
   */
  #address;

  /**
   * Connects or disconnects.
   * @type {HTMLButtonElement}
   */
  #connection;

  /**
   * The connection's state.
   * @type {HTMLElement}
   */
  #state;

  /**
   * The recording's controls.
   * @type {HTMLElement}
   */
  #recording;

  /**
   * The outcome of the last save, kept while the recording's controls change.
   * @type {HTMLElement}
   */
  #notice;

  /**
   * Whether a connection is wanted.
   * @type {boolean}
   */
  #connecting = false;

  /**
   * The debug markers' controls, shown while the publisher offers markers.
   * @type {HTMLElement}
   */
  #markers;

  /**
   * A checkbox per marker topic on offer.
   * @type {HTMLElement}
   */
  #topics;

  /**
   * A checkbox per namespace of the markers drawn.
   * @type {HTMLElement}
   */
  #namespaces;

  /**
   * Says why no namespace is listed.
   * @type {HTMLElement}
   */
  #markerNote;

  /**
   * The marker topics last read, or null before any.
   * @type {Object|null}
   */
  #topicsShown = null;

  /**
   * The namespaces listed; the lists are only rebuilt when they change, so that markers
   * that only moved leave a box being clicked in place.
   * @type {string|null}
   */
  #namespacesListed = null;

  /**
   * @param {Document} document The page.
   * @param {LiveActions} actions What the controls do.
   */
  constructor(document, actions) {
    this.#actions = actions;
    this.panel = new SidePanel('live', 'Live', document.createElement('section'));
    this.panel.element.classList.add('live');
    const form = document.createElement('form');
    form.className = 'connect';
    this.#address = document.createElement('input');
    this.#address.type = 'url';
    this.#address.value = DEFAULT_LIVE_ADDRESS;
    this.#address.setAttribute('aria-label', 'Live publisher address');
    this.#connection = document.createElement('button');
    form.append(this.#address, this.#connection);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (this.#connecting) this.#actions.disconnect();
      else this.#actions.connect(this.#address.value);
    });
    this.#state = document.createElement('p');
    this.#state.className = 'live-state';
    this.#recording = document.createElement('div');
    this.#recording.className = 'recording';
    this.#notice = document.createElement('p');
    this.#notice.className = 'notice';
    this.#markers = this.#markerControls();
    this.panel.element.append(form, this.#state, this.#recording, this.#notice, this.#markers);
    this.showConnecting(false, null);
  }

  /**
   * @returns {HTMLElement} The section choosing the marker topics and namespaces.
   */
  #markerControls() {
    const document = this.panel.element.ownerDocument;
    const section = document.createElement('section');
    section.className = 'markers';
    section.setAttribute('aria-label', 'ROS markers');
    const title = document.createElement('h3');
    title.textContent = 'ROS markers';
    this.#topics = document.createElement('div');
    this.#topics.className = 'marker-topics';
    this.#topics.setAttribute('aria-label', 'Topics');
    this.#namespaces = document.createElement('div');
    this.#namespaces.className = 'marker-namespaces';
    this.#namespaces.setAttribute('aria-label', 'Namespaces');
    this.#markerNote = document.createElement('p');
    this.#markerNote.className = 'marker-note';
    section.append(title, this.#topics, this.#namespaces, this.#markerNote);
    return section;
  }

  /**
   * List the marker topics, each with whether it is read.
   * @param {Object} topics The topics read and on offer.
   */
  showMarkerTopics(topics) {
    if (JSON.stringify(topics) === JSON.stringify(this.#topicsShown)) return;
    this.#topicsShown = topics;
    const subscribed = new Set(topics[MarkerTopicsKey.SUBSCRIBED] ?? []);
    const available = topics[MarkerTopicsKey.ROS] ? topics[MarkerTopicsKey.AVAILABLE] ?? [] : [];
    this.#topics.replaceChildren(
      ...available.map((topic) =>
        this.#checkbox(topic, subscribed.has(topic), (checked) =>
          this.#actions.changeMarkerTopic({ [MarkerTopicRequestKey.TOPIC]: topic, [MarkerTopicRequestKey.SUBSCRIBED]: checked }).then(
            (changed) => { if (changed) this.showMarkerTopics(changed); },
            (error) => {
              this.#topicsShown = null;
              this.#markerNote.textContent = error.message;
            },
          ),
        ),
      ),
    );
    this.#noteMarkers();
  }

  /**
   * List the namespaces of the markers drawn, each with how many it has and whether
   * they are drawn.
   * @param {Array<import('../scene/debug-markers.js').MarkerNamespace>} namespaces The
   *   namespaces.
   */
  showMarkerNamespaces(namespaces) {
    const listed = JSON.stringify(namespaces);
    if (listed === this.#namespacesListed) return;
    this.#namespacesListed = listed;
    const topics = new Set(namespaces.map(({ topic }) => topic));
    this.#namespaces.replaceChildren(
      ...namespaces.map(({ topic, namespace, count, shown }) => {
        const name = namespace || '(no namespace)';
        const label = `${topics.size > 1 ? `${topic} · ` : ''}${name} (${count})`;
        return this.#checkbox(label, shown, (checked) => this.#actions.showMarkerNamespace(topic, namespace, checked));
      }),
    );
    this.#noteMarkers();
  }

  /**
   * Say why no namespace is listed, or nothing when one is.
   */
  #noteMarkers() {
    const topics = this.#topicsShown;
    if (this.#namespaces.children.length) this.#markerNote.textContent = '';
    else if (topics && !topics[MarkerTopicsKey.ROS]) this.#markerNote.textContent = MarkerNote.NO_ROS;
    else if (topics && !(topics[MarkerTopicsKey.SUBSCRIBED] ?? []).length) this.#markerNote.textContent = MarkerNote.NO_TOPIC;
    else this.#markerNote.textContent = MarkerNote.NO_MARKERS;
  }

  /**
   * @param {string} text What the checkbox switches.
   * @param {boolean} checked Whether it is on.
   * @param {function(boolean): void} change Called with its new state.
   * @returns {HTMLElement} The labelled checkbox.
   */
  #checkbox(text, checked, change) {
    const document = this.panel.element.ownerDocument;
    const label = document.createElement('label');
    label.className = 'switch';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked;
    input.addEventListener('change', () => change(input.checked));
    const caption = document.createElement('span');
    caption.textContent = text;
    label.append(input, caption);
    return label;
  }

  /**
   * @param {boolean} connecting Whether a connection is wanted.
   * @param {Object|null} info The publisher's information, or null while it does not answer.
   * @param {string=} address The address connected to.
   */
  showConnecting(connecting, info, address) {
    this.#connecting = connecting;
    if (address) this.#address.value = address;
    this.#connection.textContent = connecting ? 'Disconnect' : 'Connect';
    this.#address.disabled = connecting;
    if (!connecting) this.#state.textContent = 'Not connected. Start a demo with cramera-live, or a plan in the Plan Builder, then connect.';
    else if (!info) this.#state.textContent = 'Waiting for the publisher to answer. A run that ended stays shown as it was last.';
    else this.#state.textContent = `Connected to ${info[LiveInfoKey.PUBLISHER]}; it offers ${offeredText(info[LiveInfoKey.CAPABILITIES] ?? [])}.`;
    this.panel.element.dataset.connected = String(Boolean(connecting && info));
    if (!info || !(info[LiveInfoKey.CAPABILITIES] ?? []).includes(Capability.RECORDING)) this.#recording.replaceChildren();
    this.#markers.hidden = !(connecting && (info?.[LiveInfoKey.CAPABILITIES] ?? []).includes(Capability.MARKERS));
  }

  /**
   * @param {Object} status The recording's status.
   */
  showRecording(status) {
    const document = this.panel.element.ownerDocument;
    const state = status[RecordingStatusKey.STATE];
    const summary = document.createElement('p');
    summary.textContent = recordingText(status);
    const controls = [summary];
    if (state === RecordingState.RECORDING) controls.push(this.#button('Stop recording', () => this.#actions.stopRecording()));
    if (state === RecordingState.FINALIZED && isWritten(status)) controls.push(this.#saveForm(), this.#button('Discard', () => this.#actions.discardRecording()));
    this.#recording.replaceChildren(...controls);
  }

  /**
   * @returns {HTMLElement} The form saving the ended recording under a name.
   */
  #saveForm() {
    const document = this.panel.element.ownerDocument;
    const form = document.createElement('form');
    form.className = 'save';
    const name = document.createElement('input');
    name.placeholder = 'scene_name';
    name.setAttribute('aria-label', 'Scene name');
    const destination = document.createElement('select');
    destination.setAttribute('aria-label', 'Save to');
    for (const value of Object.values(SceneDestination)) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      destination.append(option);
    }
    const save = document.createElement('button');
    save.textContent = 'Save';
    form.append(name, destination, save);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      const request = { [RecordingSaveRequestKey.NAME]: name.value, [RecordingSaveRequestKey.DESTINATION]: destination.value };
      this.#actions.saveRecording(request).then(
        (saved) => { this.#notice.textContent = `Saved as ${saved[SavedSceneKey.SCENE]}; choose it in the scene picker.`; },
        (error) => { this.#notice.textContent = error.message; },
      );
    });
    return form;
  }

  /**
   * @param {string} text The button's text.
   * @param {function(): void} action What it does.
   * @returns {HTMLButtonElement} The button.
   */
  #button(text, action) {
    const button = this.panel.element.ownerDocument.createElement('button');
    button.type = 'button';
    button.textContent = text;
    button.addEventListener('click', action);
    return button;
  }
}
