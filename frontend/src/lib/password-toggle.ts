const wiredDocuments = new WeakSet<Document>();

/** One navigation-safe listener also covers controls inside collapsible headers. */
export function wirePasswordToggles(root: Document = document): void {
  if (wiredDocuments.has(root)) return;
  wiredDocuments.add(root);
  root.addEventListener("click", event => {
    const toggle = event.target instanceof Element
      ? event.target.closest<HTMLButtonElement>(".password-toggle, .metadata-password-toggle") : null;
    const input = toggle?.parentElement?.querySelector("input");
    if (!input) return;
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    toggle?.querySelector(".eye-icon")?.classList.toggle("hidden", visible);
    toggle?.querySelector(".eye-slash-icon")?.classList.toggle("hidden", !visible);
  }, { capture: true });
}
