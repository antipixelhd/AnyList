import {acceptGameCategories, showCategoryInfo} from './game-category-store.ts';
import type {CategoryPresentation} from './list-categories.ts';

export function mountCategoryControls(root: HTMLElement, initial: CategoryPresentation, signal: AbortSignal) {
  let presentation = initial;
  let draft: CategoryPresentation;
  let mode = '';
  let busy = false;
  const candidate = root.querySelector<HTMLElement>('[data-category-controls]');
  if (!candidate) return;
  const controls: HTMLElement = candidate;
  const editor = root.querySelector<HTMLElement>('[data-category-editor]')!;
  const filters = root.querySelector<HTMLElement>('.status-filters')!;
  const rows = editor.querySelector<HTMLElement>('[data-category-edit-rows]')!;
  const error = editor.querySelector<HTMLElement>('[role=alert]')!;
  const save = editor.querySelector<HTMLButtonElement>('[data-category-save]')!;

  function finish() {
    if(busy)return;
    mode = '';
    editor.hidden = true;
    filters.hidden = false;
    controls.querySelectorAll('[aria-pressed]').forEach(button => button.setAttribute('aria-pressed', 'false'));
  }
  function move(id: string, delta: number) {
    const index = draft.categories.findIndex(category => category.id === id);
    const next = index + delta;
    if (next < 0 || next >= draft.categories.length) return;
    const [category] = draft.categories.splice(index, 1);
    draft.categories.splice(next, 0, category);
    const focused = document.activeElement as HTMLElement | null;
    for (const category of draft.categories) rows.append(rows.querySelector(`[data-category-row="${category.id}"]`)!);
    if (focused?.isConnected) focused.focus();
  }
  function render() {
    rows.replaceChildren();
    const counts = JSON.parse(root.dataset.statusCounts || '{}') as Record<string, number>;
    for (const category of draft.categories) {
      const row = document.createElement('div');
      row.className = 'category-edit-row';
      row.dataset.categoryRow = category.id;
      row.classList.toggle('category-hidden', category.hidden);
      const name = document.createElement(mode === 'edit' && category.can_rename ? 'input' : 'span');
      if (name instanceof HTMLInputElement) {
        name.value = category.label;
        name.maxLength = 32;
        name.setAttribute('aria-label', `Name for ${category.default_label}`);
        name.addEventListener('input', () => {category.label = name.value;});
      } else name.textContent = category.label;
      const button = (text: string, label: string, action: () => void) => {
        const control = document.createElement('button');
        control.type = 'button';
        control.className = 'category-icon';
        control.textContent = text;
        control.setAttribute('aria-label', label);
        control.title = label;
        control.addEventListener('click', action);
        return control;
      };
      if (mode === 'reorder') {
        const handle = button('↕', `Drag ${category.label}; use arrow keys to move`, () => {});
        handle.classList.add('category-drag');
        handle.addEventListener('keydown', event => {
          if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
            event.preventDefault(); move(category.id, event.key === 'ArrowUp' ? -1 : 1); handle.focus();
          }
        });
        let dragging = false;
        handle.addEventListener('pointerdown', event => {
          if (event.button !== 0) return;
          event.preventDefault(); handle.focus(); dragging = true; handle.setPointerCapture(event.pointerId);
          row.classList.add('category-dragging');
        });
        handle.addEventListener('pointermove', event => {
          if (!dragging) return;
          const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-category-row]');
          if (!target || target === row || !rows.contains(target)) return;
          const from = draft.categories.findIndex(item => item.id === category.id);
          const to = draft.categories.findIndex(item => item.id === target.dataset.categoryRow);
          move(category.id, to - from);
          handle.setPointerCapture(event.pointerId);
        });
        const release = () => {dragging = false; row.classList.remove('category-dragging');};
        handle.addEventListener('pointerup', release);
        handle.addEventListener('pointercancel', release);
        handle.addEventListener('lostpointercapture', release);
        row.append(handle, name,
          button('↑', `Move ${category.label} up`, () => move(category.id, -1)),
          button('↓', `Move ${category.label} down`, () => move(category.id, 1)));
      } else {
        row.append(name);
        if (category.can_hide && !counts[category.id]) {
          const toggle = button(category.hidden ? '↶' : '×', `${category.hidden ? 'Restore' : 'Hide'} ${category.label}`, () => {
            category.hidden = !category.hidden; render();
            rows.querySelector<HTMLButtonElement>(`[data-category-row="${category.id}"] button`)?.focus();
          });
          toggle.classList.toggle('category-hide', !category.hidden);
          if (category.hidden) {
            toggle.classList.add('category-restore');
            const icon = editor.querySelector<HTMLTemplateElement>('[data-category-restore-icon]')!;
            toggle.replaceChildren(icon.content.cloneNode(true));
          }
          row.append(toggle);
        }
      }
      rows.append(row);
    }
  }
  controls.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('[data-category-mode]');
    if (!button || busy) return;
    const next = button.dataset.categoryMode!;
    if (next === 'info') {showCategoryInfo(button, presentation); return;}
    if (mode === next) {finish(); return;}
    draft = structuredClone(presentation);
    mode = next;
    error.hidden = true;
    controls.querySelectorAll('[aria-pressed]').forEach(control => control.setAttribute('aria-pressed', String(control === button)));
    filters.hidden = true;
    editor.hidden = false;
    render();
  }, {signal});
  editor.querySelector('[data-category-cancel]')!.addEventListener('click', finish, {signal});
  editor.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !busy) {event.preventDefault(); finish();}
    if (event.key === 'Enter' && event.target instanceof HTMLInputElement) {event.preventDefault(); save.click();}
  }, {signal});
  save.addEventListener('click', async () => {
    if (busy) return;
    busy = true; error.hidden = true;
    editor.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button').forEach(control=>control.disabled=true);
    try {
      const response = await fetch('/api/proxy/tracking/list-categories/game', {
        method: 'PATCH', signal, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({revision: presentation.revision,
          order: draft.categories.map(category => category.id),
          labels: Object.fromEntries(draft.categories.filter(category => category.can_rename).map(category => [category.id, category.label])),
          hidden: draft.categories.filter(category => category.hidden).map(category => category.id)}),
      });
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 409) {
          const latest = await fetch('/api/proxy/tracking/list-categories/game', {signal, cache: 'no-store'});
          if (latest.ok) {presentation = await latest.json(); acceptGameCategories(presentation);}
        }
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to save categories.');
      }
      presentation = data;
      busy = false;
      finish();
      acceptGameCategories(presentation);
    } catch (cause) {
      if (!signal.aborted) {error.textContent = (cause as Error).message; error.hidden = false;}
    } finally {
      busy = false;
      editor.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button').forEach(control=>control.disabled=false);
    }
  }, {signal});
  document.addEventListener('anylist:game-categories', event => {
    const value = (event as CustomEvent<CategoryPresentation>).detail;
    if (root.dataset.owner === 'true' && value.revision >= presentation.revision) presentation = value;
  }, {signal});
}
