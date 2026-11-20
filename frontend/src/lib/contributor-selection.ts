/** Prevent an intermediate empty list from clamping the document's scroll. */
export function replaceContributorSelection(region:HTMLElement, change:()=>void) {
  const top=window.scrollY;
  const minimum=region.style.minHeight;
  region.style.minHeight=`${region.getBoundingClientRect().height}px`;
  try { change(); }
  finally {
    region.style.minHeight=minimum;
    // Explicit instant restoration also suppresses native anchoring to replaced
    // cards. The browser clamps this only when the final page is too short.
    window.scrollTo({top,behavior:'instant'});
  }
}
