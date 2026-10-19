/** Preview environments may link to drafts without publishing normal notice routes. */
export function footerLinks(pathname: string, published: boolean, previewSlot = '') {
  const draftPage = pathname.startsWith('/dev/');
  const drafts = draftPage || Boolean(previewSlot);
  const available = published || drafts;
  const prefix = draftPage || (!published && drafts) ? '/dev' : '';
  return {
    brand: available ? `${prefix}/about` : '/',
    links: available ? [
      ...(draftPage ? [{href: '/dev/legal-notice', label: 'Legal notice'}] : []),
      {href: `${prefix}/privacy`, label: 'Privacy'},
      {href: `${prefix}/credits`, label: 'Credits & licenses'},
    ] : [],
  };
}
