/* Force-driven laboratory manipulation with authoritative simulation feedback. */
(function (root, factory) {
  'use strict';
  const laboratory = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = laboratory;
  if (root) root.LaboratoryPhysics = laboratory;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  // %% simulation service
  const Routes = Object.freeze({
    START: '/api/laboratory/physics/start', STATE: '/api/laboratory/physics/state',
    TARGET: '/api/laboratory/physics/target', RELEASE: '/api/laboratory/physics/release',
    RESET: '/api/laboratory/physics/reset', STOP: '/api/laboratory/physics/stop',
    LIQUID: '/api/laboratory/physics/liquid',
    TARE: '/api/laboratory/physics/scale/tare',
  });
  const RobotRoutes = Object.freeze({
    START: '/api/laboratory/physics/robot/start', STATE: '/api/laboratory/physics/robot/state',
    TARGET: '/api/laboratory/physics/robot/target', RELEASE: '/api/laboratory/physics/robot/release',
    RESET: '/api/laboratory/physics/robot/reset', STOP: '/api/laboratory/physics/robot/stop',
    RUN: '/api/laboratory/physics/robot/run', PAUSE: '/api/laboratory/physics/robot/pause',
    MIX: '/api/laboratory/physics/robot/mix',
    LIQUID: '/api/laboratory/physics/robot/liquid',
    TARE: '/api/laboratory/physics/robot/scale/tare',
  });
  const RobotStates = Object.freeze({IDLE: 'idle', RUNNING: 'running', PAUSED: 'paused', SUCCEEDED: 'succeeded', FAILED: 'failed'});
  const RobotSources = Object.freeze({TRANSFER: 'recorded_cram_joint_targets', MIXING: 'laboratory_liquid_mixing'});
  const RobotLabels = Object.freeze({idle: 'Bereit', running: 'PR2 bewegt das Glas', paused: 'PR2 pausiert', succeeded: 'Glas umgesetzt', failed: 'Bewegung angehalten'});
  const MixingLabels = Object.freeze({...RobotLabels, running: 'PR2 mischt die Flüssigkeiten', succeeded: 'Mischung in A2 abgelegt'});
  const RobotPhases = Object.freeze({approach: 'Glas anfahren', grasp: 'Kontaktgriff prüfen', lift: 'Glas anheben', transport: 'Glas transportieren', pour: 'Flüssigkeit eingießen', return: 'Spenderglas zurückstellen', swirl: 'Mischung sanft schwenken', place: 'Glas im Ständer abstellen', release: 'Glas absetzen', settle: 'Stabilen Stand prüfen', complete: 'Abgeschlossen'});
  const POLL_INTERVAL = 33;
  const RETRY_INTERVAL = 1500;
  const MOVE_STEP = .01;
  const TILT_STEP = 15;
  const LIFT_CLEARANCE = .22;
  const VOLUME_STEP = .5;
  const GlassCamera = Object.freeze({POSITION: [.32, -.42, .28], TARGET: [0, 0, .075]});
  const ProgramCommands = Object.freeze(['run', 'runMixing']);
  const LiquidCommands = Object.freeze(['fillLiquid', 'reset', ...ProgramCommands]);
  const ScaleCommands = Object.freeze(['tareScale', 'reset', ...ProgramCommands]);
  const SCALE_MOVE_TIMEOUT = 20000;
  const SCALE_MOVE_TOLERANCE = .006;
  const SCALE_SEATING_DEPTH = .0002;
  const SCALE_ROTATION_TOLERANCE = .05;
  const SCALE_SETTLING_SPEED = .003;
  const SCALE_SETTLING_ANGULAR_SPEED = .03;
  const SCALE_SEATING_ANGLE = Math.PI / 45;
  const STOPPER_CAP_RADIUS = .0105;

  /** Exchange desired targets and observed simulation states with the local service. */
  class Client {
    constructor(request, config) {
      this.request = request;
      this.robot = Boolean(config && config.robot);
      this.routes = this.robot ? RobotRoutes : Routes;
    }
    async read(url, options) {
      const response = await this.request(url, options);
      const answer = await response.json();
      if (!response.ok || answer.ok === false) throw new Error(answer.error || 'Die Simulation ist nicht erreichbar.');
      return answer;
    }
    post(url, body) {
      return this.read(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
    }
    start() { return this.post(this.routes.START); }
    state() { return this.read(this.routes.STATE, {method: 'GET', cache: 'no-store'}); }
    target(target) { return this.post(this.routes.TARGET, target); }
    release() { return this.post(this.routes.RELEASE); }
    reset() { return this.post(this.routes.RESET); }
    stop() { return this.post(this.routes.STOP); }
    run() { return this.post(this.routes.RUN); }
    runMixing() { return this.post(this.routes.MIX); }
    pause() { return this.post(this.routes.PAUSE); }
    fillLiquid(key, volumeMl) { return this.post(this.routes.LIQUID, {key, volumeMl}); }
    tareScale() { return this.post(this.routes.TARE); }
  }

  // %% target commands and observed contacts
  /** Serialize targets without letting local interaction replace physical object poses. */
  class Controller {
    constructor(config, service, adapter, changed) {
      this.config = config;
      this.service = service;
      this.adapter = adapter;
      this.changed = changed || (() => {});
      this.objects = {};
      this.contacts = [];
      this.liquid = null;
      this.scale = null;
      this.scaleRevision = 0;
      this.scaleTransfer = null;
      this.scaleTransferError = null;
      this.simulationTime = null;
      this.target = null;
      this.robot = {state: RobotStates.IDLE, phase: '', progress: 0};
      this.robotRequest = null;
      this.selected = config.objects.length ? config.objects[0].key : null;
      this.dragged = null;
      this.state = 'idle';
      this.starting = false;
      this.runRevision = 0;
      this.error = null;
      this.revision = 0;
      this.liquidRevision = 0;
      this.queue = [];
      this.activeCommand = null;
      this.draining = null;
      this.polling = false;
      this.disposed = false;
      this.nextPoll = 0;
    }
    ready() { return !this.disposed && !this.starting && this.state === 'running'; }
    robotBusy() { return this.config.robot && (this.robotRequest || this.robot.state === RobotStates.RUNNING); }
    manualReady() { return this.ready() && !this.robotBusy(); }
    liquidBusy() { return LiquidCommands.includes(this.activeCommand) || this.queue.some(command => LiquidCommands.includes(command.type)); }
    scaleBusy() { return ScaleCommands.includes(this.activeCommand) || this.queue.some(command => ScaleCommands.includes(command.type)); }
    tareScale() {
      if (!this.manualReady() || !this.scale || !this.scale.available || !this.scale.stable || this.scaleBusy()) return Promise.resolve(false);
      this.scaleRevision += 1;
      return this.enqueue({type: 'tareScale'});
    }
    async start() {
      if (this.disposed || this.starting || this.ready()) return false;
      this.starting = true;
      this.runRevision += 1;
      this.liquidRevision += 1;
      this.scaleRevision += 1;
      this.target = null;
      this.dragged = null;
      this.liquid = null;
      this.scale = null;
      this.scaleTransfer = null;
      this.scaleTransferError = null;
      this.queue = [];
      this.error = null;
      this.changed();
      try {
        if (this.draining) await this.draining;
        const answer = await this.service.start();
        if (this.disposed) return false;
        this.state = answer.state;
        this.starting = false;
        this.nextPoll = 0;
        await this.refresh();
        return this.ready();
      } catch (error) {
        this.state = 'failed';
        this.error = error.message;
        this.nextPoll = Date.now() + RETRY_INTERVAL;
        return false;
      } finally {
        this.starting = false;
        if (!this.disposed) this.changed();
      }
    }
    known(key) { return this.config.objects.some(object => object.key === key); }
    bounded(position) {
      if (!Array.isArray(position) || position.length !== 3 || !position.every(Number.isFinite)) return null;
      return position.map((value, axis) => Math.min(this.config.bounds.max[axis], Math.max(this.config.bounds.min[axis], value)));
    }
    setTarget(key, position, orientation) {
      this.scaleTransfer = null;
      this.scaleTransferError = null;
      return this.targetCommand(key, position, orientation);
    }
    targetCommand(key, position, orientation) {
      const bounded = this.bounded(position);
      if (!this.manualReady() || !this.known(key) || !bounded) return Promise.resolve(false);
      const rotation = orientation || (this.target && this.target.key === key && this.target.orientation);
      if (rotation && (!Array.isArray(rotation) || rotation.length !== 4 || !rotation.every(Number.isFinite) || Math.hypot(...rotation) === 0)) return Promise.resolve(false);
      this.selected = key;
      this.target = {key, position: bounded};
      if (rotation) this.target.orientation = rotation.slice();
      this.revision += 1;
      this.changed();
      return this.enqueue({type: 'target', target: {...this.target, position: bounded.slice()}});
    }
    moveToScale() {
      const pose = this.objects[this.selected];
      const scale = this.config.scale;
      if (!this.manualReady() || !pose || !scale || !scale.panPosition || this.scaleTransfer) return Promise.resolve(false);
      const pan = scale.panPosition.slice();
      if (scale.holder && !this.selectedLiquid() && scale.holder.innerRadius < STOPPER_CAP_RADIUS) {
        pan[0] += (scale.panRadius + scale.holder.outerRadius) / 2;
      }
      const elevation = Math.max(pose[2] + LIFT_CLEARANCE, pan[2] + LIFT_CLEARANCE);
      if (elevation > this.config.bounds.max[2]) return Promise.resolve(false);
      const upright = [0, 0, 0, 1];
      const seated = scale.holder && this.selectedLiquid()
        ? [0, Math.sin(SCALE_SEATING_ANGLE / 2), 0, Math.cos(SCALE_SEATING_ANGLE / 2)] : upright;
      this.scaleTransferError = null;
      this.scaleTransfer = {key: this.selected, index: 0, startedAt: Date.now(), previousPose: pose.slice(), previousTime: this.simulationTime, waypoints: [
        {position: [pose[0], pose[1], elevation], orientation: pose.slice(3)},
        {position: [pose[0], pose[1], elevation], orientation: upright},
        {position: [pan[0], pan[1], elevation], orientation: upright},
        {position: [pan[0], pan[1], pan[2] - SCALE_SEATING_DEPTH], orientation: seated},
      ]};
      const target = this.scaleTransfer.waypoints[0];
      return this.targetCommand(this.selected, target.position, target.orientation);
    }
    async advanceScaleTransfer() {
      const transfer = this.scaleTransfer;
      if (!transfer) return;
      if (!this.manualReady() || this.error) { this.scaleTransfer = null; return; }
      const observed = this.objects[transfer.key];
      if (!observed) { this.scaleTransfer = null; return; }
      if (Date.now() - transfer.startedAt > SCALE_MOVE_TIMEOUT) {
        this.scaleTransfer = null;
        this.scaleTransferError = 'Weg zur Waage blockiert. Das Glas bleibt gehalten; manuell weiterbewegen oder loslassen.';
        this.error = this.scaleTransferError;
        return;
      }
      const target = transfer.waypoints[transfer.index];
      const elapsed = this.simulationTime === null || transfer.previousTime === null ? null : this.simulationTime - transfer.previousTime;
      const travel = Math.hypot(...observed.slice(0, 3).map((value, axis) => value - transfer.previousPose[axis]));
      const previousRotation = transfer.previousPose.slice(3);
      const currentRotation = observed.slice(3);
      const rotationAgreement = Math.abs(previousRotation.reduce((sum, value, axis) => sum + value * currentRotation[axis], 0))
        / (Math.hypot(...previousRotation) * Math.hypot(...currentRotation));
      const rotationTravel = 2 * Math.acos(Math.min(1, rotationAgreement));
      transfer.previousPose = observed.slice();
      transfer.previousTime = this.simulationTime;
      if (elapsed !== null && (!(elapsed > 0) || travel / elapsed > SCALE_SETTLING_SPEED || rotationTravel / elapsed > SCALE_SETTLING_ANGULAR_SPEED)) return;
      const distance = Math.hypot(...target.position.map((value, axis) => value - observed[axis]));
      const observedRotation = observed.slice(3);
      const alignment = Math.abs(target.orientation.reduce((sum, value, axis) => sum + value * observedRotation[axis], 0))
        / (Math.hypot(...target.orientation) * Math.hypot(...observedRotation));
      if (distance > SCALE_MOVE_TOLERANCE || alignment < Math.cos(SCALE_ROTATION_TOLERANCE / 2)) return;
      if (transfer.index === transfer.waypoints.length - 1 && this.config.scale.holder
        && !(this.scale && this.scale.available && this.scale.objects.includes(transfer.key) && this.scale.forceNewtons > 0)) return;
      transfer.index += 1;
      transfer.startedAt = Date.now();
      if (transfer.index === transfer.waypoints.length) {
        this.scaleTransfer = null;
        await this.release();
        return;
      }
      const next = transfer.waypoints[transfer.index];
      await this.targetCommand(transfer.key, next.position, next.orientation);
    }
    hold(key) {
      const pose = this.objects[key];
      return pose ? this.setTarget(key, pose.slice(0, 3)) : Promise.resolve(false);
    }
    beginDrag(key) {
      if (!this.manualReady() || !this.known(key) || !this.objects[key]) return false;
      this.dragged = key;
      this.hold(key);
      return true;
    }
    dragTo(position) {
      if (!this.dragged || !this.target || this.target.key !== this.dragged) return Promise.resolve(false);
      return this.setTarget(this.dragged, [position[0], position[1], this.target.position[2]]);
    }
    nudge(delta) {
      const pose = this.target && this.target.key === this.selected ? this.target.position : this.objects[this.selected];
      if (!pose) return Promise.resolve(false);
      return this.setTarget(this.selected, delta.map((value, axis) => pose[axis] + value));
    }
    selectedLiquid() { return this.liquid && this.liquid.tubes && this.liquid.tubes[this.selected]; }
    tilt(degrees) {
      if (!this.selectedLiquid() || !Number.isFinite(degrees)) return Promise.resolve(false);
      const pose = this.objects[this.selected];
      if (!pose) return Promise.resolve(false);
      const target = this.target && this.target.key === this.selected ? this.target : null;
      const [x, y, z, w] = target && target.orientation ? target.orientation : pose.slice(3);
      const angle = degrees * Math.PI / 360;
      const sine = Math.sin(angle), cosine = Math.cos(angle);
      const orientation = [cosine * x + sine * z, cosine * y + sine * w, cosine * z - sine * x, cosine * w - sine * y];
      return this.setTarget(this.selected, target ? target.position : pose.slice(0, 3), orientation);
    }
    upright() {
      if (!this.selectedLiquid()) return Promise.resolve(false);
      const pose = this.target && this.target.key === this.selected ? this.target.position : this.objects[this.selected];
      return pose ? this.setTarget(this.selected, pose.slice(0, 3), [0, 0, 0, 1]) : Promise.resolve(false);
    }
    fillLiquid(volumeMl) {
      const tube = this.selectedLiquid();
      if (!this.manualReady() || !tube || !Number.isFinite(volumeMl) || volumeMl < 0 || volumeMl > tube.capacityMl) return Promise.resolve(false);
      this.revision += 1;
      this.liquidRevision += 1;
      return this.enqueue({type: 'fillLiquid', key: this.selected, volumeMl, revision: this.revision});
    }
    observedTilt() {
      const pose = this.objects[this.selected];
      if (!pose) return 0;
      const [x, y, z, w] = pose.slice(3);
      const length = x * x + y * y + z * z + w * w;
      return length ? Math.acos(Math.max(-1, Math.min(1, 1 - 2 * (x * x + y * y) / length))) * 180 / Math.PI : 0;
    }
    viewGlass() {
      const pose = this.objects[this.selected];
      if (!pose || !this.adapter.focus) return false;
      this.adapter.focus(
        GlassCamera.POSITION.map((offset, axis) => pose[axis] + offset),
        GlassCamera.TARGET.map((offset, axis) => pose[axis] + offset),
      );
      return true;
    }
    release() { return this.clear('release'); }
    reset() { return this.clear('reset'); }
    run() { return this.startProgram('run'); }
    runMixing() { return this.startProgram('runMixing'); }
    startProgram(type) {
      if (!this.config.robot || !this.ready() || this.robotBusy()) return Promise.resolve(false);
      this.robotRequest = type;
      return this.clear(type);
    }
    pause() {
      if (!this.config.robot || !this.ready() || !this.robotBusy() || this.robotRequest === 'pause') return Promise.resolve(false);
      this.robotRequest = 'pause';
      return this.clear('pause');
    }
    clear(type) {
      this.scaleTransfer = null;
      this.scaleTransferError = null;
      this.dragged = null;
      this.target = null;
      this.revision += 1;
      if (LiquidCommands.includes(type)) this.liquidRevision += 1;
      if (ScaleCommands.includes(type)) this.scaleRevision += 1;
      this.queue = this.queue.filter(command => command.type !== 'target');
      this.changed();
      return this.enqueue({type});
    }
    enqueue(command) {
      if (this.disposed) return Promise.resolve(false);
      if (command.type === 'target' && this.queue.length && this.queue[this.queue.length - 1].type === 'target') {
        this.queue[this.queue.length - 1] = command;
      } else this.queue.push(command);
      if (!this.draining) this.draining = this.drain();
      return this.draining;
    }
    async drain() {
      while (this.queue.length && !this.disposed) {
        const command = this.queue.shift();
        this.activeCommand = command.type;
        try {
          let answer;
          if (command.type === 'target') answer = await this.service.target(command.target);
          else if (command.type === 'fillLiquid') answer = await this.service.fillLiquid(command.key, command.volumeMl);
          else answer = await this.service[command.type]();
          if (command.type === 'tareScale' && !this.disposed && answer.scale) {
            this.scale = answer.scale;
            if (this.adapter.setScaleState) this.adapter.setScaleState(this.scale);
          }
          if (command.type === 'fillLiquid' && !this.disposed && command.revision === this.revision && answer.liquid) {
            if (answer.objects) {
              this.objects = answer.objects;
              for (const [key, pose] of Object.entries(this.objects)) this.adapter.setObjectPose(key, pose);
            }
            this.liquid = answer.liquid;
            if (this.adapter.setLiquidState) this.adapter.setLiquidState(this.liquid);
          }
          if (this.config.robot && [...ProgramCommands, 'pause', 'reset'].includes(command.type)) {
            const startingProgram = ProgramCommands.includes(command.type);
            const state = startingProgram ? RobotStates.RUNNING : (command.type === 'pause' ? RobotStates.PAUSED : RobotStates.IDLE);
            const source = startingProgram ? (command.type === 'runMixing' ? RobotSources.MIXING : RobotSources.TRANSFER) : this.robot.source;
            this.robot = answer.robot || {...this.robot, state, source};
          }
          this.error = null;
        } catch (error) {
          this.error = error.message;
          this.target = null;
          this.dragged = null;
          this.scaleTransfer = null;
          this.queue = this.queue.filter(pending => pending.type !== 'target');
        }
        if (LiquidCommands.includes(command.type)) this.liquidRevision += 1;
        if (ScaleCommands.includes(command.type)) this.scaleRevision += 1;
        this.activeCommand = null;
        if (this.robotRequest === command.type) this.robotRequest = null;
        this.changed();
      }
      this.draining = null;
      return !this.error;
    }
    async refresh() {
      if (this.polling || this.disposed || this.starting || Date.now() < this.nextPoll) return;
      const revision = this.revision;
      const runRevision = this.runRevision;
      const commandPending = Boolean(this.draining);
      const liquidRevision = this.liquidRevision;
      const liquidPending = this.liquidBusy();
      const scaleRevision = this.scaleRevision;
      const scalePending = this.scaleBusy();
      this.polling = true;
      try {
        const answer = await this.service.state();
        if (this.disposed || runRevision !== this.runRevision) return;
        this.state = answer.state;
        this.simulationTime = Number.isFinite(answer.time) ? answer.time : null;
        this.objects = answer.objects || {};
        this.contacts = answer.contacts || [];
        if (!commandPending && !this.draining && revision === this.revision) {
          this.target = answer.target || null;
          this.robot = answer.robot || this.robot;
        }
        if (answer.frames && this.adapter.setJointFrames) this.adapter.setJointFrames(answer.frames);
        for (const [key, pose] of Object.entries(this.objects)) this.adapter.setObjectPose(key, pose);
        if (!liquidPending && !this.liquidBusy() && liquidRevision === this.liquidRevision) {
          this.liquid = answer.liquid || null;
          if (this.liquid && this.adapter.setLiquidState) this.adapter.setLiquidState(this.liquid);
        }
        if (this.adapter.showContacts) this.adapter.showContacts(this.contacts, this.target);
        if (!scalePending && !this.scaleBusy() && scaleRevision === this.scaleRevision) {
          this.scale = answer.scale || null;
          if (this.adapter.setScaleState) this.adapter.setScaleState(this.scale);
        }
        this.error = answer.error || this.scaleTransferError || null;
        await this.advanceScaleTransfer();
        this.nextPoll = 0;
      } catch (error) {
        if (runRevision !== this.runRevision) return;
        this.state = 'failed';
        this.scale = null;
        if (this.adapter.setScaleState) this.adapter.setScaleState(null);
        this.error = error.message;
        this.nextPoll = Date.now() + RETRY_INTERVAL;
      } finally {
        this.polling = false;
        if (!this.disposed) this.changed();
      }
    }
    feedback() {
      const relevant = this.contacts.filter(contact => contact.bodyA === this.selected || contact.bodyB === this.selected);
      const force = relevant.reduce((sum, contact) => sum + Math.max(0, Number(contact.force) || 0), 0);
      const pose = this.target && this.objects[this.target.key];
      const distance = pose ? Math.hypot(...this.target.position.map((value, axis) => value - pose[axis])) : 0;
      return {contactCount: relevant.length, force, distance};
    }
    destroy() { this.disposed = true; this.queue = []; this.scaleTransfer = null; }
  }

  // %% compact manipulation controls
  /** Mount object, movement and measured contact controls beside the live scene. */
  function mount(host, config, service, adapter, clock) {
    const document = host.ownerDocument;
    const scheduler = clock || document.defaultView;
    const listeners = [];
    let disposed = false;
    function element(tag, parent, className, text) {
      const item = document.createElement(tag);
      item.className = className || '';
      if (text) item.textContent = text;
      parent.appendChild(item);
      return item;
    }
    function listen(item, event, callback) {
      item.addEventListener(event, callback);
      listeners.push(() => item.removeEventListener(event, callback));
    }
    function button(parent, text, callback, className) {
      const item = element('button', parent, className, text);
      item.type = 'button';
      listen(item, 'click', callback);
      return item;
    }
    const panel = element('section', host, 'laboratory-physics');
    panel.setAttribute('aria-label', 'Labor mit Kontaktphysik');
    element('h3', panel, '', config.robot ? 'PR2 & Kontakt' : 'Glas & Kontakt');
    element('p', panel, 'laboratory-physics-intro', config.robot ? 'Der PR2 greift und bewegt das Glas mit Kontaktphysik.' : 'Gläser ziehen, anheben und loslassen. Hindernisse halten das Glas zurück.');
    const label = element('label', panel, 'laboratory-physics-field', 'Objekt');
    const select = element('select', label, 'laboratory-physics-object');
    for (const object of config.objects) {
      const option = element('option', select, '', object.label || object.key);
      option.value = object.key;
    }
    const controller = new Controller(config, service, adapter, render);
    const scale = element('section', panel, 'laboratory-physics-scale');
    element('h4', scale, '', 'Waage');
    const scaleReading = element('p', scale, 'laboratory-physics-scale-reading');
    const scaleStatus = element('p', scale, 'laboratory-physics-scale-status');
    element('p', scale, 'laboratory-physics-note', 'Flüssigkeiten werden mit Wasserdichte gewogen: 1 ml = 1 g.');
    const tare = button(scale, 'Tara / Nullstellen', () => controller.tareScale(), 'laboratory-physics-scale-tare');
    const moveToScale = button(scale, 'Objekt auf die Waage', () => controller.moveToScale(), 'laboratory-physics-scale-move');
    const cancelScale = button(scale, 'Transport anhalten', () => controller.hold(controller.selected), 'laboratory-physics-scale-cancel');
    const viewScale = adapter.focus && config.scale ? button(scale, 'Waage ansehen', () => {
      const position = config.scale.panPosition;
      adapter.focus([position[0] + .28, position[1] - .45, position[2] + .3], position);
    }, 'laboratory-physics-scale-view') : null;
    const liquid = element('section', panel, 'laboratory-physics-liquid');
    element('h4', liquid, '', 'Flüssigkeit');
    const liquidStatus = element('p', liquid, 'laboratory-physics-liquid-status');
    const volumeLabel = element('label', liquid, 'laboratory-physics-volume-label', 'Füllmenge (ml)');
    const volume = element('input', volumeLabel, 'laboratory-physics-volume');
    volume.type = 'number';
    volume.min = 0;
    volume.step = VOLUME_STEP;
    let volumeKey = null;
    listen(volume, 'input', render);
    const fill = button(liquid, 'Füllung setzen', () => controller.fillLiquid(Number(volume.value)), 'laboratory-physics-fill');
    const liftClear = button(liquid, 'Aus dem Ständer heben', () => controller.nudge([0, 0, LIFT_CLEARANCE]), 'laboratory-physics-lift-clear');
    liftClear.title = 'Ziel um ' + Math.round(LIFT_CLEARANCE * 100) + ' cm anheben';
    const tilts = element('div', liquid, 'laboratory-physics-tilts');
    const tiltLeft = button(tilts, '−' + TILT_STEP + '°', () => controller.tilt(-TILT_STEP), 'laboratory-physics-tilt-left');
    tiltLeft.setAttribute('aria-label', 'Nach links kippen · ' + TILT_STEP + ' Grad');
    const upright = button(tilts, 'Aufrecht', () => controller.upright(), 'laboratory-physics-upright');
    const tiltRight = button(tilts, '+' + TILT_STEP + '°', () => controller.tilt(TILT_STEP), 'laboratory-physics-tilt-right');
    tiltRight.setAttribute('aria-label', 'Nach rechts kippen · ' + TILT_STEP + ' Grad');
    const tiltStatus = element('p', liquid, 'laboratory-physics-tilt-status');
    const viewGlass = adapter.focus ? button(liquid, 'Glas ansehen', () => controller.viewGlass(), 'laboratory-physics-view-glass') : null;
    element('p', liquid, 'laboratory-physics-note', 'Erst anheben, dann kippen. Vereinfachtes Flüssigkeitsmodell.');
    const start = button(panel, 'Labor starten / fortsetzen', () => controller.start(), 'laboratory-physics-start');
    let robotRun, robotMix, robotPause, robotStatus, robotProgress, robotMeasurements, robotMotionNote;
    if (config.robot) {
      robotMix = button(panel, 'Misch-Demo starten', () => controller.runMixing(), 'laboratory-physics-robot-mix');
      element('p', panel, 'laboratory-physics-robot-recipe', 'Je 5 ml Bernstein und Türkis ins klare Glas gießen, sanft schwenken und in A2 ablegen. Ein neuer Start setzt diese Füllmengen und die Ausgangspositionen.');
      robotRun = button(panel, 'PR2: A1 → A3 starten', () => controller.run(), 'laboratory-physics-robot-run');
      element('p', panel, 'laboratory-physics-robot-reset-note', 'Neuer Durchlauf stellt PR2 und Gläser zurück; die Füllmengen bleiben erhalten. Pausierte Abläufe lassen sich fortsetzen.');
      robotPause = button(panel, 'PR2 pausieren', () => controller.pause(), 'laboratory-physics-robot-pause');
      robotStatus = element('p', panel, 'laboratory-physics-robot-status');
      robotStatus.setAttribute('role', 'status');
      robotProgress = element('progress', panel, 'laboratory-physics-robot-progress');
      robotProgress.max = 1;
      robotProgress.setAttribute('aria-label', 'Fortschritt der PR2-Bewegung');
      robotMeasurements = element('p', panel, 'laboratory-physics-robot-measurements');
      robotMotionNote = element('p', panel, 'laboratory-physics-robot-motion-note');
    }
    select.value = controller.selected;
    listen(select, 'change', () => { controller.scaleTransfer = null; controller.selected = select.value; render(); });
    const hold = button(panel, 'Glas halten', () => controller.hold(select.value), 'laboratory-physics-hold');
    const moves = element('div', panel, 'laboratory-physics-moves');
    const motion = [
      ['←', [-MOVE_STEP, 0, 0], 'Nach links'], ['↑', [0, MOVE_STEP, 0], 'Nach hinten'],
      ['↓', [0, -MOVE_STEP, 0], 'Nach vorne'], ['→', [MOVE_STEP, 0, 0], 'Nach rechts'],
      ['Anheben', [0, 0, MOVE_STEP], 'Anheben'], ['Absenken', [0, 0, -MOVE_STEP], 'Absenken'],
    ].map(([text, delta, title]) => {
      const control = button(moves, text, () => controller.nudge(delta));
      control.setAttribute('aria-label', title + ' · 1 cm');
      control.title = title + ' · 1 cm';
      return control;
    });
    const release = button(panel, 'Glas loslassen', () => controller.release(), 'laboratory-physics-release');
    const status = element('p', panel, 'laboratory-physics-status');
    status.setAttribute('role', 'status');
    const force = element('p', panel, 'laboratory-physics-force');
    const distance = element('p', panel, 'laboratory-physics-distance');
    element('p', panel, 'laboratory-physics-note', 'Orange: gewünschte Position · Rot: Kontaktpunkte');
    const camera = element('div', panel, 'laboratory-physics-cameras');
    if (config.cameras && adapter.focus) {
      for (const [name, text] of [['closeup', 'Nahansicht'], ['overview', 'Übersicht']]) {
        if (config.cameras[name]) button(camera, text, () => adapter.focus(config.cameras[name].position, config.cameras[name].target));
      }
    }
    const reset = button(panel, 'Labor zurücksetzen', () => controller.reset(), 'laboratory-physics-reset');
    function render() {
      if (disposed) return;
      const ready = controller.ready();
      const manualReady = controller.manualReady();
      const reading = controller.scale;
      scale.hidden = !config.scale && !reading;
      scaleReading.textContent = reading && reading.available && Number.isFinite(reading.grams)
        ? (Math.abs(reading.grams) < .0005 ? 0 : reading.grams).toFixed(3) + ' g' : '— g';
      scaleStatus.textContent = !reading || !reading.available ? 'Waage nicht verfügbar'
        : (reading.stable ? 'Stabil' : 'Einpendeln …') + ' · Tara: ' + reading.tareGrams.toFixed(3) + ' g';
      tare.disabled = !manualReady || !reading || !reading.available || !reading.stable || controller.scaleBusy();
      moveToScale.disabled = !manualReady || !config.scale || !controller.objects[controller.selected] || Boolean(controller.scaleTransfer);
      cancelScale.hidden = !controller.scaleTransfer;
      if (controller.scaleTransfer) scaleStatus.textContent = 'Objekt wird angehoben, zur Waage bewegt und abgesetzt …';
      start.hidden = ready;
      start.disabled = controller.starting;
      select.value = controller.selected;
      select.disabled = !manualReady;
      for (const control of [hold, ...motion]) control.disabled = !manualReady;
      reset.disabled = !ready;
      release.disabled = !manualReady || !controller.target;
      const tube = controller.selectedLiquid();
      liquid.hidden = !tube;
      if (viewGlass) viewGlass.disabled = !controller.objects[controller.selected];
      for (const control of [volume, fill, liftClear, tiltLeft, upright, tiltRight]) control.disabled = !manualReady || !tube;
      if (tube) {
        volume.max = tube.capacityMl;
        if (volumeKey !== controller.selected) {
          volume.value = String(Math.round(tube.volumeMl / VOLUME_STEP) * VOLUME_STEP);
          volumeKey = controller.selected;
        }
        const requestedVolume = Number(volume.value);
        fill.disabled = !manualReady || !String(volume.value).trim() || !Number.isFinite(requestedVolume) || requestedVolume < 0 || requestedVolume > tube.capacityMl;
        liquidStatus.textContent = tube.volumeMl.toFixed(1) + ' / ' + tube.capacityMl.toFixed(1) + ' ml · Ausgelaufen: ' + (Number(controller.liquid.spilledMl) || 0).toFixed(1) + ' ml';
        tiltStatus.textContent = 'Neigung: ' + Math.round(controller.observedTilt()) + '°';
      }
      status.textContent = controller.error || (controller.starting ? 'Labor wird gestartet …' : (ready ? (controller.robotBusy() ? 'Manuelles Bewegen ist während des PR2-Auftrags pausiert.' : (controller.target ? 'Glas gehalten · Pfeile bewegen das Ziel.' : 'Glas wählen oder direkt in der Szene ziehen.')) : 'Die Simulation ist nicht aktiv. Labor starten, um fortzufahren.'));
      status.dataset.error = String(Boolean(controller.error));
      if (config.robot) {
        const mixing = controller.robot.source === RobotSources.MIXING;
        const paused = controller.robot.state === RobotStates.PAUSED;
        robotMotionNote.textContent = mixing ? 'Bewegung: lokaler Kontaktregler. Kontakte: MuJoCo.' : 'Gelenkziele: gespeicherter CRAM-Plan. Bewegung und Kontakte: MuJoCo.';
        robotRun.disabled = !ready || Boolean(controller.robotBusy());
        robotRun.textContent = paused && !mixing ? 'PR2 fortsetzen' : 'PR2: A1 → A3 starten';
        robotMix.disabled = robotRun.disabled;
        robotMix.textContent = paused && mixing ? 'Misch-Demo fortsetzen' : 'Misch-Demo starten';
        robotPause.disabled = !ready || !controller.robotBusy() || controller.robotRequest === 'pause';
        const phase = controller.robot.message || RobotPhases[controller.robot.phase];
        const label = (mixing ? MixingLabels : RobotLabels)[controller.robot.state] || controller.robot.state;
        robotStatus.textContent = controller.robot.error || (label + (phase && controller.robot.state === RobotStates.RUNNING ? ' · ' + phase : ''));
        robotStatus.dataset.error = String(Boolean(controller.robot.error));
        robotProgress.value = Math.max(0, Math.min(1, Number(controller.robot.progress) || 0));
        const measurements = [];
        if (Number.isFinite(controller.robot.gripperForce)) measurements.push('Greifkontakt: ' + controller.robot.gripperForce.toFixed(2) + ' N');
        if (Number.isFinite(controller.robot.maximumLift)) measurements.push('Max. Hub: ' + (controller.robot.maximumLift * 100).toFixed(1) + ' cm');
        robotMeasurements.textContent = measurements.join(' · ');
        robotMeasurements.hidden = !measurements.length;
      }
      const feedback = controller.feedback();
      force.textContent = 'Kontakt: ' + feedback.contactCount + ' · ' + feedback.force.toFixed(2) + ' N';
      if (config.robot && controller.robot.holding) {
        distance.textContent = 'Vom PR2 gehalten · Kontaktphysik aktiv';
      } else if (config.robot && controller.robot.state === RobotStates.RUNNING) {
        distance.textContent = 'PR2-Auftrag aktiv · Kontaktphysik aktiv';
      } else {
        distance.textContent = controller.target ? 'Abstand zum Ziel: ' + (feedback.distance * 1000).toFixed(1) + ' mm' : 'Losgelassen · Schwerkraft aktiv';
      }
    }
    render();
    const initialized = controller.refresh();
    const timer = scheduler.setInterval(() => controller.refresh(), POLL_INTERVAL);
    return {
      controller, initialized, refresh: () => controller.refresh(),
      destroy() {
        disposed = true;
        controller.destroy();
        scheduler.clearInterval(timer);
        listeners.forEach(remove => remove());
        panel.remove();
      },
    };
  }

  /** Start or resume the laboratory physics scene from a non-physics page. */
  function mountStart(host, service, navigate) {
    const document = host.ownerDocument;
    const panel = document.createElement('section');
    panel.className = 'laboratory-physics-entry';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = service.robot ? 'PR2 mit Kontaktphysik' : 'Labor mit Kontaktphysik öffnen';
    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    panel.appendChild(button);
    panel.appendChild(status);
    host.appendChild(panel);
    let pending = false;
    const start = async () => {
      if (pending) return;
      pending = true;
      button.disabled = true;
      status.textContent = 'Labor wird vorbereitet …';
      try {
        const answer = await service.start();
        if (!answer.viewerUrl) throw new Error('Die Laboransicht ist noch nicht bereit.');
        navigate(answer.viewerUrl);
      } catch (error) {
        status.textContent = error.message;
        pending = false;
        button.disabled = false;
      }
    };
    button.addEventListener('click', start);
    return {destroy() { button.removeEventListener('click', start); panel.remove(); }};
  }

  return {Client, Controller, Routes, RobotRoutes, RobotStates, RobotSources, POLL_INTERVAL, MOVE_STEP, TILT_STEP, LIFT_CLEARANCE, SCALE_MOVE_TIMEOUT, mount, mountStart};
});
