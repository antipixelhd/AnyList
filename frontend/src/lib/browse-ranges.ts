export function releaseYearBounds(start: string, end: string, min: number, max: number) {
  const clamp = (value: string, fallback: number) => {
    const year = Number(value.slice(0, 4));
    return value && Number.isFinite(year) ? Math.max(min, Math.min(max, year)) : fallback;
  };
  return { lower: clamp(start, min), upper: clamp(end, max) };
}

export function releaseYearDates(lower: number, upper: number, min: number, max: number) {
  return {
    start: lower === min ? "" : `${lower}-01-01`,
    end: upper === max ? "" : `${upper}-12-31`,
  };
}

export function initializeBrowseRanges(form: HTMLFormElement, signal: AbortSignal) {
  const start = form.elements.namedItem("start") as HTMLInputElement;
  const end = form.elements.namedItem("end") as HTMLInputElement;
  const year = form.querySelector<HTMLElement>("[data-year-range]")!;
  const lower = year.querySelector<HTMLInputElement>("[data-range-lower]")!;
  const upper = year.querySelector<HTMLInputElement>("[data-range-upper]")!;
  const votes = form.elements.namedItem("min_votes") as HTMLInputElement;
  const voteRange = votes.closest<HTMLElement>("[data-vote-range]")!;
  const min = Number(lower.min), max = Number(upper.max);
  const paint = () => {
    const bounds = releaseYearBounds(start.value, end.value, min, max);
    lower.value = String(bounds.lower);
    upper.value = String(bounds.upper);
    lower.setAttribute("aria-valuemax", upper.value);
    upper.setAttribute("aria-valuemin", lower.value);
    lower.style.zIndex = bounds.lower === max ? "3" : "";
    year.style.setProperty("--range-start", `${(bounds.lower - min) / (max - min) * 100}%`);
    year.style.setProperty("--range-end", `${(bounds.upper - min) / (max - min) * 100}%`);
    year.querySelector("output")!.textContent = start.value || end.value
      ? `${bounds.lower} – ${bounds.upper}` : "Any year";
    voteRange.style.setProperty("--range-end", `${Number(votes.value) / Number(votes.max) * 100}%`);
    voteRange.querySelector("output")!.textContent = Number(votes.value).toLocaleString();
  };
  for (const input of [lower, upper]) {
    input.addEventListener("input", () => {
      if (Number(lower.value) > Number(upper.value)) input.value = input === lower ? upper.value : lower.value;
      const dates = releaseYearDates(Number(lower.value), Number(upper.value), min, max);
      start.value = dates.start;
      end.value = dates.end;
      paint();
    }, { signal });
    input.addEventListener("change", () => start.dispatchEvent(new Event("change", { bubbles: true })), { signal });
  }
  votes.addEventListener("input", paint, { signal });
  paint();
  return paint;
}
