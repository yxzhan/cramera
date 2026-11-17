/**
 * The page on the full screen, as F11 shows it, for an address shared as a
 * presentation. Browsers grant the full screen only while a person clicks or presses a
 * key, so the page asks for it at the first such gesture.
 */

/**
 * The events that count as a person's gesture, by `Event.type`.
 */
export const FULLSCREEN_GESTURES = Object.freeze(['pointerdown', 'keydown']);

export class FirstGestureFullscreen {
  /**
   * The document whose root element takes the full screen.
   * @type {Document}
   */
  #document;

  /**
   * The listener, to remove it again.
   * @type {function(Event): void}
   */
  #listener;

  /**
   * @param {Document} document The document whose root element takes the full screen.
   */
  constructor(document) {
    this.#document = document;
    this.#listener = () => {
      this.dispose();
      if (document.fullscreenElement) return;
      // a browser that refuses, or one without the full screen, keeps the page as it is
      document.documentElement.requestFullscreen?.()?.catch(() => {});
    };
    for (const gesture of FULLSCREEN_GESTURES) document.addEventListener(gesture, this.#listener);
  }

  /**
   * Stop waiting for the first gesture.
   */
  dispose() {
    for (const gesture of FULLSCREEN_GESTURES) this.#document.removeEventListener(gesture, this.#listener);
  }
}
