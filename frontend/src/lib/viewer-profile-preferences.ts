import {api} from './api';

/** Private profile data is shared only inside this authenticated SSR request. */
export function viewerProfilePreferences(locals:App.Locals) {
  return locals.profilePreferences??=locals.token
    ?api.profile.get(locals.token).catch(()=>null)
    :Promise.resolve(null);
}
