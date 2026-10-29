/** Keep mounted cards, decoded images and focus while applying a Home snapshot. */
export function reconcileHomePanel(current:Element,fresh:Element) {
  const focused=current.contains(document.activeElement)?document.activeElement as HTMLElement:null;
  const key=(node:Node):string|null=>{
    if(node.nodeType!==1)return null;
    const element=node as Element;
    if(element.hasAttribute('data-progress-card'))return `series:${element.getAttribute('data-media-id')}`;
    if(element.hasAttribute('data-upcoming-card'))return `upcoming:${element.getAttribute('data-media-id')}:${element.getAttribute('data-season-number')}`;
    if(element.hasAttribute('data-game-playing-card'))return `game:${element.getAttribute('data-game-playing-card')}`;
    return element.id?`id:${element.id}`:null;
  };
  const compatible=(a:Node,b:Node)=>a.nodeType===b.nodeType&&(a.nodeType!==1||(a as Element).tagName===(b as Element).tagName);
  const patch=(target:Node,next:Node)=>{
    if(target.isEqualNode(next))return;
    if(target.nodeType!==1){target.nodeValue=next.nodeValue;return;}
    const element=target as Element,replacement=next as Element;
    for(const attribute of [...element.attributes])if(!replacement.hasAttribute(attribute.name))element.removeAttribute(attribute.name);
    for(const attribute of [...replacement.attributes])if(element.getAttribute(attribute.name)!==attribute.value)element.setAttribute(attribute.name,attribute.value);
    const remaining=[...element.childNodes];
    let anchor:ChildNode|null=element.firstChild;
    for(const child of [...replacement.childNodes]){
      const identity=key(child);
      const match=identity?remaining.find(node=>key(node)===identity&&compatible(node,child))
        :remaining.find(node=>!key(node)&&compatible(node,child));
      const node=match||child.cloneNode(true);
      if(match){remaining.splice(remaining.indexOf(match),1);patch(match,child);}
      if(node!==anchor)element.insertBefore(node,anchor);
      anchor=node.nextSibling;
    }
    remaining.forEach(node=>node.remove());
  };
  patch(current,fresh);
  if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
}
