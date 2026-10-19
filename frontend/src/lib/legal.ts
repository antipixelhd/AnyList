export interface LegalConfig {
  operator_name: string; operator_address: string; contact_email: string;
  hosting_details: string; retention_details: string; transfer_details: string;
}

export async function legalConfig(): Promise<LegalConfig> {
  const empty = {operator_name:'', operator_address:'', contact_email:'', hosting_details:'', retention_details:'', transfer_details:''};
  try {
    const response = await fetch(`http://127.0.0.1:${process.env.BACKEND_PORT || '7331'}/legal/config`, {signal:AbortSignal.timeout(3000)});
    if (!response.ok) return empty;
    const data = await response.json();
    return Object.fromEntries(Object.keys(empty).map(key => [key, typeof data[key] === 'string' ? data[key] : ''])) as unknown as LegalConfig;
  } catch { return empty; }
}

export function legalDraft(config: LegalConfig): boolean {
  return Object.values(config).some(value => !value.trim());
}
