/** Routing reachability only. Backend approval is checked independently. */
export function anonymousGameDiscoveryRoute(method:string,path:string){
  if(method==='GET'||method==='HEAD')return /^\/(?:browse\/games|discover-game|api\/proxy\/tracking\/games\/(?:search|browse(?:\/facets)?))\/?$/.test(path);
  return method==='POST'&&/^\/api\/proxy\/tracking\/games\/resolve\/[1-9][0-9]*\/?$/.test(path);
}

/** Restricted routes require fresh authorization before navigation reuse. */
export function approvedGamesPage(url:URL){
  return /^\/(?:games(?:\/|$)|browse\/games\/?$|discover-game\/?$|user\/[^/]+\/games\/?$)/.test(url.pathname)
    || /^\/(?:actors|person|studio)\//.test(url.pathname)&&(
      url.searchParams.get('media_type')==='game'||url.searchParams.get('domain')==='game');
}

/** Bounded tab snapshots may be reused after fresh approval/profile checks. */
export function reusableGamesPage(url:URL){
  return /^\/(?:browse\/games|user\/[^/]+\/(?:games|stats\/games))\/?$/.test(url.pathname);
}
