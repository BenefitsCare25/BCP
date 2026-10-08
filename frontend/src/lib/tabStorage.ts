/** `sessionStorage` for this tab, with a memory fallback.
 *
 * Private mode and blocked site data make every storage call throw. The memory
 * copy keeps the value for the rest of this page load, which covers in-app
 * navigation; only a full reload with storage blocked loses it. */
const memory = new Map<string, string>();

export const tabStorage = {
  get(key: string): string | null {
    const held = memory.get(key);
    if (held !== undefined) return held;
    try {
      return window.sessionStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string): void {
    memory.set(key, value);
    try {
      window.sessionStorage.setItem(key, value);
    } catch {
      /* Storage blocked: the memory copy serves this page load. */
    }
  },
  remove(key: string): void {
    memory.delete(key);
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      /* Storage blocked: nothing was persisted. */
    }
  },
};
