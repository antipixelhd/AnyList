type CancellableJob = { id: number; job_type?: string };
const cancellations = new WeakMap<HTMLButtonElement, { jobId: number; requested: boolean }>();

/** Bind the same cancellation behavior on connections and maintenance jobs. */
export function wireCancelButton(
  button: HTMLButtonElement | null,
  job: CancellableJob,
  token: string,
  onError: (message: string) => void,
): void {
  if (!button) return;
  if (job.job_type === 'clear') {
    cancellations.delete(button);
    button.classList.add('hidden');
    button.onclick = null;
    return;
  }
  let state = cancellations.get(button);
  if (!state || state.jobId !== job.id) {
    state = { jobId: job.id, requested: false };
    cancellations.set(button, state);
  }
  const current = state;
  button.classList.remove('hidden');
  button.disabled = current.requested;
  button.textContent = current.requested ? 'Cancelling…' : 'Cancel';
  button.onclick = async () => {
    if (current.requested) return;
    current.requested = true;
    button.disabled = true;
    button.textContent = 'Cancelling…';
    try {
      const response = await fetch(`/api/proxy/sync/${job.id}/cancel`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(`Unable to cancel job (HTTP ${response.status})`);
      // Leave the button disabled until polling observes the cooperative stop.
    } catch (error) {
      if (cancellations.get(button) !== current) return;
      current.requested = false;
      button.disabled = false;
      button.textContent = 'Cancel';
      onError(error instanceof Error ? error.message : 'Unable to cancel job');
    }
  };
}
