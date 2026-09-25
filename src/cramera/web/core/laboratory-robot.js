/* Start the fixed native PR2 transfer and display its server-confirmed outcome. */
(function (root, factory) {
  'use strict';
  const laboratory = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = laboratory;
  if (root) root.LaboratoryRobot = laboratory;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  // %% execution vocabulary
  const Routes = Object.freeze({START: '/api/laboratory/pr2/start', STATUS: '/api/laboratory/pr2/status'});
  const States = Object.freeze({IDLE: 'idle', PENDING: 'pending', RUNNING: 'running', SUCCEEDED: 'succeeded', FAILED: 'failed', UNKNOWN: 'unknown'});
  const POLL_INTERVAL = 1500;
  const Live = Object.freeze({
    BRIDGE_URL: 'http://127.0.0.1:8765', SCENE: '__live__', ROBOT: 'pr2', TUBE: 'tube_clear',
    VIEWER_URL: '/index.html?scene=__live__&layout=scene&live=127.0.0.1:8765',
  });
  const Messages = Object.freeze({
    [States.IDLE]: 'PR2 nimmt das klare Glas aus A1 und setzt es in A3.',
    [States.PENDING]: 'PR2-Lauf wird gestartet …',
    [States.RUNNING]: 'CRAM führt den PR2-Lauf aus. Die Aufzeichnung entsteht nach Abschluss.',
    [States.SUCCEEDED]: 'CRAM-Lauf und Zielposition bestätigt. Die Aufzeichnung ist bereit.',
    [States.FAILED]: 'Der PR2-Lauf konnte nicht abgeschlossen werden.',
    [States.UNKNOWN]: 'Serverstatus nicht erreichbar. Der Ausführungszustand wird erneut geprüft.',
  });

  // %% fixed HTTP service
  /** Send only the fixed start command; object poses stay in the native simulation. */
  class Client {
    constructor(request) { this.request = request; }
    async start() {
      return this.read(Routes.START, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    }
    async status() { return this.read(Routes.STATUS, {method: 'GET', cache: 'no-store'}); }
    async info() { return this.read(Live.BRIDGE_URL + '/info', {method: 'GET', cache: 'no-store'}); }
    async prepareLive() { return this.read(Live.BRIDGE_URL + '/live_scene', {method: 'GET', cache: 'no-store'}); }
    async read(url, options) {
      const response = await this.request(url, options);
      const payload = await response.json();
      if (!response.ok && !payload.state) throw new Error(payload.error || 'Serverstatus nicht erreichbar.');
      return payload;
    }
  }

  // %% guarded live-view entry
  /** Wait for this laboratory world to be bundled before attaching its live viewer. */
  class LiveEntry {
    constructor(service, navigate, changed) {
      this.service = service;
      this.navigate = navigate;
      this.changed = changed || (() => {});
      this.message = 'Die PR2-Laborwelt wird vorbereitet …';
      this.polling = false;
      this.finished = false;
    }
    show(message) { this.message = message; this.changed(message); }
    followRecording(answer) {
      if (answer.state !== States.SUCCEEDED || !answer.ok || !answer.recordingUrl) return false;
      this.finished = true;
      this.navigate(answer.recordingUrl);
      return true;
    }
    async refresh() {
      if (this.polling || this.finished) return;
      this.polling = true;
      try {
        const run = await this.service.status();
        if (this.followRecording(run)) return;
        if (run.state !== States.RUNNING) {
          this.show(run.error || 'Es läuft gerade kein PR2-Laborauftrag. Starte ihn in der Laborbank.');
          return;
        }
        const info = await this.service.info();
        if (!info.running || String(info.robot).toLowerCase() !== Live.ROBOT ||
            !Array.isArray(info.objects) || !info.objects.includes(Live.TUBE)) {
          this.show('Die PR2-Laborwelt ist noch nicht bereit. Warte auf den laufenden Laborauftrag …');
          return;
        }
        const bundle = await this.service.prepareLive();
        if (bundle.scene !== Live.SCENE) {
          this.show('Die Live-Ansicht wird vorbereitet …');
          return;
        }
        const current = await this.service.status();
        if (this.followRecording(current)) return;
        if (current.state !== States.RUNNING) {
          this.show(current.error || 'Der PR2-Lauf ist nicht mehr aktiv.');
          return;
        }
        this.finished = true;
        this.navigate(Live.VIEWER_URL);
      } catch (error) {
        this.show('Die Live-Verbindung ist noch nicht erreichbar. Der Status wird erneut geprüft …');
      } finally { this.polling = false; }
    }
  }

  // %% observed execution state
  /** Keep launch requests and server observations separate from manual workbench poses. */
  class Controller {
    constructor(service, changed) {
      this.service = service;
      this.changed = changed || (() => {});
      this.state = States.IDLE;
      this.error = null;
      this.log = '';
      this.liveUrl = null;
      this.recordingUrl = null;
      this.revision = 0;
      this.polling = false;
    }
    busy() { return [States.PENDING, States.RUNNING, States.UNKNOWN].includes(this.state); }
    receive(answer) {
      const states = [States.IDLE, States.RUNNING, States.SUCCEEDED, States.FAILED];
      if (!answer || !states.includes(answer.state)) throw new Error('Ungültiger Serverstatus.');
      this.state = answer.ok === false ? States.FAILED : answer.state;
      this.error = answer.error || null;
      this.log = answer.log || '';
      this.liveUrl = answer.liveUrl || null;
      this.recordingUrl = this.state === States.SUCCEEDED ? answer.recordingUrl || null : null;
      this.changed();
    }
    unavailable() {
      this.state = States.UNKNOWN;
      this.error = Messages[States.UNKNOWN];
      this.recordingUrl = null;
      this.changed();
    }
    async start() {
      if (this.busy()) return;
      this.revision += 1;
      this.state = States.PENDING;
      this.error = null;
      this.log = '';
      this.recordingUrl = null;
      this.changed();
      try { this.receive(await this.service.start()); }
      catch (error) { this.unavailable(); }
    }
    async refresh() {
      if (this.polling || this.state === States.PENDING) return;
      const revision = this.revision;
      this.polling = true;
      try {
        const answer = await this.service.status();
        if (revision === this.revision) this.receive(answer);
      } catch (error) {
        if (revision === this.revision) this.unavailable();
      } finally { this.polling = false; }
    }
  }

  // %% native execution controls
  /** Mount a native robot command with live status, failure output and recorded replay. */
  function mount(host, service, clock) {
    const document = host.ownerDocument;
    const scheduler = clock || document.defaultView;
    let disposed = false;
    let initialized = false;
    function element(tag, parent, className, text) {
      const item = document.createElement(tag);
      item.className = className;
      if (text) item.textContent = text;
      parent.appendChild(item);
      return item;
    }
    const panel = element('section', host, 'laboratory-robot');
    element('strong', panel, 'laboratory-robot-heading', 'Roboter · CRAM');
    element('p', panel, 'laboratory-robot-note', 'Eigener Simulationslauf ab Ausgangszustand · manuelle Änderungen werden nicht übernommen.');
    const button = element('button', panel, 'laboratory-robot-start', 'PR2: A1 → A3');
    button.type = 'button';
    const status = element('p', panel, 'laboratory-robot-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const live = element('a', panel, 'laboratory-robot-live', 'Live-Ansicht öffnen ↗');
    const replay = element('a', panel, 'laboratory-robot-replay', 'Aufzeichnung öffnen ↗');
    for (const link of [live, replay]) {
      link.target = '_blank';
      link.rel = 'noopener';
      link.hidden = true;
    }
    const details = element('details', panel, 'laboratory-robot-details');
    element('summary', details, 'laboratory-robot-log-heading', 'Ausführungsprotokoll');
    const log = element('pre', details, 'laboratory-robot-log');
    const controller = new Controller(service, render);
    function render() {
      if (disposed) return;
      button.disabled = !initialized || controller.busy();
      status.textContent = controller.error || Messages[controller.state];
      status.dataset.error = String([States.FAILED, States.UNKNOWN].includes(controller.state));
      live.hidden = controller.state !== States.RUNNING || !controller.liveUrl;
      replay.hidden = controller.state !== States.SUCCEEDED || !controller.recordingUrl;
      if (controller.liveUrl) live.href = controller.liveUrl;
      if (controller.recordingUrl) replay.href = controller.recordingUrl;
      details.hidden = !controller.log;
      log.textContent = controller.log;
    }
    const start = () => controller.start();
    button.addEventListener('click', start);
    const refresh = async () => {
      await controller.refresh();
      initialized = true;
      render();
    };
    render();
    const initialization = refresh();
    const timer = scheduler.setInterval(refresh, POLL_INTERVAL);
    return {
      controller,
      refresh,
      initialized: initialization,
      destroy() {
        disposed = true;
        scheduler.clearInterval(timer);
        button.removeEventListener('click', start);
        panel.remove();
      },
    };
  }

  return {Client, Controller, LiveEntry, Live, States, Routes, mount};
});
