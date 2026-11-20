export type HealthAction = 'metadata' | 'characters' | 'statistics' | 'game_scan' | 'game_reconcile';
export interface GameScanCandidate {
  id: number; name: string; steam_app_id: string | null; igdb_id: string | null; duplicate_id: number | null;
  status: 'unknown' | 'match' | 'ambiguous' | 'unmatched' | 'excluded' | 'failed' | 'conflict' | 'reconciled' | 'already_linked';
  detail: string;
}
export interface GameScan { checked_at: string; next_after_id: number | null; candidates: GameScanCandidate[] }
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
  game_scan?: GameScan | null;
}
export interface DataHealth {
  checked_at: string; tracked_titles: number; catalogue_records: number;
  checks: HealthCheck[]; job: HealthJob | null;
}
export const healthActions: { action: HealthAction; label: string; description: string }[] = [
  { action: 'game_scan', label: 'Scan orphaned games', description: 'Check up to 100 legacy game or Steam records against exact IGDB references. Review matches before reconciling.' },
  { action: 'metadata', label: 'Fill missing metadata', description: 'Recover missing title metadata, country, runtimes and character artwork in bounded batches. Provider limits and retry delays are respected.' },
  { action: 'characters', label: 'Repair character links', description: 'Rebuild missing actor–character mappings from existing credits.' },
  { action: 'statistics', label: 'Refresh statistics', description: 'Request a one-off refresh for up to 20 accounts. The normal daily schedule stays in place.' },
];
export function isHealthJobActive(job: HealthJob | null): boolean {
  return job?.status === 'pending' || job?.status === 'running';
}
export function canReconcileGames(job: HealthJob | null): boolean {
  return job?.action === 'game_scan' && job.status === 'completed'
    && !!job.game_scan?.candidates.some(row => row.status === 'match');
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
