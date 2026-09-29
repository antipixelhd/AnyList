type CancellableJob = { id: number; job_type?: string };

/** Bind the same cancellation behavior on connections and maintenance jobs. */
export function wireCancelButton(
  button: HTMLButtonElement | null,
  job: CancellableJob,
  token: string,
  onError: (message: string) => void,
): void {
  if (!button) return;
  if (job.job_type === 'clear') {
    button.classList.add('hidden');
    button.onclick = null;
    return;
  }
  button.classList.remove('hidden');
  button.disabled = false;
  button.textContent = 'Cancel';
  button.onclick = async () => {
    button.disabled = true;
    button.textContent = 'Cancelling…';
    try {
      const response = await fetch(`/api/proxy/sync/${job.id}/cancel`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error(`Unable to cancel job (HTTP ${response.status})`);
      // Leave the button disabled until polling observes the cooperative stop.
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Cancel';
      onError(error instanceof Error ? error.message : 'Unable to cancel job');
    }
  };
}
