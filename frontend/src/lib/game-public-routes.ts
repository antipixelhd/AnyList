/** Routing reachability only. Backend approval is checked independently. */
export function anonymousGameDiscoveryRoute(method:string,path:string){
  if(method==='GET'||method==='HEAD')return /^\/(?:browse\/games|discover-game|api\/proxy\/tracking\/games\/(?:search|browse(?:\/facets)?))\/?$/.test(path);
  return method==='POST'&&/^\/api\/proxy\/tracking\/games\/resolve\/[1-9][0-9]*\/?$/.test(path);
}

/** Restricted documents require a current server response on navigation. */
export function approvedGamesPage(url:URL){
  return /^\/(?:games(?:\/|$)|browse\/games\/?$|discover-game\/?$|user\/[^/]+\/games\/?$)/.test(url.pathname)
    || /^\/(?:actors|person|studio)\//.test(url.pathname)&&url.searchParams.get('media_type')==='game';
}
