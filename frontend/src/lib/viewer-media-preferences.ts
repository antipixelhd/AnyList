import {tracker} from './tracker';
import {mediaDomains,type MediaDomains} from './media-domains';

/** One private read shared by components in this SSR request, never across viewers. */
export function viewerMediaPreferences(locals:App.Locals):Promise<MediaDomains>{
  return locals.mediaPreferences??=locals.token
    ?tracker('preferences',locals.token).then(mediaDomains).catch(()=>mediaDomains(null))
    :Promise.resolve(mediaDomains(null));
}
