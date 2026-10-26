/** Preserve browser shortcuts and editing dialogs except for the advertised toggle. */
export function quickSearchShortcut(event: Pick<KeyboardEvent, 'key'|'ctrlKey'|'metaKey'|'shiftKey'|'altKey'|'repeat'|'isComposing'>, open: boolean, blocked = false): 'open'|'close'|'handled'|undefined {
  if (blocked || event.isComposing || event.altKey || event.shiftKey || !(event.ctrlKey || event.metaKey)) return;
  const key = event.key.toLowerCase();
  if (event.repeat && (key === 's' || key === 'k')) return 'handled';
  if (key === 's') return open ? 'close' : 'open';
  if (key === 'k') return 'open';
}
