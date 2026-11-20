import {placeDropdown} from './dropdown-position';
import {formatDate} from './date-time';
import type {ProgressMotion} from './series-progress-motion';

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

export function syncProgressRange(slider: HTMLInputElement, count: number, max: number, total: number, motion:ProgressMotion, preview = count) {
  if (slider.max !== String(max)) slider.max = String(max);
  if (slider.valueAsNumber !== count) slider.value = String(count);
  slider.setAttribute('aria-valuetext', `${count} of ${total} episodes watched`);
  progressFill(slider.parentElement!, preview, max,motion);
}

function typedEpisodeCount(input: HTMLInputElement) {
  const count = Math.min(input.valueAsNumber, Number(input.max));
  return Number.isInteger(count) && count >= 0 ? count : undefined;
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
  let countEdit: {input: HTMLInputElement; button: HTMLButtonElement; slider: HTMLInputElement; season: number} | undefined;
  const isPopover = () => supportsPopover && picker.matches(':popover-open');
  const updateFades = () => {
    scroll.toggleAttribute('data-fade-top', scroll.scrollTop > 1);
    scroll.toggleAttribute('data-fade-bottom', scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop > 1);
  };
  const reposition = () => {
    if (isPopover()) placeDropdown(trigger, picker, 400, true);
    updateFades();
  };
  const syncExpanded = () => trigger.setAttribute('aria-expanded', String(picker.open || isPopover()));
  const close = () => {
    finishCountEdit(false);
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

  trigger.addEventListener('click', event => {
    if (trigger.hasAttribute('popovertarget')) {
      // Keep opening and placement in one script turn, including trusted drag-release clicks.
      event.preventDefault();
      if (isPopover()) {close();return;}
      // The source option preserves native invoker focus order; older browsers ignore it.
      (picker.showPopover as (options:{source:HTMLElement})=>void)({source:trigger});
    } else picker.showModal();
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
    if (!picker.open && !isPopover()) finishCountEdit(false);
    reposition();
    syncExpanded();
  }, {signal});
  picker.addEventListener('close', () => {
    if (!picker.open && !isPopover()) finishCountEdit(false);
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
    const input = event.target as HTMLInputElement;
    if (countEdit?.input === input) {
      const {slider} = countEdit;
      // Preview the fill while keeping the slider value available for canceling the edit.
      progressFill(slider.parentElement!, typedEpisodeCount(input) ?? slider.valueAsNumber, Number(slider.max), motion);
      return;
    }
    const row = input.closest('li');
    if (row && input.matches('[data-row-slider]')) edit(Number(row.dataset.season), input.valueAsNumber);
  }, {signal});
  rows.addEventListener('click', event => {
    const button = (event.target as Element).closest<HTMLButtonElement>('button');
    const row = button?.closest<HTMLLIElement>('li');
    if (!row || !button || button.disabled) return;
    const season = Number(row.dataset.season);
    const slider = row.querySelector<HTMLInputElement>('[data-row-slider]')!;
    if (button.hasAttribute('data-row-counter')) {
      finishCountEdit(true);
      const input = row.querySelector<HTMLInputElement>('[data-row-count-input]')!;
      countEdit = {input, button, slider, season};
      input.value = slider.value;
      input.disabled = input.hidden = false;
      button.hidden = true;
      input.focus();
      input.select();
    } else if (button.hasAttribute('data-row-season')) {
      edit(season, 'toggle');
    } else if (button.dataset.rowStep) {
      edit(season, slider.valueAsNumber + Number(button.dataset.rowStep));
    }
  }, {signal});
  rows.addEventListener('focusout', event => {
    if (event.target === countEdit?.input) finishCountEdit(true);
  }, {signal});
  rows.addEventListener('keydown', event => {
    if (event.target !== countEdit?.input || (event.key !== 'Enter' && event.key !== 'Escape')) return;
    event.preventDefault();
    event.stopPropagation();
    if (event.isComposing || event.keyCode === 229) return;
    finishCountEdit(event.key === 'Enter', true);
  }, {signal});

  function finishCountEdit(commit: boolean, restoreFocus = false) {
    if (!countEdit) return;
    const {input, button, slider, season} = countEdit;
    countEdit = undefined;
    const value = commit ? typedEpisodeCount(input) : undefined;
    input.disabled = input.hidden = true;
    button.hidden = false;
    if (value !== undefined) edit(season, value);
    else progressFill(slider.parentElement!, slider.valueAsNumber, Number(slider.max), motion);
    if (restoreFocus) button.focus({preventScroll: true});
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
      finishCountEdit(false);
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
      const slider = row.querySelector<HTMLInputElement>('[data-row-slider]')!;
      const countField = row.querySelector<HTMLElement>('[data-row-count]')!;
      const counter = row.querySelector<HTMLButtonElement>('[data-row-counter]')!;
      const input = row.querySelector<HTMLInputElement>('[data-row-count-input]')!;
      const [minus, plus] = row.querySelectorAll<HTMLButtonElement>('[data-row-step]');
      const label = row.querySelector<HTMLButtonElement>('[data-row-season]')!;
      const action = canRestore ? 'Restore previous progress' : canToggle ? 'Mark released episodes watched' : '';
      label.textContent = name;
      label.disabled = !enabled || !released || !canToggle;
      label.title = label.disabled ? '' : action;
      label.setAttribute('aria-label', label.disabled ? name : `${name}: ${action}`);
      if ((!enabled || !released) && countEdit?.input === input) finishCountEdit(false);
      row.toggleAttribute('data-empty', count === 0);
      row.toggleAttribute('data-unreleased', !released);
      const date = row.querySelector<HTMLElement>('[data-row-release]')!;
      date.hidden = Boolean(released);
      for (const control of [slider.parentElement!, countField, minus, plus]) control.hidden = !released;
      progressFill(segments.children[index] as HTMLElement, count, total,motion);
      if (!released) {
        date.textContent = releaseDateLabel(releaseDate);
        return;
      }
      slider.disabled = !enabled;
      slider.setAttribute('aria-label', `${name} episode progress`);
      input.max = String(released);
      syncProgressRange(slider, count, released, total, motion, countEdit?.input === input ? typedEpisodeCount(input) : undefined);
      counter.toggleAttribute('data-complete', complete);
      countField.dataset.rowTotal = String(total);
      counter.querySelector('[data-row-count-label]')!.textContent = complete ? `✓ ${count}` : `${count}/${total}`;
      counter.disabled = !enabled;
      counter.setAttribute('aria-label', `Edit ${name} episode count: ${count} of ${total} watched`);
      input.setAttribute('aria-label', `Episodes watched for ${name}`);
      minus.disabled = slider.disabled || count === 0;
      plus.disabled = slider.disabled || count >= released;
      minus.setAttribute('aria-label', `Decrease ${name} progress`);
      plus.setAttribute('aria-label', `Increase ${name} progress`);
    });
    if (picker.open || isPopover()) updateFades();
  }

  return {render, close};
}
