/* %% native PR2 controls independent of the 3D renderer */
(function () {
  'use strict';
  const host = document.getElementById('laboratory-pr2-controls');
  let controls = null;
  let physics = null;
  let robotPhysics = null;
  function mount() {
    if (controls) return;
    controls = LaboratoryRobot.mount(
      host, new LaboratoryRobot.Client(window.fetch.bind(window)), window,
    );
    if (typeof LaboratoryPhysics !== 'undefined') {
      physics = LaboratoryPhysics.mountStart(
        host, new LaboratoryPhysics.Client(window.fetch.bind(window)), url => window.location.assign(url),
      );
      robotPhysics = LaboratoryPhysics.mountStart(
        host, new LaboratoryPhysics.Client(window.fetch.bind(window), {robot: true}), url => window.location.assign(url),
      );
    }
  }
  mount();
  window.addEventListener('pagehide', () => {
    if (controls) controls.destroy();
    if (physics) physics.destroy();
    if (robotPhysics) robotPhysics.destroy();
    controls = null;
    physics = null;
    robotPhysics = null;
  });
  window.addEventListener('pageshow', mount);
})();
