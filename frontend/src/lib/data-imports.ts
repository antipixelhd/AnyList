export interface ImportFormat {
  id: "scrob" | "trakt" | "yamtrack";
  title: string;
  extension: ".zip" | ".csv";
  endpoint: string;
  fieldPrefix: string;
  color: "blue" | "green";
  options: readonly { key: string; label: string; secret?: boolean }[];
  note?: string;
}

const personalOptions = [
  { key: "watched", label: "Watch History" },
  { key: "ratings", label: "Ratings" },
  { key: "collection", label: "Collection" },
  { key: "lists", label: "Lists" },
  { key: "comments", label: "Comments" },
];

export const importFormats: readonly ImportFormat[] = [
  {
    id: "scrob", title: "AnyList", extension: ".zip", endpoint: "/api/proxy/export/import",
    fieldPrefix: "", color: "blue",
    options: [...personalOptions,
      { key: "api_keys", label: "Api Keys", secret: true },
      { key: "media_connections", label: "Media & Cloud Connections", secret: true },
      { key: "scrobble_connections", label: "Scrobble-only Connections", secret: true },
      { key: "connections", label: "Connections", secret: true },
    ],
  },
  {
    id: "trakt", title: "Trakt", extension: ".zip", endpoint: "/api/proxy/trakt/import/upload",
    fieldPrefix: "sync_", color: "blue",
    options: personalOptions.filter(option => option.key !== "collection"),
  },
  {
    id: "yamtrack", title: "Yamtrack/Floppy", extension: ".csv", endpoint: "/api/proxy/yamtrack/import/upload",
    fieldPrefix: "", color: "green", options: personalOptions,
    note: "Collection and Lists only come from a Floppy export - a vanilla Yamtrack file has neither, and simply won't add anything for those.",
  },
];

export function acceptsImportFile(format: ImportFormat, file: File): boolean {
  return file.name.toLowerCase().endsWith(format.extension);
}

interface UploadCallbacks {
  onStarted: (data: { job_id?: number; message?: string }) => void;
  onError: (message: string) => void;
}

/** A format has one active upload. Navigation cancels it and suppresses late feedback. */
export function createImportUploader(format: ImportFormat, token: string, callbacks: UploadCallbacks) {
  let request: AbortController | undefined;
  let stopped = false;
  return {
    async upload(file: File, selection: Record<string, boolean>) {
      if (stopped || request) return;
      if (!acceptsImportFile(format, file)) {
        callbacks.onError(`Please select a ${format.extension} export file.`);
        return;
      }
      if (!format.options.some(option => selection[option.key] === true)) {
        callbacks.onError("Select at least one item to import.");
        return;
      }
      const controller = (request = new AbortController());
      try {
        const body = new FormData();
        body.append("file", file);
        for (const option of format.options) {
          body.append(`${format.fieldPrefix}${option.key}`, String(selection[option.key] === true));
        }
        const response = await fetch(format.endpoint, {
          method: "POST", headers: { Authorization: `Bearer ${token}` }, body,
          signal: controller.signal,
        });
        const data = await response.json();
        if (controller.signal.aborted) return;
        if (!response.ok) throw new Error(data.detail || "Import failed");
        callbacks.onStarted(data);
      } catch (cause) {
        if (!controller.signal.aborted) callbacks.onError(cause instanceof Error ? cause.message : "Import failed");
      } finally {
        if (request === controller) request = undefined;
      }
    },
    stop() {
      stopped = true;
      request?.abort();
    },
  };
}
