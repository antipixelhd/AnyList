/** Route-local state for retained profile panels, including detached panels. */
export function profilePanelKey(url:URL){
  const match=url.pathname.match(/^\/user\/([^/]+)\/(movies|series|list|games|stats(?:\/games)?)\/?$/);
  return match?{username:decodeURIComponent(match[1]),key:match[2]}:undefined;
}
export function panelActive(root:HTMLElement){return root.isConnected&&!root.closest('[hidden],[inert]');}
export function panelUrl(root:HTMLElement){if(panelActive(root))return location.href;return root.closest<HTMLElement>('[data-profile-url]')?.dataset.profileUrl||location.href;}
export function setPanelUrl(root:HTMLElement,url:string|URL,push=false){
  const panel=root.closest<HTMLElement>('[data-profile-url]');
  if(panel)panel.dataset.profileUrl=String(url);
  if(panelActive(root)){
    if(push)history.pushState(history.state,'',url);else history.replaceState(history.state,'',url);
  }
}
