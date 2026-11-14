import {bindRovingOptions} from './roving-options';
import {showMenu, hideMenu} from './ui-motion';
import {placeDropdown} from './dropdown-position';

/** One interaction and motion contract for shared tracking-editor selects. */
export function setupEditorSelect(root: HTMLElement, signal: AbortSignal) {
  const select = root.querySelector<HTMLSelectElement>('select')!;
  const trigger = root.querySelector<HTMLButtonElement>('[aria-haspopup="listbox"]')!;
  const value = root.querySelector<HTMLElement>('[data-editor-select-value]')!;
  const menu = root.querySelector<HTMLElement>('[role="listbox"]')!;
  const scope = root.closest('dialog') || root;
  const floating = menu.hasAttribute('popover') && typeof menu.showPopover === 'function';
  const place = () => { if (floating) placeDropdown(trigger, menu, 180); };
  const reposition = () => {
    if (trigger.getAttribute('aria-expanded') === 'true') place();
  };
  bindRovingOptions(menu, '[data-editor-option]', signal);
  let signature = '';
  let typed = '', typedAt = 0;
  const buttons = () => [...menu.querySelectorAll<HTMLButtonElement>('[data-editor-option]')];
  const enabled = () => buttons().filter(button => !button.disabled);
  const close = (immediate = false) => {
    if (trigger.getAttribute('aria-expanded') !== 'true' && !immediate) return;
    trigger.setAttribute('aria-expanded', 'false');
    void hideMenu(menu, immediate).then(() => {
      if (floating && menu.hidden && menu.matches(':popover-open')) menu.hidePopover();
    });
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
    buttons().forEach(button => {const selected=button.dataset.editorOption === select.value;button.setAttribute('aria-selected', String(selected));button.tabIndex=selected?0:-1;});
    if (select.disabled) close();
  };
  const open = (focus = false) => {
    sync();
    if (trigger.disabled || !enabled().length) return;
    if (floating) {
      menu.hidden = false;
      if (!menu.matches(':popover-open')) menu.showPopover();
      place();
    }
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
    } else if (event.key.length===1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing) {
      const now=performance.now();typed=now-typedAt<700?typed+event.key:event.key;typedAt=now;
      const match=items.find(item=>item.textContent?.trim().toLocaleLowerCase().startsWith(typed.toLocaleLowerCase()));
      if(match){event.preventDefault();match.focus();}
    }
  }, {signal});
  scope.addEventListener('click', event => { if (!root.contains(event.target as Node)) close(); }, {signal});
  root.addEventListener('focusout', event => { if (!root.contains((event as FocusEvent).relatedTarget as Node)) close(); }, {signal});
  if (floating) {
    scope.addEventListener('scroll', event => { if (event.target !== menu) reposition(); }, {signal, capture:true, passive:true});
    window.addEventListener('resize', reposition, {signal});
    window.visualViewport?.addEventListener('resize', reposition, {signal});
    window.visualViewport?.addEventListener('scroll', reposition, {signal});
  }
  scope.addEventListener('close', () => close(true), {signal});
  select.addEventListener('change', sync, {signal});
  signal.addEventListener('abort', () => close(true), {once: true});
  sync();
  return {sync, close};
}
