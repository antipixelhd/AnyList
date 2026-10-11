export type HealthAction = 'metadata' | 'characters' | 'statistics';
export interface HealthExample { id: number; name: string; detail: string; href: string | null }
export interface HealthCheck {
  code: string; label: string; description: string; severity: 'warning' | 'info';
  count: number; unit: string; action: HealthAction | null; examples: HealthExample[];
}
export interface HealthJob {
  id: number; action: HealthAction;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'interrupted';
  step: string | null; completed_steps: number; total_steps: number;
  results: Record<string, Record<string, number>>; error: string | null; updated_at: string;
}
export interface DataHealth {
  checked_at: string; tracked_titles: number; catalogue_records: number;
  checks: HealthCheck[]; job: HealthJob | null;
}
export const healthActions: { action: HealthAction; label: string; description: string }[] = [
  { action: 'metadata', label: 'Fill missing metadata', description: 'Recover missing title metadata, country, runtimes and character artwork in bounded batches. Provider limits and retry delays are respected.' },
  { action: 'characters', label: 'Repair character links', description: 'Rebuild missing actor–character mappings from existing credits.' },
  { action: 'statistics', label: 'Refresh statistics', description: 'Request a one-off refresh for up to 20 accounts. The normal daily schedule stays in place.' },
];
export function isHealthJobActive(job: HealthJob | null): boolean {
  return job?.status === 'pending' || job?.status === 'running';
}
export function healthSummary(checks: HealthCheck[]) {
  return {
    attention: checks.filter(check => check.count > 0 && check.severity === 'warning').length,
    information: checks.filter(check => check.count > 0 && check.severity === 'info').length,
    clear: checks.filter(check => check.count === 0).length,
  };
}
export function healthJobMessage(job: HealthJob): string {
  if (job.error) return job.error;
  if (job.status === 'pending') return 'Repair queued';
  if (job.status === 'running') return job.step || 'Repair running';
  const failures = Object.values(job.results).reduce((sum, result) => sum + (result.failed || 0), 0);
  return failures ? `Batch finished with ${failures} failed requests. Existing data was preserved.` : 'Batch completed';
}
