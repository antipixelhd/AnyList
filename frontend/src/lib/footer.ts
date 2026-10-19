/** Publication controls navigation visibility; dev routes remain directly accessible. */
export function footerLinks(pathname: string, published: boolean) {
  const draftPage = pathname.startsWith('/dev/');
  const prefix = draftPage ? '/dev' : '';
  return {
    brand: published ? `${prefix}/about` : '/',
    links: published ? [
      ...(draftPage ? [{href: '/dev/legal-notice', label: 'Legal notice'}] : []),
      {href: `${prefix}/privacy`, label: 'Privacy'},
      {href: `${prefix}/credits`, label: 'Credits & licenses'},
    ] : [],
  };
}
