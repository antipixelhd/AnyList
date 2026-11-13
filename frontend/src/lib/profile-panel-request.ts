export const profilePanelHeader = 'X-AnyList-Profile-Panel';
export const profilePagePath = /^\/user\/[^/]+(?:\/(?:movies|series|list|games|favorites|social|library|stats(?:\/games)?))?\/?$/;

export function profilePanelRequest(request:Request) {
  return request.method === 'GET' && profilePagePath.test(new URL(request.url).pathname) &&
    request.headers.get(profilePanelHeader) === '1';
}
