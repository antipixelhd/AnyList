import {markFailureHandled} from './handled-failures.ts';

export function mountDetailNote(root: HTMLElement, write: (value: string) => Promise<string>, signal: AbortSignal) {
  const input = root.querySelector<HTMLTextAreaElement>('#detail-note');
  const status = root.querySelector<HTMLElement>('[data-detail-note-status]');
  if (!input || !status) return;
  const document = input.ownerDocument;
  const window = document.defaultView;

  let savedValue = input.value;
  let dirty = false;
  let composing = false;
  let revision = 0;
  let saving = false;
  let timer: ReturnType<typeof setTimeout>;

  const flush = async () => {
    clearTimeout(timer);
    if (!dirty || composing) return;
    dirty = false;
    const value = input.value;
    if (!saving && value === savedValue) {
      status.textContent = '';
      return;
    }
    const version = ++revision;
    saving = true;
    status.textContent = 'Saving…';
    try {
      const saved = await write(value);
      if (version !== revision) return;
      savedValue = saved;
      if (!dirty && !signal.aborted) {
        if (input.value !== saved) input.value = saved;
        status.textContent = 'Saved';
      }
    } catch (error) {
      markFailureHandled(error);
      if (version !== revision || dirty) return;
      dirty = true;
      if (!signal.aborted) status.textContent = 'Note not saved';
    } finally {
      if (version === revision) saving = false;
    }
  };
  const schedule = () => {
    dirty = true;
    status.textContent = '';
    clearTimeout(timer);
    if (!composing) timer = setTimeout(flush, 500);
  };

  input.addEventListener('input', schedule, {signal});
  input.addEventListener('compositionstart', () => { composing = true; clearTimeout(timer); }, {signal});
  input.addEventListener('compositionend', () => { composing = false; schedule(); }, {signal});
  input.addEventListener('blur', flush, {signal});
  window?.addEventListener('online', flush, {signal});
  window?.addEventListener('pagehide', flush, {signal});
  document.addEventListener('visibilitychange', () => { if (document.hidden) void flush(); }, {signal});
  signal.addEventListener('abort', flush, {once:true});

  return (value: string) => {
    savedValue = value;
    if (!saving && !dirty && input.value !== value) input.value = value;
  };
}
