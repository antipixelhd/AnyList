/** Discovery metadata follows the admin's logged-out-navigation gate. */
export function anonymousGameDiscoveryRoute(method:string,path:string){
  if(method==='GET'||method==='HEAD')return /^\/(?:browse\/games|discover-game|api\/proxy\/tracking\/games\/(?:search|browse(?:\/facets)?))\/?$/.test(path);
  return method==='POST'&&/^\/api\/proxy\/tracking\/games\/resolve\/[1-9][0-9]*\/?$/.test(path);
}
