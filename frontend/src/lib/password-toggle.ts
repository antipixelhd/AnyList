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
    const externalSecret = input.hasAttribute('data-connection-secret');
    const visible = externalSecret ? input.dataset.secretVisible !== 'true' : input.type === "password";
    if (externalSecret) input.dataset.secretVisible = String(visible);
    else input.type = visible ? "text" : "password";
    toggle?.querySelector(".eye-icon")?.classList.toggle("hidden", visible);
    toggle?.querySelector(".eye-slash-icon")?.classList.toggle("hidden", !visible);
  }, { capture: true });
}
