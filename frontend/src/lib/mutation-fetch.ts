/** Browser connectivity hints cannot establish whether our server is reachable. */
export function mutationFetch(
  fetcher: typeof fetch,
  origin: string,
  onSuccess: () => void,
  onFailure: (error: unknown) => void,
): typeof fetch {
  return async (input, init) => {
    const request = input instanceof Request ? input : null;
    const method = String(init?.method || request?.method || 'GET').toUpperCase();
    const url = new URL(request?.url || String(input), origin);
    // Identity batches read projections; optional portrait acquisition does not
    // change viewer state or invalidate unrelated navigation/search results.
    const readOnlyPost = method === 'POST' && [
      '/api/proxy/tracking/browse/context',
      '/api/proxy/tracking/games/state',
      '/api/proxy/tracking/games/people/images',
    ].includes(url.pathname);
    const mutation = method !== 'GET' && method !== 'HEAD'
      && !readOnlyPost && url.origin === new URL(origin).origin && url.pathname.startsWith('/api/proxy/');
    let response: Response;
    try {
      response = await fetcher(input, init);
    } catch (error) {
      if (mutation && !(error instanceof Error && error.name === 'AbortError')) onFailure(error);
      throw error;
    }
    if (mutation && response.ok) onSuccess();
    return response;
  };
}
