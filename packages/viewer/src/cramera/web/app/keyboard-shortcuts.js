/**
 * Keyboard shortcuts of the viewer: space plays and pauses, the arrow keys step one
 * frame, Home and End jump to the ends, F moves the camera onto the selection.
 */

/**
 * The keys the viewer answers, by `KeyboardEvent.key`.
 */
export const ShortcutKey = Object.freeze({
  PLAY_PAUSE: ' ',
  PREVIOUS_FRAME: 'ArrowLeft',
  NEXT_FRAME: 'ArrowRight',
  FIRST_FRAME: 'Home',
  LAST_FRAME: 'End',
  FOCUS_SELECTION: 'f',
});

/**
 * The elements whose own keys win over the shortcuts.
 */
const FORM_CONTROLS = Object.freeze(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON']);

// %% shortcuts

/**
 * What the shortcuts act on.
 */
export class PlaybackActions {
  /**
   * @param {function(): void} togglePlaying Starts or pauses playback.
   * @param {function(number): void} step Moves by a number of frames.
   * @param {function(boolean): void} jump Jumps to the last frame when true, the first when false.
   * @param {function(): void} focus Moves the camera onto the selection.
   */
  constructor(togglePlaying, step, jump, focus = () => {}) {
    /**
     * Starts or pauses playback.
     * @type {function(): void}
     */
    this.togglePlaying = togglePlaying;
    /**
     * Moves by a number of frames.
     * @type {function(number): void}
     */
    this.step = step;
    /**
     * Jumps to an end of the recording.
     * @type {function(boolean): void}
     */
    this.jump = jump;
    /**
     * Moves the camera onto the selection.
     * @type {function(): void}
     */
    this.focus = focus;
  }
}

export class KeyboardShortcuts {
  /**
   * The element the shortcuts listen on.
   * @type {EventTarget}
   */
  #target;

  /**
   * The listener, to remove it again.
   * @type {function(KeyboardEvent): void}
   */
  #listener;

  /**
   * @param {EventTarget} target The element the shortcuts listen on, usually the document.
   * @param {PlaybackActions} actions What the shortcuts act on.
   */
  constructor(target, actions) {
    this.#target = target;
    this.#listener = (event) => {
      if (event.altKey || event.ctrlKey || event.metaKey || FORM_CONTROLS.includes(event.target?.tagName)) return;
      const action = {
        [ShortcutKey.PLAY_PAUSE]: () => actions.togglePlaying(),
        [ShortcutKey.PREVIOUS_FRAME]: () => actions.step(-1),
        [ShortcutKey.NEXT_FRAME]: () => actions.step(1),
        [ShortcutKey.FIRST_FRAME]: () => actions.jump(false),
        [ShortcutKey.LAST_FRAME]: () => actions.jump(true),
        [ShortcutKey.FOCUS_SELECTION]: () => actions.focus(),
      }[event.key.length === 1 ? event.key.toLowerCase() : event.key];
      if (!action) return;
      event.preventDefault();
      action();
    };
    target.addEventListener('keydown', this.#listener);
  }

  /**
   * Stop answering the keys.
   */
  dispose() {
    this.#target.removeEventListener('keydown', this.#listener);
  }
}
