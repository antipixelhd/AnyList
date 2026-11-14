/** A composite picker contributes one Tab stop; arrows remain owned by its controller. */
export function bindRovingOptions(root: HTMLElement, selector: string, signal: AbortSignal) {
  const activate = (target: Element) => {
    root.querySelectorAll<HTMLElement>(selector).forEach(option => {
      option.tabIndex = option === target ? 0 : -1;
    });
  };
  root.addEventListener('focusin', event => {
    const option = (event.target as Element).closest(selector);
    if (option && root.contains(option)) activate(option);
  }, {signal});
  return activate;
}
