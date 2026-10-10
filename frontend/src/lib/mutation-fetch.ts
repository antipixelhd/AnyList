/** Browser connectivity hints cannot establish whether our server is reachable. */
export function mutationFetch(
  fetcher: typeof fetch,
  origin: string,
  onSuccess: () => void,
  onFailure: () => void,
): typeof fetch {
  return async (input, init) => {
    const request = input instanceof Request ? input : null;
    const method = String(init?.method || request?.method || 'GET').toUpperCase();
    const url = new URL(request?.url || String(input), origin);
    const mutation = method !== 'GET' && method !== 'HEAD'
      && url.origin === new URL(origin).origin && url.pathname.startsWith('/api/proxy/');
    let response: Response;
    try {
      response = await fetcher(input, init);
    } catch (error) {
      if (mutation && !(error instanceof Error && error.name === 'AbortError')) onFailure();
      throw error;
    }
    if (mutation && response.ok) onSuccess();
    return response;
  };
}
