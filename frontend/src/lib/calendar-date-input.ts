import AirDatepicker from 'air-datepicker';
import englishLocale from 'air-datepicker/locale/en';
import type {AirDatepickerLocale} from 'air-datepicker';
import {dateInputPlaceholder, formatDate, localToday, parseDateInput} from './date-time';
import {dismiss, reveal} from './ui-motion';

// The picker works in local calendar dates; never interpret ISO dates as UTC timestamps.
const pickerDate = (iso: string) => new Date(`${iso}T12:00:00`);
// The locale is published as CommonJS; Vite dev and production can unwrap it differently.
const localeEn: AirDatepickerLocale = 'default' in englishLocale
  ? (englishLocale as unknown as {default:AirDatepickerLocale}).default : englishLocale;

export function setupCalendarDateInputs(root: ParentNode, signal: AbortSignal) {
  const refreshers: Array<() => void> = [];
  const android = /Android/i.test(navigator.userAgent);
  root.querySelectorAll<HTMLElement>('[data-calendar-input]').forEach(control => {
    const input = control.querySelector<HTMLInputElement>('[data-date-iso]')!;
    const display = control.querySelector<HTMLInputElement>('[data-date-display]')!;
    const trigger = control.querySelector<HTMLButtonElement>('[data-date-picker]')!;
    let picker: AirDatepicker<HTMLElement> | undefined;
    let panel: HTMLDivElement | undefined;
    let header: HTMLElement | undefined;
    let opened = false;
    let generation = 0;
    const valid = (message = '') => {
      display.setCustomValidity(message);
      display.setAttribute('aria-invalid', String(Boolean(message)));
    };
    const syncPicker = () => {
      if (!picker) return;
      picker.clear({silent:true});
      if (input.value) {
        void picker.selectDate(pickerDate(input.value), {silent:true});
        picker.setViewDate(pickerDate(input.value));
      }
    };
    const refresh = () => {
      display.value = formatDate(input.value);
      display.removeAttribute('placeholder');
      valid();
      syncPicker();
    };
    const close = async (immediate = false) => {
      if (!panel || !opened) return;
      opened = false;
      const token = ++generation;
      display.setAttribute('aria-expanded', 'false');
      trigger.setAttribute('aria-expanded', 'false');
      if (!immediate) await dismiss(panel);
      if (generation === token && panel.matches(':popover-open')) panel.hidePopover();
    };
    const position = () => {
      if (!panel || !opened) return;
      const rect = control.getBoundingClientRect();
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft ?? 0;
      const top = viewport?.offsetTop ?? 0;
      const width = viewport?.width ?? innerWidth;
      const height = viewport?.height ?? innerHeight;
      panel.style.left = `${Math.max(left + 8, Math.min(rect.left, left + width - panel.offsetWidth - 8))}px`;
      const below = rect.bottom + 6;
      panel.style.top = `${Math.max(top + 8, Math.min(below + panel.offsetHeight <= top + height - 8 ? below : rect.top - panel.offsetHeight - 6, top + height - panel.offsetHeight - 8))}px`;
    };
    const updateHeader = () => {
      if (!picker || !header) return;
      const year = picker.viewDate.getFullYear();
      header.querySelector('[data-calendar-year]')!.textContent = picker.currentView === 'years' ? `${Math.floor(year / 10) * 10}–${Math.floor(year / 10) * 10 + 9}` : String(year);
      const month = header.querySelector<HTMLButtonElement>('[data-calendar-month]')!;
      month.textContent = localeEn.months[picker.viewDate.getMonth()];
      month.hidden = picker.currentView !== 'days';
      position();
    };
    const open = () => {
      if (input.disabled || display.disabled) return;
      if (android) {
        if (typeof input.showPicker === 'function') input.showPicker();
        return;
      }
      if (opened) return;
      if (!panel) {
        panel = document.createElement('div');
        panel.className = 'calendar-popover';
        panel.id = `${display.id}-calendar`;
        panel.popover = 'manual';
        panel.setAttribute('role', 'dialog');
        panel.setAttribute('aria-label', trigger.getAttribute('aria-label')!);
        // Stay inside the modal's active subtree, but escape its scroll clipping via top layer.
        control.append(panel);
        header = document.createElement('div');
        header.className = 'calendar-navigation';
        header.innerHTML = '<button type="button" data-calendar-shift="-12" aria-label="Previous year">«</button><button type="button" data-calendar-shift="-1" aria-label="Previous month">‹</button><button type="button" data-calendar-year aria-label="Choose year"></button><button type="button" data-calendar-month aria-label="Choose month"></button><button type="button" data-calendar-shift="1" aria-label="Next month">›</button><button type="button" data-calendar-shift="12" aria-label="Next year">»</button>';
        panel.append(header);
        const host = document.createElement('div');
        panel.append(host);
        picker = new AirDatepicker(host, {
          inline:true, locale:{...localeEn, daysMin:localeEn.daysShort}, firstDay:0,
          fixedHeight:true, toggleSelected:false,
          onChangeViewDate:updateHeader, onChangeView:updateHeader,
          onRenderCell:({date, cellType}) => ({attrs:{tabindex:'0', role:'button', 'aria-label':cellType === 'day' ? formatDate(localToday(date)) : cellType === 'month' ? localeEn.months[date.getMonth()] : String(date.getFullYear())}}),
          onSelect:({date}) => {
            if (!(date instanceof Date)) return;
            input.value = localToday(date);
            display.value = formatDate(input.value);
            valid();
            input.dispatchEvent(new Event('change', {bubbles:true}));
            void close();
            display.focus({preventScroll:true});
          },
        });
        header.addEventListener('click', event => {
          const button = (event.target as Element).closest<HTMLButtonElement>('button');
          if (!button || !picker) return;
          if (button.hasAttribute('data-calendar-year')) picker.setCurrentView('years');
          else if (button.hasAttribute('data-calendar-month')) picker.setCurrentView('months');
          else {
            const amount = Number(button.dataset.calendarShift);
            if (Math.abs(amount) === 1 && picker.currentView !== 'days') amount < 0 ? picker.prev() : picker.next();
            else {
              const date = new Date(picker.viewDate);
              date.setDate(1);
              date.setMonth(date.getMonth() + amount);
              picker.setViewDate(date);
            }
          }
          updateHeader();
        }, {signal});
        panel.addEventListener('keydown', event => {
          const cell = (event.target as Element).closest<HTMLElement>('.air-datepicker-cell');
          if (!cell) return;
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault(); cell.click();
          } else if (['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) {
            event.preventDefault();
            const cells = Array.from(cell.parentElement!.querySelectorAll<HTMLElement>('.air-datepicker-cell'));
            const columns = picker!.currentView === 'days' ? 7 : picker!.currentView === 'months' ? 3 : 4;
            const delta = event.key === 'ArrowLeft' ? -1 : event.key === 'ArrowRight' ? 1 : event.key === 'ArrowUp' ? -columns : columns;
            cells[(cells.indexOf(cell) + delta + cells.length) % cells.length]?.focus();
          }
        }, {signal});
        for (const element of [display, trigger]) {
          element.setAttribute('aria-controls', panel.id);
          element.setAttribute('aria-haspopup', 'dialog');
        }
      }
      generation++;
      opened = true;
      picker!.setCurrentView('days');
      syncPicker();
      if (!input.value) picker!.setViewDate(new Date());
      updateHeader();
      panel.showPopover();
      position();
      reveal(panel);
      display.setAttribute('aria-expanded', 'true');
      trigger.setAttribute('aria-expanded', 'true');
    };
    refreshers.push(() => { void close(true); refresh(); });
    display.addEventListener('input', () => {
      const raw = display.value.trim();
      const iso = raw ? parseDateInput(raw) : '';
      valid(iso === null ? `Enter a date in the format ${dateInputPlaceholder()}.` : '');
      if (iso !== null) { input.value = iso; syncPicker(); }
    }, {signal});
    display.addEventListener('blur', event => {
      if (display.validity.valid) refresh();
      if (!control.contains(event.relatedTarget as Node)) void close();
    }, {signal});
    input.addEventListener('change', refresh, {signal});
    control.addEventListener('click', event => {
      if (!panel?.contains(event.target as Node)) open();
    }, {signal});
    display.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        open();
        const cell = panel?.querySelector<HTMLElement>('.air-datepicker-cell.-selected-')
          ?? panel?.querySelector<HTMLElement>('.air-datepicker-cell.-current-')
          ?? panel?.querySelector<HTMLElement>('.air-datepicker-cell');
        cell?.focus();
      }
    }, {signal});
    document.addEventListener('pointerdown', event => {
      if (!control.contains(event.target as Node)) void close();
    }, {signal, capture:true});
    document.addEventListener('keydown', event => {
      if (opened && event.key === 'Escape') {
        event.preventDefault(); event.stopImmediatePropagation();
        void close(); display.focus({preventScroll:true});
      }
    }, {signal, capture:true});
    document.addEventListener('focusin', event => {
      if (!control.contains(event.target as Node)) void close();
    }, {signal});
    window.addEventListener('resize', position, {signal});
    window.visualViewport?.addEventListener('resize', position, {signal});
    document.addEventListener('scroll', position, {signal, capture:true});
    const dialog = control.closest('dialog');
    dialog?.addEventListener('close', () => { void close(true); }, {signal});
    const observer = new MutationObserver(() => { if (dialog?.hasAttribute('data-motion-closing')) void close(true); });
    if (dialog) observer.observe(dialog, {attributes:true, attributeFilter:['data-motion-closing']});
    input.form?.addEventListener('reset', () => queueMicrotask(() => { if (!signal.aborted) { void close(true); refresh(); } }), {signal});
    signal.addEventListener('abort', () => { observer.disconnect(); void close(true); picker?.destroy(); panel?.remove(); }, {once:true});
  });
  const refresh = () => refreshers.forEach(callback => callback());
  document.addEventListener('anylist:date-region-change', refresh, {signal});
  window.addEventListener('languagechange', refresh, {signal});
  refresh();
  return refresh;
}
