import {placeDropdown} from './dropdown-position';
import {formatDate} from './date-time';

type SeasonProgress = {
  number: number;
  name: string;
  count: number;
  total: number;
  released: number;
  releaseDate?: string | null;
  complete: boolean;
  canToggle: boolean;
  canRestore: boolean;
};

export function progressFill(element: HTMLElement, count: number, total: number) {
  element.style.setProperty('--progress', `${total ? count / total * 100 : 0}%`);
}

export function syncProgressRange(slider: HTMLInputElement, count: number, released: number, total: number) {
  if (slider.max !== String(released)) slider.max = String(released);
  if (slider.valueAsNumber !== count) slider.value = String(count);
  slider.setAttribute('aria-valuetext', `${count} of ${total} episodes watched`);
  progressFill(slider, count, released);
}

export function bindProgressRanges(panel: HTMLElement, signal: AbortSignal) {
  let drag: {slider: HTMLInputElement; id: number; left: number; width: number;
    x: number; y: number; scrubbing: boolean} | undefined;

  const scrub = (event: PointerEvent) => {
    if (!drag || drag.slider.disabled) return;
    const {slider, left, width} = drag;
    const fraction = Math.min(1, Math.max(0, (event.clientX - left) / width));
    const count = Math.round(fraction * Number(slider.max));
    if (slider.valueAsNumber === count) return;
    slider.valueAsNumber = count;
    slider.dispatchEvent(new Event('input', {bubbles: true}));
  };
  panel.addEventListener('pointerdown', event => {
    if (!event.isPrimary || event.button !== 0) return;
    const target = (event.target as Element).closest<HTMLElement>('[data-series-range]');
    const slider = target?.querySelector<HTMLInputElement>('input');
    if (!target || !slider || slider.disabled) return;
    const {left, width} = target.getBoundingClientRect();
    if (!width) return;
    drag = {slider, id: event.pointerId, left, width, x: event.clientX, y: event.clientY,
      scrubbing: event.pointerType === 'mouse'};
    target.setPointerCapture(event.pointerId);
    if (drag.scrubbing) {
      event.preventDefault();
      slider.focus({preventScroll: true});
      scrub(event);
    }
  }, {signal});
  panel.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    if (!drag.scrubbing) {
      const dx = Math.abs(event.clientX - drag.x), dy = Math.abs(event.clientY - drag.y);
      if (Math.max(dx, dy) < 5) return;
      // Leave vertical movement to the native scroll container without changing the count.
      if (dy >= dx) { drag = undefined; return; }
      drag.scrubbing = true;
    }
    scrub(event);
  }, {signal});
  panel.addEventListener('pointerup', event => {
    if (drag?.id !== event.pointerId) return;
    scrub(event);
    drag = undefined;
  }, {signal});
  for (const type of ['pointercancel', 'lostpointercapture'] as const) {
    panel.addEventListener(type, event => {
      if (drag?.id === event.pointerId) drag = undefined;
    }, {signal});
  }
}

export function releaseDateLabel(value?: string | null) {
  return formatDate(value) || 'Release date TBD';
}

export function setupSeriesProgressPicker(panel: HTMLElement, editor: HTMLDialogElement, signal: AbortSignal,
  edit: (number: number, value: number | 'toggle') => void) {
  const trigger = panel.querySelector<HTMLButtonElement>('[data-series-trigger]')!;
  const picker = panel.querySelector<HTMLDialogElement>('[data-series-picker]')!;
  const scroll = picker.querySelector<HTMLElement>('[data-series-scroll]')!;
  const rows = picker.querySelector<HTMLUListElement>('[data-series-rows]')!;
  const template = panel.querySelector<HTMLTemplateElement>('[data-series-row]')!;
  const watched = picker.querySelector<HTMLElement>('[data-series-watched]')!;
  const segments = panel.querySelector<HTMLElement>('[data-series-segments]')!;
  const position = panel.querySelector<HTMLElement>('[data-series-position]')!;
  const completed = panel.querySelector<HTMLElement>('[data-series-completed]')!;
  const mobile = matchMedia('(max-width:760px)');
  const supportsPopover = typeof picker.showPopover === 'function';
  const isPopover = () => supportsPopover && picker.matches(':popover-open');
  const updateFades = () => {
    scroll.toggleAttribute('data-fade-top', scroll.scrollTop > 1);
    scroll.toggleAttribute('data-fade-bottom', scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop > 1);
  };
  const reposition = () => {
    if (isPopover()) placeDropdown(trigger, picker, 364, true);
    updateFades();
  };
  const syncExpanded = () => trigger.setAttribute('aria-expanded', String(picker.open || isPopover()));
  const close = () => {
    if (isPopover()) picker.hidePopover();
    if (picker.open) picker.close();
    syncExpanded();
  };

  const syncMode = () => {
    close();
    if (!mobile.matches && supportsPopover) {
      picker.setAttribute('popover', 'auto');
      trigger.setAttribute('popovertarget', picker.id);
    } else {
      picker.removeAttribute('popover');
      trigger.removeAttribute('popovertarget');
      picker.removeAttribute('style');
      picker.classList.remove('tracker-floating-dropdown');
    }
  };
  syncMode();

  trigger.addEventListener('click', () => {
    if (trigger.hasAttribute('popovertarget')) return;
    picker.showModal();
    reposition();
    syncExpanded();
  }, {signal});
  picker.querySelector('[data-series-dismiss]')!.addEventListener('click', close, {signal});
  if (!('closedBy' in HTMLDialogElement.prototype)) {
    const outside = (event: PointerEvent) => {
      if (!picker.open || event.target !== picker) return false;
      const {left, right, top, bottom} = picker.getBoundingClientRect();
      return event.clientX < left || event.clientX > right || event.clientY < top || event.clientY > bottom;
    };
    let backdropPointer: number | undefined;
    picker.addEventListener('pointerdown', event => {
      backdropPointer = outside(event) ? event.pointerId : undefined;
    }, {signal});
    picker.addEventListener('pointerup', event => {
      if (backdropPointer === event.pointerId && outside(event)) close();
      backdropPointer = undefined;
    }, {signal});
    picker.addEventListener('pointercancel', () => { backdropPointer = undefined; }, {signal});
  }
  picker.addEventListener('toggle', () => {
    reposition();
    syncExpanded();
  }, {signal});
  picker.addEventListener('close', syncExpanded, {signal});
  scroll.addEventListener('scroll', updateFades, {signal, passive: true});
  editor.addEventListener('scroll', reposition, {signal, passive: true});
  window.addEventListener('resize', reposition, {signal});
  window.visualViewport?.addEventListener('resize', reposition, {signal});
  window.visualViewport?.addEventListener('scroll', reposition, {signal});
  mobile.addEventListener('change', syncMode, {signal});
  signal.addEventListener('abort', close, {once: true});

  rows.addEventListener('input', event => {
    const slider = event.target as HTMLInputElement;
    const row = slider.closest('li');
    if (row && slider.matches('[data-row-slider]')) edit(Number(row.dataset.season), slider.valueAsNumber);
  }, {signal});
  rows.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('button');
    const row = button?.closest('li');
    if (!row || !button || button.disabled) return;
    const slider = row.querySelector<HTMLInputElement>('input')!;
    edit(Number(row.dataset.season), button.dataset.rowStep
      ? slider.valueAsNumber + Number(button.dataset.rowStep) : 'toggle');
  }, {signal});

  function render(seasons: SeasonProgress[], enabled: boolean) {
    trigger.disabled = !seasons.length;
    const current = seasons.findLast(season => season.count > 0) ?? seasons.find(season => season.released > 0);
    position.textContent = current ? `S${current.number} · E${current.count}` : '—';
    completed.textContent = `${seasons.filter(season => season.complete).length} of ${seasons.length} seasons`;
    const totals = seasons.reduce(([count, total], season) => [count + season.count, total + season.total], [0, 0]);
    watched.textContent = `${totals[0]} / ${totals[1]} eps`;

    const signature = seasons.map(season => season.number).join(',');
    if (rows.dataset.seasons !== signature) {
      rows.dataset.seasons = signature;
      rows.replaceChildren(...seasons.map(season => {
        const row = template.content.firstElementChild!.cloneNode(true) as HTMLLIElement;
        row.dataset.season = String(season.number);
        return row;
      }));
      segments.replaceChildren(...seasons.map(() => document.createElement('span')));
      scroll.scrollTop = 0;
    }
    seasons.forEach(({name, count, total, released, releaseDate, complete, canToggle, canRestore}, index) => {
      const row = rows.children[index] as HTMLLIElement;
      const slider = row.querySelector<HTMLInputElement>('input')!;
      const counter = row.querySelector<HTMLButtonElement>('[data-row-counter]')!;
      const [minus, plus] = row.querySelectorAll<HTMLButtonElement>('[data-row-step]');
      row.querySelector('[data-row-season]')!.textContent = name;
      row.toggleAttribute('data-empty', count === 0);
      row.toggleAttribute('data-unreleased', !released);
      const date = row.querySelector<HTMLElement>('[data-row-release]')!;
      date.hidden = Boolean(released);
      for (const control of [slider.parentElement!, counter, minus, plus]) control.hidden = !released;
      progressFill(segments.children[index] as HTMLElement, count, total);
      if (!released) {
        date.textContent = releaseDateLabel(releaseDate);
        return;
      }
      slider.disabled = !enabled;
      slider.setAttribute('aria-label', `${name} episode progress`);
      syncProgressRange(slider, count, released, total);
      counter.toggleAttribute('data-complete', complete);
      counter.textContent = complete ? `✓ ${count}` : `${count}/${total}`;
      counter.disabled = !enabled || !canToggle;
      const action = canRestore ? 'Restore previous progress' : canToggle ? 'Mark released episodes watched' : '';
      counter.setAttribute('aria-label', `${name}: ${count} of ${total} watched${action ? `. ${action}` : ''}`);
      minus.disabled = slider.disabled || count === 0;
      plus.disabled = slider.disabled || count >= released;
      minus.setAttribute('aria-label', `Decrease ${name} progress`);
      plus.setAttribute('aria-label', `Increase ${name} progress`);
    });
    if (picker.open || isPopover()) updateFades();
  }

  return {render, close};
}
