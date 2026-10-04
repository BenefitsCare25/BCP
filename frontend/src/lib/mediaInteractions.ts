/** Remove browser media save/copy menus and dragging, preserving text and links.
 * These are UI restrictions; public browser assets remain retrievable by URL.
 */
export function installMediaInteractionGuards(): void {
  const protectMedia = (event: Event) => {
    if (event.composedPath().some(target => target instanceof Element
      && target.matches("img, video, audio, [data-protected-media]"))) {
      event.preventDefault();
    }
  };
  document.addEventListener("contextmenu", protectMedia, { capture: true });
  document.addEventListener("dragstart", protectMedia, { capture: true });
}
