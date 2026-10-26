/** Only word starts change; acronyms, deliberate capitals and nonletters stay intact. */
export function capitalizeSearchWords(value: string): string {
  return value.replace(/(^|\s)(\p{Ll})/gu, (_match, space: string, letter: string) => space + letter.toUpperCase());
}

/** Run before search listeners read the input, without emitting another normal input event. */
export function wireSearchWordCapitalization(input: HTMLInputElement, signal: AbortSignal, enabled = () => matchMedia('(max-width:650px), (pointer:coarse)').matches) {
  const normalize = () => {
    if (!enabled()) return false;
    const value = input.value;
    const next = capitalizeSearchWords(value);
    if (next === value) return false;
    const start = input.selectionStart, end = input.selectionEnd, direction = input.selectionDirection;
    input.value = next;
    if (start !== null && end !== null) input.setSelectionRange(
      capitalizeSearchWords(value.slice(0, start)).length,
      capitalizeSearchWords(value.slice(0, end)).length,
      direction || undefined,
    );
    return true;
  };
  input.addEventListener('input', event => {
    if (!(event as InputEvent).isComposing) normalize();
  }, {capture:true, signal});
  input.addEventListener('compositionend', () => {
    if (normalize()) input.dispatchEvent(new Event('input', {bubbles:true}));
  }, {signal});
}
