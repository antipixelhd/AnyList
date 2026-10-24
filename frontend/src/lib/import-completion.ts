type CompletionItem = {
  kind: 'movie' | 'show';
  completion_evidence?: {finish_date: string | null; suggested_date: string | null};
  outcome: {finish_date?: string | null};
};

/** A new Completed choice may revise a previous answer; step submission must not prompt twice. */
export function needsImportFinishDate(item: CompletionItem, reselectingCompleted = false): boolean {
  return item.kind === 'show' && !item.completion_evidence?.finish_date
    && (reselectingCompleted || !Object.hasOwn(item.outcome, 'finish_date'));
}

export function importFinishDateSuggestion(item: CompletionItem): string {
  return item.outcome.finish_date ?? item.completion_evidence?.suggested_date ?? '';
}
