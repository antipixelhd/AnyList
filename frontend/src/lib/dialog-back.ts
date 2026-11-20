type HistoryTraversal = Event & {
  navigationType: string;
  destination: { index: number; sameDocument: boolean };
};

/** Let a Back action close the current modal when the browser can cancel
 * that traversal. Native modal close requests also work independently. */
export function bindDialogBack(doc: Document) {
  const navigation = (doc.defaultView as (Window & {
    navigation?: EventTarget & { currentEntry: { index: number } | null };
  }) | null)?.navigation;
  navigation?.addEventListener('navigate', rawEvent => {
    const event = rawEvent as HistoryTraversal;
    const current = navigation.currentEntry;
    if (event.defaultPrevented || !event.cancelable || event.navigationType !== 'traverse' ||
        !event.destination.sameDocument || !current || event.destination.index < 0 ||
        event.destination.index >= current.index) return;

    const modal = 'dialog:modal:not([data-motion-closing])';
    const dialog = doc.activeElement?.closest<HTMLDialogElement>(modal) ??
      [...doc.querySelectorAll<HTMLDialogElement>(modal)].at(-1);
    if (!dialog) return;

    event.preventDefault();
    if (typeof dialog.requestClose === 'function') dialog.requestClose();
    else if (dialog.dispatchEvent(new Event('cancel', { cancelable: true }))) dialog.close();
  });
}
