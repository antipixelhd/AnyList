export function availabilityLabel(reason?: string | null) {
  if (reason === 'upcoming') return 'Season premiere scheduled';
  return reason === 'airing' ? 'Currently airing' : 'New season available';
}

export function paintEntryAvailability(dot: HTMLElement, available: boolean, reason?: string | null) {
  const label = availabilityLabel(reason);
  dot.hidden = !available;
  dot.dataset.availability = reason ?? '';
  dot.title = label;
  dot.setAttribute('aria-label', label);
}
