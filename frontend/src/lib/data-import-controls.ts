import { acceptsImportFile, createImportUploader, importFormats, type ImportFormat } from "./data-imports";
import { showMessage } from "./settings-feedback";
import { hideOverlay, showOverlay } from "./ui-motion";

const borderColors = { blue: "border-blue-500", green: "border-green-500" };

function chooseOptions(format: ImportFormat, filename: string, signal: AbortSignal) {
  return new Promise<Record<string, boolean> | null>(resolve => {
    const modal = document.getElementById(`${format.id}-import-modal`);
    const body = document.getElementById(`${format.id}-import-modal-body`);
    const confirm = document.querySelector<HTMLButtonElement>(`#${format.id}-import-modal-confirm`);
    const cancel = document.querySelector<HTMLButtonElement>(`#${format.id}-import-modal-cancel`);
    const options = Array.from(modal?.querySelectorAll<HTMLButtonElement>("[data-import-option]") ?? []);
    if (signal.aborted || !modal || !body || !confirm || !cancel || !options.length) {
      resolve(null);
      return;
    }
    const listeners = new AbortController();
    const selected = new Set(format.options.filter(option => !option.secret).map(option => option.key));
    const render = () => {
      options.forEach(option => {
        const active = selected.has(option.dataset.importOption!);
        option.querySelector(".check-icon")?.classList.toggle("invisible", !active);
        option.classList.toggle(option.dataset.secret === "true" ? "border-amber-500" : borderColors[format.color], active);
        option.classList.toggle("border-transparent", !active);
      });
      confirm.disabled = selected.size === 0;
    };
    const finish = (selection: Record<string, boolean> | null) => {
      listeners.abort();
      signal.removeEventListener("abort", onAbort);
      if (!signal.aborted) void hideOverlay(modal);
      resolve(selection);
    };
    const onAbort = () => finish(null);
    signal.addEventListener("abort", onAbort, { once: true });
    options.forEach(option => option.addEventListener("click", () => {
      const key = option.dataset.importOption!;
      if (selected.has(key)) selected.delete(key); else selected.add(key);
      render();
    }, { signal: listeners.signal }));
    confirm.addEventListener("click", () => {
      if (!selected.size) return;
      finish(Object.fromEntries(format.options.map(option => [option.key, selected.has(option.key)])));
    }, { signal: listeners.signal });
    cancel.addEventListener("click", () => finish(null), { signal: listeners.signal });
    body.textContent = `Choose what to import from "${filename}". Existing AnyList data is retained and deduplicated.`;
    render();
    showOverlay(modal);
  });
}

/** Wire one page lifetime; the caller connects successful uploads to its shared job monitor. */
export function mountDataImports(token: string, onStarted: (format: ImportFormat["id"], jobId?: number) => void) {
  const lifetime = new AbortController();
  const { signal } = lifetime;
  const uploaders: ReturnType<typeof createImportUploader>[] = [];
  const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>(".import-tab-btn"));
  const activate = (name: string) => {
    tabs.forEach(tab => {
      const active = tab.dataset.importTab === name;
      for (const cls of ["bg-blue-600", "text-white", "font-semibold"]) tab.classList.toggle(cls, active);
      for (const cls of ["text-zinc-400", "font-medium"]) tab.classList.toggle(cls, !active);
    });
    document.querySelectorAll<HTMLElement>("[data-import-panel]").forEach(panel => {
      panel.classList.toggle("hidden", panel.dataset.importPanel !== name);
    });
  };
  tabs.forEach(tab => tab.addEventListener("click", () => activate(tab.dataset.importTab!), { signal }));
  if (tabs[0]) activate(tabs[0].dataset.importTab!);

  for (const format of importFormats) {
    const dropzone = document.getElementById(`${format.id}-import-dropzone`);
    const input = document.querySelector<HTMLInputElement>(`#${format.id}_export_file`);
    if (!dropzone || !input) continue;
    const uploader = createImportUploader(format, token, {
      onStarted(data) {
        showMessage(data.message || "Import started!", "success");
        onStarted(format.id, data.job_id);
      },
      onError: message => showMessage(message, "error"),
    });
    uploaders.push(uploader);
    let busy = false;
    const upload = async (file: File) => {
      if (signal.aborted || input.disabled || busy) return;
      if (!acceptsImportFile(format, file)) {
        showMessage(`Please select a ${format.extension} export file.`, "error");
        return;
      }
      busy = true;
      input.disabled = true;
      try {
        const selection = await chooseOptions(format, file.name, signal);
        if (selection && !signal.aborted) await uploader.upload(file, selection);
      } finally {
        busy = false;
        if (!signal.aborted) input.disabled = false;
      }
    };
    dropzone.addEventListener("click", () => { if (!input.disabled) input.click(); }, { signal });
    input.addEventListener("change", () => {
      const file = input.files?.[0];
      if (file) void upload(file);
      input.value = "";
    }, { signal });
    const clearHighlight = () => dropzone.classList.remove(borderColors[format.color], "bg-zinc-900/70");
    for (const event of ["dragover", "dragenter"]) {
      dropzone.addEventListener(event, event => {
        event.preventDefault();
        if (!input.disabled) dropzone.classList.add(borderColors[format.color], "bg-zinc-900/70");
      }, { signal });
    }
    for (const event of ["dragleave", "dragend"]) dropzone.addEventListener(event, clearHighlight, { signal });
    dropzone.addEventListener("drop", event => {
      event.preventDefault();
      clearHighlight();
      const file = event.dataTransfer?.files[0];
      if (file) void upload(file);
    }, { signal });
  }
  return {
    stop() {
      lifetime.abort();
      uploaders.forEach(uploader => uploader.stop());
    },
  };
}
