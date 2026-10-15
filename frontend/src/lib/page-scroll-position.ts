/** Fixed-body modal locks keep the page offset in `top`, not window.scrollY. */
export function pageScrollPosition(scrollY:number,style:Pick<CSSStyleDeclaration,'position'|'top'>) {
  const lockedTop=Number.parseFloat(style.top);
  return Math.max(0,style.position==='fixed'&&Number.isFinite(lockedTop)?-lockedTop:scrollY);
}
