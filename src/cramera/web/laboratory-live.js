/* %% wait for the identified PR2 laboratory and its live scene bundle */
(function () {
  'use strict';
  const status = document.getElementById('laboratory-live-status');
  const entry = new LaboratoryRobot.LiveEntry(
    new LaboratoryRobot.Client(window.fetch.bind(window)),
    url => window.location.replace(url),
    message => { status.textContent = message; },
  );
  entry.refresh();
  const timer = window.setInterval(() => entry.refresh(), 1500);
  window.addEventListener('pagehide', () => window.clearInterval(timer), {once: true});
})();
