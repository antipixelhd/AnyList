import {tracker} from './tracker';

/** Reuse the full page snapshot; coalesce a fallback read within this SSR request. */
export function viewerNotifications(locals:App.Locals) {
  return locals.notifications??=locals.user && locals.token
    ?(locals.pageContext?.notifications ? Promise.resolve(locals.pageContext.notifications)
      :tracker('notifications',locals.token).catch(()=>null))
    :Promise.resolve(null);
}
