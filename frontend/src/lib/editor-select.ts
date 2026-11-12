import {showMenu, hideMenu} from './ui-motion';

/** One interaction and motion contract for shared tracking-editor selects. */
export function setupEditorSelect(root: HTMLElement, signal: AbortSignal) {
  const select = root.querySelector<HTMLSelectElement>('select')!;
  const trigger = root.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')!;
  const value = root.querySelector<HTMLElement>('[data-editor-select-value]')!;
  const menu = root.querySelector<HTMLElement>('[role="listbox"]')!;
  const scope = root.closest('dialog') || root;
  let signature = '';
  const buttons = () => [...menu.querySelectorAll<HTMLButtonElement>('[data-editor-option]')];
  const enabled = () => buttons().filter(button => !button.disabled);
  const close = () => {
    trigger.setAttribute('aria-expanded', 'false');
    if (!menu.hidden) void hideMenu(menu);
  };
  const sync = () => {
    const options = [...select.options];
    const next = JSON.stringify(options.map(option => [option.value, option.text, option.disabled, option.hidden]));
    if (next !== signature) {
      close();
      menu.replaceChildren(...options.filter(option => !option.hidden).map(option => {
        const button = document.createElement('button');
        button.type = 'button';
        button.setAttribute('role', 'option');
        button.dataset.editorOption = option.value;
        button.textContent = option.text;
        button.disabled = option.disabled;
        return button;
      }));
      signature = next;
    }
    trigger.disabled = select.disabled;
    value.textContent = select.selectedOptions[0]?.text || select.getAttribute('aria-label') || '';
    if (!trigger.hasAttribute('aria-labelledby')) trigger.setAttribute('aria-label', `${select.getAttribute('aria-label')}: ${value.textContent}`);
    buttons().forEach(button => button.setAttribute('aria-selected', String(button.dataset.editorOption === select.value)));
    if (select.disabled) close();
  };
  const open = (focus = false) => {
    sync();
    if (trigger.disabled || !enabled().length) return;
    showMenu(menu);
    trigger.setAttribute('aria-expanded', 'true');
    if (focus) (enabled().find(button => button.dataset.editorOption === select.value) || enabled()[0])?.focus();
  };
  trigger.addEventListener('click', () => {
    if (trigger.getAttribute('aria-expanded') === 'true') close();
    else open();
  }, {signal});
  trigger.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); open(true);
    }
  }, {signal});
  menu.addEventListener('click', event => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-editor-option]');
    if (!button || button.disabled || !menu.contains(button)) return;
    select.value = button.dataset.editorOption!;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    sync(); close(); trigger.focus();
  }, {signal});
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && trigger.getAttribute('aria-expanded') === 'true') {
      event.preventDefault(); event.stopPropagation(); close(); trigger.focus();
      return;
    }
    if (!menu.contains(event.target as Node)) return;
    const items = enabled(), index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
    }
  }, {signal});
  scope.addEventListener('click', event => { if (!root.contains(event.target as Node)) close(); }, {signal});
  root.addEventListener('focusout', event => { if (!root.contains((event as FocusEvent).relatedTarget as Node)) close(); }, {signal});
  scope.addEventListener('close', close, {signal});
  select.addEventListener('change', sync, {signal});
  signal.addEventListener('abort', close, {once: true});
  sync();
  return {sync, close};
}
