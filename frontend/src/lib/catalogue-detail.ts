export async function catalogueDetail(id: number, kind: 'game' | 'book', token?: string) {
  const response = await fetch(`http://127.0.0.1:${process.env.BACKEND_PORT || '7331'}/catalogue/entities/${id}/details`, {
    headers:token ? {Authorization:`Bearer ${token}`} : {},
  });
  if (!response.ok) throw new Error(response.status===404?'Not found.':'Unable to load this page. Please try again.');
  const detail = await response.json();
  if (detail.type !== kind) throw new Error('Not found.');
  return detail;
}
