/* Manual, discrete manipulation of separately modelled laboratory objects. */
(function (root, factory) {
  'use strict';
  const laboratory = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = laboratory;
  if (root) root.LaboratoryWorkbench = laboratory;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  // %% interaction vocabulary
  const LIFT_HEIGHT = 1.14;
  const Codes = Object.freeze({
    DONE: 'done', NOT_READY: 'not_ready', INVALID: 'invalid', OCCUPIED: 'occupied',
    HANDS_FULL: 'hands_full', NOT_HELD: 'not_held', STOPPER_IN_USE: 'stopper_in_use',
  });
  const Messages = Object.freeze({
    [Codes.DONE]: 'Bereit.',
    [Codes.NOT_READY]: 'Die Laborobjekte werden noch geladen.',
    [Codes.INVALID]: 'Dieses Objekt oder dieser Platz ist nicht verfügbar.',
    [Codes.OCCUPIED]: 'Der Platz ist belegt. Bitte einen freien Platz wählen.',
    [Codes.HANDS_FULL]: 'Zuerst das angehobene Glas einsetzen.',
    [Codes.NOT_HELD]: 'Zuerst ein Glas aufnehmen.',
    [Codes.STOPPER_IN_USE]: 'Zuerst den Stopfen vom anderen Glas abnehmen.',
  });
  const TubeLabels = Object.freeze({
    tube_clear: 'Klares Glas', tube_amber: 'Bernsteinprobe', tube_teal: 'Türkise Probe',
  });

  /** A command outcome with a stable machine-readable reason and visible feedback. */
  class Outcome {
    constructor(code, message) {
      this.code = code;
      this.ok = code === Codes.DONE;
      this.message = message || Messages[code];
    }
  }

  // %% rack occupancy and stopper attachment
  /** Keep discrete manual manipulations consistent with the visible scene poses. */
  class Controller {
    constructor(config, adapter) {
      this.config = config;
      this.adapter = adapter;
      this.slots = new Map(config.rackSlots.map(slot => [slot.id, slot]));
      this.tubes = new Map(config.tubes.map(tube => [tube.key, tube]));
      this.occupancy = new Map(config.tubes.map(tube => [tube.slot, tube.key]));
      this.heldTube = null;
      this.cappedTube = null;
      this.drawerOpen = false;
    }

    /** Return the tube occupying a rack slot, or null for an empty slot. */
    occupant(slot) { return this.occupancy.get(slot) || null; }

    /** Return the recorded label of a tube, with readable defaults for the starter set. */
    label(key) {
      const tube = this.tubes.get(key);
      return tube && (tube.label || TubeLabels[key] || key);
    }

    /** Resolve the stopper's insertion pose along the tube's local upright axis. */
    stopperPose(tubePose) {
      const distance = this.config.stopper.tubeHeight - this.config.stopper.insertionDepth;
      const [x, y, z, qx, qy, qz, qw] = tubePose;
      return [
        x + distance * 2 * (qx * qz + qw * qy),
        y + distance * 2 * (qy * qz - qw * qx),
        z + distance * (1 - 2 * (qx * qx + qy * qy)),
        qx, qy, qz, qw,
      ];
    }

    /** Apply an object move and restore earlier writes if an adapter rejects one. */
    applyPoses(poses) {
      if (!this.adapter.isReady()) return false;
      const previous = poses.map(([key]) => this.adapter.getPose(key));
      if (previous.some(pose => !pose)) return false;
      this.adapter.pause();
      for (let index = 0; index < poses.length; index += 1) {
        if (this.adapter.setPose(poses[index][0], poses[index][1]) !== false) continue;
        for (let restore = index - 1; restore >= 0; restore -= 1) {
          this.adapter.setPose(poses[restore][0], previous[restore]);
        }
        return false;
      }
      return true;
    }

    /** Move a tube together with its attached stopper when present. */
    moveTube(key, pose) {
      const poses = [[key, pose]];
      if (this.cappedTube === key) poses.push([this.config.stopper.key, this.stopperPose(pose)]);
      return this.applyPoses(poses);
    }

    /** Lift one tube, releasing its rack slot only after the scene accepts the move. */
    lift(key) {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      if (!this.tubes.has(key)) return new Outcome(Codes.INVALID);
      if (this.heldTube) return new Outcome(Codes.HANDS_FULL);
      const pose = this.adapter.getPose(key);
      if (!pose) return new Outcome(Codes.NOT_READY);
      const lifted = pose.slice();
      lifted[2] = LIFT_HEIGHT;
      if (!this.moveTube(key, lifted)) return new Outcome(Codes.NOT_READY);
      for (const [slot, occupant] of this.occupancy) {
        if (occupant === key) this.occupancy.delete(slot);
      }
      this.heldTube = key;
      return new Outcome(Codes.DONE, this.label(key) + ' angehoben. Einen freien Platz wählen.');
    }

    /** Insert the held tube into a free rack slot. */
    insert(slot) {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      if (!this.slots.has(slot)) return new Outcome(Codes.INVALID);
      if (!this.heldTube) return new Outcome(Codes.NOT_HELD);
      if (this.occupant(slot)) return new Outcome(Codes.OCCUPIED);
      const key = this.heldTube;
      if (!this.moveTube(key, this.slots.get(slot).pose.slice())) return new Outcome(Codes.NOT_READY);
      this.occupancy.set(slot, key);
      this.heldTube = null;
      return new Outcome(Codes.DONE, this.label(key) + ' in ' + slot + ' eingesetzt.');
    }

    /** Attach the free stopper to a selected tube. */
    attachStopper(key) {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      if (!this.tubes.has(key)) return new Outcome(Codes.INVALID);
      if (this.cappedTube && this.cappedTube !== key) return new Outcome(Codes.STOPPER_IN_USE);
      const pose = this.adapter.getPose(key);
      if (!pose || !this.applyPoses([[this.config.stopper.key, this.stopperPose(pose)]])) return new Outcome(Codes.NOT_READY);
      this.cappedTube = key;
      return new Outcome(Codes.DONE, this.label(key) + ': Stopfen aufgesetzt.');
    }

    /** Return the stopper to its worktop position and clear its attachment. */
    removeStopper() {
      if (!this.applyPoses([[this.config.stopper.key, this.config.stopper.home.slice()]])) return new Outcome(Codes.NOT_READY);
      this.cappedTube = null;
      return new Outcome(Codes.DONE, 'Stopfen auf der Arbeitsplatte abgelegt.');
    }

    /** Move the drawer between its configured closed and open joint positions. */
    setDrawer(open) {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      this.adapter.pause();
      if (this.adapter.setJoint(this.config.drawer.joint, open ? this.config.drawer.open : 0) === false) return new Outcome(Codes.NOT_READY);
      this.drawerOpen = Boolean(open);
      return new Outcome(Codes.DONE, open ? 'Schublade geöffnet.' : 'Schublade geschlossen.');
    }

    /** Show one of the scene's saved cameras in world coordinates. */
    focus(name) {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      const camera = this.config.cameras[name];
      if (!camera) return new Outcome(Codes.INVALID);
      this.adapter.focus(camera.position.slice(), camera.target.slice());
      return new Outcome(Codes.DONE, name === 'closeup' ? 'Nahansicht der Laborbank.' : 'Übersicht des Labors.');
    }

    /** Restore the initial tube positions, stopper position and closed drawer. */
    reset() {
      if (!this.adapter.isReady()) return new Outcome(Codes.NOT_READY);
      const poses = this.config.tubes.map(tube => [tube.key, this.slots.get(tube.slot).pose.slice()]);
      poses.push([this.config.stopper.key, this.config.stopper.home.slice()]);
      this.adapter.pause();
      if (this.adapter.setJoint(this.config.drawer.joint, 0) === false) return new Outcome(Codes.NOT_READY);
      if (!this.applyPoses(poses)) {
        this.adapter.setJoint(this.config.drawer.joint, this.drawerOpen ? this.config.drawer.open : 0);
        return new Outcome(Codes.NOT_READY);
      }
      this.occupancy = new Map(this.config.tubes.map(tube => [tube.slot, tube.key]));
      this.heldTube = null;
      this.cappedTube = null;
      this.drawerOpen = false;
      return new Outcome(Codes.DONE, 'Laborbank zurückgesetzt.');
    }
  }

  // %% compact workbench controls
  /** Mount controls only when a scene supplies the laboratory interaction metadata. */
  function mount(host, config, adapter, mountRobot) {
    if (!config) return null;
    const controller = new Controller(config, adapter);
    const document = host.ownerDocument;
    const listeners = [];
    const panel = document.createElement('details');
    panel.className = 'laboratory-workbench';
    panel.open = true;
    panel.setAttribute('aria-label', mountRobot ? 'Laborbank – Roboter und manuelle Manipulation' : 'Laborbank – manuelle Manipulation');

    function element(tag, parent, className, text) {
      const item = document.createElement(tag);
      if (className) item.className = className;
      if (text) item.textContent = text;
      parent.appendChild(item);
      return item;
    }

    function listen(item, event, handler) {
      item.addEventListener(event, handler);
      listeners.push(() => item.removeEventListener(event, handler));
    }

    function button(parent, text, command, className) {
      const item = element('button', parent, className, text);
      item.type = 'button';
      listen(item, 'click', () => {
        const result = command();
        status.textContent = result.message;
        status.dataset.error = String(!result.ok);
        refresh();
      });
      return item;
    }

    const heading = element('summary', panel, 'laboratory-heading');
    element('span', heading, 'laboratory-title', 'Laborbank');
    element('span', heading, 'laboratory-mode', mountRobot ? 'CRAM + Manuell' : 'Manuell');
    const body = element('div', panel, 'laboratory-body');
    const robot = mountRobot ? mountRobot(body) : null;
    if (robot) element('strong', body, 'laboratory-manual-heading', 'Manuelle Laborbank');
    element('p', body, 'laboratory-intro', 'Gläser anheben, umstecken und verschließen.');
    const drawer = button(body, 'Schublade öffnen', () => controller.setDrawer(!controller.drawerOpen), 'laboratory-drawer');
    const tubeLabel = element('label', body, 'laboratory-field');
    element('span', tubeLabel, null, 'Reagenzglas');
    const tubeSelect = element('select', tubeLabel);
    for (const tube of config.tubes) {
      const option = element('option', tubeSelect, null, controller.label(tube.key));
      option.value = tube.key;
    }
    const lift = button(body, 'Glas aufnehmen', () => controller.lift(tubeSelect.value), 'laboratory-primary');
    const slotLabel = element('label', body, 'laboratory-field');
    element('span', slotLabel, null, 'Zielplatz im Ständer');
    const slotSelect = element('select', slotLabel);
    const slotOptions = new Map();
    for (const slot of config.rackSlots) {
      const option = element('option', slotSelect);
      option.value = slot.id;
      slotOptions.set(slot.id, option);
    }
    const insert = button(body, 'In Platz einsetzen', () => controller.insert(slotSelect.value), 'laboratory-primary');
    const stopper = button(body, 'Stopfen aufsetzen', () => controller.cappedTube === tubeSelect.value ? controller.removeStopper() : controller.attachStopper(tubeSelect.value));
    const inventory = element('p', body, 'laboratory-inventory');
    const status = element('p', body, 'laboratory-status', 'Ein Glas wählen und aufnehmen.');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const cameraRow = element('div', body, 'laboratory-camera-row');
    const closeup = button(cameraRow, 'Nahansicht', () => controller.focus('closeup'));
    const overview = button(cameraRow, 'Übersicht', () => controller.focus('overview'));
    const reset = button(body, 'Zurücksetzen', () => controller.reset(), 'laboratory-reset');
    element('p', body, 'laboratory-note', 'Direkte Objektpositionen · keine Flüssigkeitssimulation');
    let wasReady = adapter.isReady();

    /** Update occupancy, attachment and readiness after a command or scene load. */
    function refresh() {
      const ready = adapter.isReady();
      for (const [id, option] of slotOptions) {
        const occupant = controller.occupant(id);
        option.textContent = id + ' · ' + (occupant ? controller.label(occupant) : 'frei');
        option.disabled = Boolean(occupant);
      }
      if (!slotSelect.value || controller.occupant(slotSelect.value)) {
        const free = config.rackSlots.find(slot => !controller.occupant(slot.id));
        slotSelect.value = free ? free.id : '';
      }
      if (controller.heldTube) tubeSelect.value = controller.heldTube;
      tubeSelect.disabled = !ready || Boolean(controller.heldTube);
      slotSelect.disabled = !ready;
      lift.disabled = !ready || Boolean(controller.heldTube);
      insert.disabled = !ready || !controller.heldTube || !slotSelect.value || Boolean(controller.occupant(slotSelect.value));
      stopper.textContent = controller.cappedTube === tubeSelect.value ? 'Stopfen abnehmen' : 'Stopfen aufsetzen';
      stopper.disabled = !ready || Boolean(controller.cappedTube && controller.cappedTube !== tubeSelect.value);
      drawer.textContent = controller.drawerOpen ? 'Schublade schließen' : 'Schublade öffnen';
      for (const control of [drawer, closeup, overview, reset]) control.disabled = !ready;
      const free = config.rackSlots.length - controller.occupancy.size;
      inventory.textContent = free + ' / ' + config.rackSlots.length + ' Plätze frei' +
        (controller.heldTube ? ' · ' + controller.label(controller.heldTube) + ' angehoben' : '') +
        (controller.cappedTube ? ' · Stopfen: ' + controller.label(controller.cappedTube) : '');
      if (!ready) status.textContent = Messages[Codes.NOT_READY];
      if (ready && !wasReady) {
        status.textContent = 'Ein Glas wählen und aufnehmen.';
        status.dataset.error = 'false';
      }
      wasReady = ready;
    }

    listen(tubeSelect, 'change', refresh);
    listen(slotSelect, 'change', refresh);
    host.appendChild(panel);
    refresh();
    return {
      controller,
      refresh,
      destroy() {
        if (robot) robot.destroy();
        listeners.forEach(remove => remove());
        panel.remove();
      },
    };
  }

  return {Controller, Codes, LIFT_HEIGHT, mount};
});
