let initialized=false;
/** One delegated selector handler, shared by SSR and dynamically retained panels. */
export function initializeProfileSections(){
  if(initialized)return;initialized=true;
document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const button = target.closest<HTMLButtonElement>(
      ".profile-section-selector__option[data-section]",
    );
    if (!button) return;

    const selector = button.closest<HTMLElement>(".profile-section-selector");
    if (!selector) return;

    const value = button.dataset.section;
    if (!value) return;

    selector.querySelectorAll<HTMLButtonElement>(".profile-section-selector__option").forEach((option) => {
      const selected = option === button;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-pressed", String(selected));
    });

    selector.dispatchEvent(
      new CustomEvent("profile-section-change", {
        bubbles: true,
        detail: { value },
      }),
    );
  });
}
