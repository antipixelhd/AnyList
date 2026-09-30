import type { SyncJob } from "./api";
import { wireCancelButton } from "./sync-controls.ts";

const colors = {
  simkl: "bg-blue-600",
  trakt: "bg-red-500",
  mdblist: "bg-violet-500",
  bingebase: "bg-emerald-500",
  "trakt-import": "bg-red-500",
  "scrob-import": "bg-blue-500",
  "yamtrack-import": "bg-green-500",
};
type ProgressWidget = keyof typeof colors;

/** Resolve controls on each update because settings navigation replaces their DOM. */
export function createSyncProgressRenderer(
  widget: ProgressWidget,
  token: string,
  onError: (message: string) => void,
) {
  return (job: SyncJob | null) => {
    const isImport = widget.endsWith("-import");
    const progress = document.getElementById(`${widget}-progress`);
    const dropzone = isImport ? document.getElementById(`${widget}-dropzone`) : null;
    if (!progress || (isImport && !dropzone)) return;
    if (!job) {
      progress.classList.add("hidden");
      dropzone?.classList.remove("hidden");
      return;
    }
    progress.classList.remove("hidden");
    dropzone?.classList.add("hidden");

    const prefix = isImport ? widget : `${widget}-job`;
    const bar = document.getElementById(`${prefix}-bar`);
    const pctEl = document.getElementById(`${prefix}-pct`);
    const label = document.getElementById(`${prefix}-label`);
    const items = document.getElementById(`${prefix}-items`);
    const status = isImport ? document.getElementById(`${prefix}-status`) : null;
    const cancel = isImport ? null : document.querySelector<HTMLButtonElement>(`#${prefix}-cancel`);
    const operation = isImport || (widget === "trakt" && job.job_type === "import")
      ? "Import" : widget === "bingebase" || job.job_type === "push" ? "Push" : "Pull";
    const color = widget === "simkl" && job.job_type === "push" ? "bg-amber-500" : colors[widget];
    const baseClass = `${isImport ? "h-2" : "h-1.5"} rounded-full transition-all duration-500`;
    const failed = job.status === "failed";
    const cancelled = job.status === "cancelled";
    const completed = job.status === "completed";
    if (failed || cancelled || completed) {
      cancel?.classList.add("hidden");
      if (bar) {
        bar.className = `${baseClass} ${completed ? color : "bg-red-500"}`;
        bar.style.width = "100%";
      }
      if (label) label.textContent = `${operation} ${completed ? "complete" : cancelled ? "cancelled" : "failed"}`;
      if (pctEl) pctEl.textContent = completed ? "100%" : "";
      if (items) items.textContent = completed ? completedItems(widget, job)
        : isImport || cancelled ? "" : String(job.error_message || "Unknown error");
      if (status) status.textContent = failed ? String(job.error_message || "Unknown error") : "";
      return;
    }

    wireCancelButton(cancel, job, token, onError);
    const pct = job.total_items > 0 ? Math.min(100, Math.round(job.processed_items / job.total_items * 100)) : 0;
    const pending = job.status === "pending";
    const indeterminate = pending || pct === 0;
    if (bar) {
      bar.className = `${baseClass} ${color}${indeterminate ? " animate-pulse" : ""}`;
      bar.style.width = indeterminate ? "100%" : `${pct}%`;
    }
    const activeLabel = operation === "Import" ? "Importing…" : operation === "Push" ? "Pushing…" : "Pulling…";
    if (label) label.textContent = pending ? `${operation} queued…`
      : (!isImport && widget !== "bingebase" && job.current_step) || activeLabel;
    if (pctEl) pctEl.textContent = pct > 0 ? `${pct}%` : "";
    if (items) items.textContent = job.total_items > 0 ? `${job.processed_items} / ${job.total_items} items` : "Loading…";
    if (status) status.textContent = "";
  };
}

function completedItems(widget: ProgressWidget, job: SyncJob) {
  if (widget.endsWith("-import")) return job.total_items > 0
    ? `${job.processed_items} / ${job.total_items} items` : `${job.processed_items} items`;
  if (widget === "mdblist") return `${job.processed_items ?? 0} items`;
  if (widget === "bingebase") return `${job.processed_items ?? 0} items pushed`;
  const stats = job.stats;
  if (!stats) return `${job.processed_items} items`;
  return widget === "simkl"
    ? `${stats.movies ?? 0} movies, ${stats.episodes ?? 0} episodes, ${stats.ratings ?? 0} ratings`
    : `${stats.succeeded ?? 0} ok, ${stats.failed ?? 0} failed`;
}
