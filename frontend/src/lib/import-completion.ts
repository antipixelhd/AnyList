type CompletionItem = {
  kind: 'movie' | 'show';
  completion_evidence?: {finish_date: string | null; suggested_date: string | null};
  outcome: {finish_date?: string | null};
};

type ProgressItem = {
  kind: 'movie' | 'show';
  requires_watch_confirmation?: boolean;
  progress_reviewed?: boolean;
  seasons?: Array<{season_number: number; represented: number; total_released: number | null}>;
};

export function hasConfirmedMovieEvidence(item: ProgressItem): boolean {
  return item.kind === 'movie' && item.requires_watch_confirmation === false && item.progress_reviewed === true;
}

export function needsImportProgressReview(item: ProgressItem): boolean {
  if (item.kind === 'movie') return !item.progress_reviewed;
  if (item.requires_watch_confirmation && !item.progress_reviewed) return true;
  const seasons = (item.seasons ?? []).filter(season => season.season_number > 0);
  if (seasons.some(season => season.total_released == null)) return true;
  const released = seasons.filter(season => (season.total_released ?? 0) > 0);
  return !released.length || released.some(season => season.represented < season.total_released!);
}

/** A new Completed choice may revise a previous answer; step submission must not prompt twice. */
export function needsImportFinishDate(item: CompletionItem, reselectingCompleted = false): boolean {
  return item.kind === 'show' && !item.completion_evidence?.finish_date
    && (reselectingCompleted || !Object.hasOwn(item.outcome, 'finish_date'));
}

export function importFinishDateSuggestion(item: CompletionItem): string {
  return item.outcome.finish_date ?? item.completion_evidence?.suggested_date ?? '';
}
