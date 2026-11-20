let initialized=false;
export function initializeEntryAnnotations() {
    if(initialized)return;initialized=true;
    const {signal}=new AbortController();
    // Keep long notes inside the viewport in compact and cover-grid layouts.
    const place = (note: HTMLElement) => {
      note.removeAttribute('data-tooltip-dismissed');
      const tooltip = note.querySelector<HTMLElement>('.entry-note-tooltip')!;
      tooltip.style.translate = '0 0';
      tooltip.classList.remove('tooltip-below');
      const bounds = tooltip.getBoundingClientRect();
      const shift = bounds.left < 12 ? 12 - bounds.left : bounds.right > window.innerWidth - 12 ? window.innerWidth - 12 - bounds.right : 0;
      tooltip.style.translate = `${shift}px 0`;
      const below = bounds.top < 12;
      tooltip.classList.toggle('tooltip-below', below);
    };
    document.addEventListener('pointerover', event => {
      const note = (event.target as HTMLElement).closest<HTMLElement>('.entry-note');
      if (note && !note.contains(event.relatedTarget as Node | null)) place(note);
    }, {signal});
    document.addEventListener('focusin', event => {
      const note = (event.target as HTMLElement).closest<HTMLElement>('.entry-note');
      if (note) place(note);
    }, {signal});
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') (event.target as HTMLElement).closest('.entry-note')?.setAttribute('data-tooltip-dismissed', 'true');
    }, {signal});
    window.addEventListener('resize', () => {
      document.querySelectorAll<HTMLElement>('.entry-note:hover,.entry-note:focus-within').forEach(place);
    }, {signal});
  }
