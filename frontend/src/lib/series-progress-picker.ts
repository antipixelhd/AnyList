import {placeDropdown} from './dropdown-position';
import {formatDate} from './date-time';
import type {ProgressMotion} from './series-progress-motion';

const zoomThreshold = 24;
const zoomSpan = 20;

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

export function progressFill(element: HTMLElement, count: number, total: number, motion:ProgressMotion) {
  motion.fill(element,total ? count / total * 100 : 0);
}

export function syncProgressRange(slider: HTMLInputElement, count: number, max: number, total: number, motion:ProgressMotion, min = 0) {
  if (slider.min !== String(min)) slider.min = String(min);
  if (slider.max !== String(max)) slider.max = String(max);
  if (slider.valueAsNumber !== count) slider.value = String(count);
  slider.setAttribute('aria-valuetext', `${count} of ${total} episodes watched`);
  progressFill(slider.parentElement!, count - min, max - min,motion);
}

export function bindProgressRanges(panel: HTMLElement, signal: AbortSignal, motion:ProgressMotion) {
  let drag: {slider: HTMLInputElement; id: number; left: number; width: number;
    x: number; y: number; scrubbing: boolean} | undefined;

  const scrub = (event: PointerEvent) => {
    if (!drag || drag.slider.disabled) return;
    const {slider, left, width} = drag;
    const fraction = Math.min(1, Math.max(0, (event.clientX - left) / width));
    const min = Number(slider.min), max = Number(slider.max);
    const count = Math.round(min + fraction * (max - min));
    if (slider.valueAsNumber !== count) {
      slider.valueAsNumber = count;
      slider.dispatchEvent(new Event('input', {bubbles: true}));
    }
    motion.fill(slider.parentElement!,fraction*100,true);
  };
  const finish = () => {
    if (!drag) return;
    const {slider}=drag;
    const min=Number(slider.min),max=Number(slider.max);
    motion.endDrag(slider.parentElement!,max>min?(slider.valueAsNumber-min)/(max-min)*100:0);
    drag=undefined;
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
      motion.startDrag(target);
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
      if (dy >= dx) { finish(); return; }
      drag.scrubbing = true;
      motion.startDrag(drag.slider.parentElement!);
    }
    scrub(event);
  }, {signal});
  panel.addEventListener('pointerup', event => {
    if (drag?.id !== event.pointerId) return;
    if (drag.scrubbing) scrub(event);
    else {
      motion.startDrag(drag.slider.parentElement!);
      scrub(event);
    }
    finish();
  }, {signal});
  for (const type of ['pointercancel', 'lostpointercapture'] as const) {
    panel.addEventListener(type, event => {
      if (drag?.id === event.pointerId) finish();
    }, {signal});
  }
  signal.addEventListener('abort',finish,{once:true});
}

export function releaseDateLabel(value?: string | null) {
  return formatDate(value) || 'Release date TBD';
}

export function setupSeriesProgressPicker(panel: HTMLElement, editor: HTMLDialogElement, signal: AbortSignal,
  edit: (number: number, value: number | 'toggle') => void, motion:ProgressMotion) {
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
  let zoomedRow: HTMLLIElement | undefined;
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
    collapseZoom();
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
  picker.addEventListener('beforetoggle', event => {
    if ((event as ToggleEvent).newState !== 'open') return;
    // Opening finishes synchronously, but toggle is a later task and may miss first paint.
    queueMicrotask(() => {
      if (signal.aborted || (!picker.open && !isPopover())) return;
      reposition();
      syncExpanded();
    });
  }, {signal});
  picker.addEventListener('toggle', () => {
    if (!picker.open && !isPopover()) collapseZoom();
    reposition();
    syncExpanded();
  }, {signal});
  picker.addEventListener('close', () => {
    collapseZoom();
    syncExpanded();
  }, {signal});
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
    if (row && slider.matches('[data-row-slider],[data-detail-slider]')) edit(Number(row.dataset.season), slider.valueAsNumber);
  }, {signal});
  rows.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('button');
    const row = button?.closest<HTMLLIElement>('li');
    if (!row || !button || button.disabled) return;
    if (button.hasAttribute('data-row-zoom')) {
      toggleZoom(row, event.detail === 0);
      return;
    }
    const slider = row.querySelector<HTMLInputElement>('[data-row-slider]')!;
    edit(Number(row.dataset.season), button.dataset.rowStep
      ? slider.valueAsNumber + Number(button.dataset.rowStep) : 'toggle');
  }, {signal});

  function collapseZoom() {
    if (!zoomedRow) return;
    zoomedRow.querySelector<HTMLElement>('[data-row-detail]')!.hidden = true;
    zoomedRow.querySelector('[data-row-zoom]')!.setAttribute('aria-expanded', 'false');
    zoomedRow = undefined;
  }

  function syncZoom(row: HTMLLIElement, overview: HTMLInputElement, total: number, reset = false) {
    const slider = row.querySelector<HTMLInputElement>('[data-detail-slider]')!;
    const count = overview.valueAsNumber, released = Number(overview.max);
    let min = Number(slider.min), max = Number(slider.max);
    // Keep the scale fixed while dragging in the zoomed range.
    if (reset || count < min || count > max || max > released) {
      min = Math.max(0, Math.min(count - zoomSpan / 2, released - zoomSpan));
      max = min + zoomSpan;
    }
    syncProgressRange(slider, count, max, total, motion, min);
    row.querySelector('[data-detail-min]')!.textContent = String(min);
    row.querySelector('[data-detail-value]')!.textContent = String(count);
    row.querySelector('[data-detail-max]')!.textContent = String(max);
  }

  function toggleZoom(row: HTMLLIElement, focus: boolean) {
    const wasOpen = row === zoomedRow;
    collapseZoom();
    if (!wasOpen) {
      const overview = row.querySelector<HTMLInputElement>('[data-row-slider]')!;
      const detail = row.querySelector<HTMLElement>('[data-row-detail]')!;
      zoomedRow = row;
      row.querySelector('[data-row-zoom]')!.setAttribute('aria-expanded', 'true');
      syncZoom(row, overview, Number(row.dataset.total), true);
      detail.hidden = false;
      reposition();
      const visible = scroll.getBoundingClientRect();
      const bottom = detail.getBoundingClientRect().bottom;
      scroll.scrollTop += Math.max(0, bottom + 14 - visible.bottom);
      if (focus) detail.querySelector<HTMLInputElement>('input')!.focus({preventScroll: true});
    } else reposition();
  }

  function render(seasons: SeasonProgress[], enabled: boolean) {
    trigger.disabled = !seasons.length;
    const current = seasons.findLast(season => season.count > 0) ?? seasons.find(season => season.released > 0);
    position.textContent = current ? `S${current.number} · E${current.count}` : '—';
    completed.textContent = `${seasons.filter(season => season.complete).length} of ${seasons.length} seasons`;
    const totals = seasons.reduce(([count, total], season) => [count + season.count, total + season.total], [0, 0]);
    watched.textContent = `${totals[0]} / ${totals[1]} eps`;

    const signature = seasons.map(season => season.number).join(',');
    if (rows.dataset.seasons !== signature) {
      collapseZoom();
      rows.dataset.seasons = signature;
      rows.replaceChildren(...seasons.map(season => {
        const row = template.content.firstElementChild!.cloneNode(true) as HTMLLIElement;
        row.dataset.season = String(season.number);
        const detail = row.querySelector<HTMLElement>('[data-row-detail]')!;
        detail.id = `${picker.id}-zoom-${season.number}`;
        row.querySelector('[data-row-zoom]')!.setAttribute('aria-controls', detail.id);
        return row;
      }));
      segments.replaceChildren(...seasons.map(() => document.createElement('span')));
      scroll.scrollTop = 0;
    }
    const hadZoom = Boolean(zoomedRow);
    seasons.forEach(({name, count, total, released, releaseDate, complete, canToggle, canRestore}, index) => {
      const row = rows.children[index] as HTMLLIElement;
      row.dataset.total = String(total);
      const slider = row.querySelector<HTMLInputElement>('[data-row-slider]')!;
      const counter = row.querySelector<HTMLButtonElement>('[data-row-counter]')!;
      const zoom = row.querySelector<HTMLButtonElement>('[data-row-zoom]')!;
      const [minus, plus] = row.querySelectorAll<HTMLButtonElement>('[data-row-step]');
      const label = row.querySelector<HTMLElement>('[data-row-season]')!;
      label.textContent = name;
      label.hidden = released > zoomThreshold;
      zoom.hidden = !label.hidden;
      zoom.disabled = !enabled;
      if (zoom.hidden || zoom.disabled) {
        if (zoomedRow === row) collapseZoom();
      } else {
        zoom.querySelector('[data-row-zoom-name]')!.textContent = name;
        zoom.setAttribute('aria-label', `Zoom episode progress for ${name}`);
        row.querySelector('[data-detail-slider]')!.setAttribute('aria-label', `Fine episode progress for ${name}`);
      }
      row.toggleAttribute('data-empty', count === 0);
      row.toggleAttribute('data-unreleased', !released);
      const date = row.querySelector<HTMLElement>('[data-row-release]')!;
      date.hidden = Boolean(released);
      for (const control of [slider.parentElement!, counter, minus, plus]) control.hidden = !released;
      progressFill(segments.children[index] as HTMLElement, count, total,motion);
      if (!released) {
        date.textContent = releaseDateLabel(releaseDate);
        return;
      }
      slider.disabled = !enabled;
      slider.setAttribute('aria-label', `${name} episode progress`);
      syncProgressRange(slider, count, released, total,motion);
      if (zoomedRow === row) syncZoom(row, slider, total);
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
    if (hadZoom && !zoomedRow) reposition();
    else if (picker.open || isPopover()) updateFades();
  }

  return {render, close};
}
