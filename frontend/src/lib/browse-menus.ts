import {showMenu,hideMenu,dismiss} from './ui-motion';

/** Shared browse menus: outside-click, Escape, focus restoration and motion. */
export function initializeBrowseMenus(form:HTMLFormElement,signal:AbortSignal){
  const advancedFilters=form.querySelector<HTMLDetailsElement>('.browse-more')!;
  const advancedPanel=form.querySelector<HTMLElement>('.browse-secondary-filters')!;
  const closeMenus = (except?: HTMLDetailsElement) =>
    form
      .querySelectorAll<HTMLDetailsElement>("[data-filter-menu]")
      .forEach((menu) => {
        if (menu === except || !menu.open) return;
        void closeFilter(menu);
      });
  async function closeFilter(menu: HTMLDetailsElement, restoreFocus = false) {
    const panel = menu.querySelector<HTMLElement>(".browse-options")!;
    await hideMenu(panel);
    if (panel.hidden) menu.open = false;
    if (restoreFocus) menu.querySelector<HTMLElement>("summary")?.focus();
  }
  async function closeAdvancedFilters(restoreFocus = false) {
    if (!advancedFilters.open || advancedPanel.inert) return;
    advancedPanel.inert = true;
    if (await dismiss(advancedPanel)) {
      advancedFilters.open = false;
      advancedPanel.inert = false;
      if (restoreFocus) advancedFilters.querySelector<HTMLElement>("summary")?.focus();
    }
  }
  for (const menu of form.querySelectorAll<HTMLDetailsElement>(
    "[data-filter-menu]",
  )) {
    menu.querySelector("summary")!.addEventListener(
      "click",
      (event) => {
        event.preventDefault();
        const panel = menu.querySelector<HTMLElement>(".browse-options")!;
        if (menu.open && !panel.inert) void closeFilter(menu);
        else {
          closeMenus(menu);
          menu.open = true;
          showMenu(panel);
        }
      },
      { signal },
    );
  }
  document.addEventListener(
    "click",
    (event) => {
      if (!(event.target as Element).closest("[data-filter-menu]"))
        closeMenus();
      // Inline phone filters behave like the list-page section, not a popover.
      if (!form.hasAttribute('data-mobile-filters') && !advancedFilters.contains(event.target as Node) &&
          !(event.target as Element).closest(".browse-select-options"))
        void closeAdvancedFilters();
    },
    { signal },
  );
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !(event.target as Element).closest('[role="listbox"]'))
      void closeAdvancedFilters(true);
  }, { signal });
  form.addEventListener(
    "keydown",
    (event) => {
      if (event.key === "Escape") {
        const menu = (event.target as Element).closest<HTMLDetailsElement>(
          "[data-filter-menu]",
        );
        if (menu?.open) {
          event.stopPropagation();
          void closeFilter(menu, true);
        }
      }
    },
    { signal },
  );
  return {closeMenus,closeAdvancedFilters};
}
