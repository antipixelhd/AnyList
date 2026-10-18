import { timelineGroups, type TimelineItem } from './browse-timeline-data';
import { setLocalDateTime } from './date-time-dom';

/** Provider pages share one chronological snapshot. Append new cards only;
 * existing cards/headings keep their DOM positions and interaction state. */
export function appendBrowseTimeline<T extends TimelineItem>(root: HTMLElement, cards: HTMLElement[], items: Map<HTMLElement,T>, ascending = false) {
  const byItem = new Map(cards.filter(card => !root.contains(card)).map(card => [items.get(card)!, card]));
  const years = new Map([...root.querySelectorAll<HTMLElement>('[data-timeline-year]')].map(year => [year.dataset.timelineYear!, year]));
  const months = new Map([...root.querySelectorAll<HTMLElement>('[data-timeline-month]')].map(month => [month.dataset.timelineMonth!, month]));
  timelineGroups([...byItem.keys()].filter(Boolean), ascending).forEach(group => {
    let year = years.get(group.year);
    if (!year) {
      year = document.createElement('section');
      year.className = 'browse-timeline-year';
      year.dataset.timelineYear = group.year;
      const heading = document.createElement('h2');
      heading.className = 'browse-timeline-year-label';
      heading.textContent = group.year || 'Undated';
      const body = document.createElement('div');
      body.className = 'browse-timeline-months';
      year.append(heading, body);
      root.append(year);
      years.set(group.year, year);
    }
    const body = year.querySelector<HTMLElement>('.browse-timeline-months')!;
    group.months.forEach(group => {
      let month = months.get(group.key);
      if (!month) {
        month = document.createElement('section');
        month.className = 'browse-timeline-month';
        month.dataset.timelineMonth = group.key;
        const heading = document.createElement('h3');
        heading.textContent = group.label;
        if (/^\d{4}-\d{2}$/.test(group.key) && !group.key.endsWith('-00')) setLocalDateTime(heading, group.key, 'month-name');
        const grid = document.createElement('div');
        grid.className = 'browse-grid';
        month.append(heading, grid);
        body.append(month);
        months.set(group.key, month);
      }
      const grid = month.querySelector<HTMLElement>('.browse-grid')!;
      group.items.forEach(item => grid.append(byItem.get(item)!));
    });
  });
}
