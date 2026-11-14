export const returnViewLifetime = 5 * 60_000;
export const returnViewFreshness = 2 * 60_000;
const maxReturnViewBytes = 2 * 1024 * 1024;

export function createReturnViewCache(now = () => Date.now(), budget = 6 * 1024 * 1024, limit = 8, scheduleExpiry = false) {
  type Entry = {html:string; bytes:number; expires:number; dirty:boolean};
  const entries = new Map<string, Entry>();
  let bytes = 0;
  let viewer = '';
  let expiryTimer:ReturnType<typeof setTimeout>|undefined;
  const remove = (key:string) => {
    const entry = entries.get(key);
    if (!entry) return;
    bytes -= entry.bytes;
    entries.delete(key);
  };
  const clear = () => { clearTimeout(expiryTimer); expiryTimer=undefined; entries.clear(); bytes = 0; };
  const prune = () => {
    const time = now();
    for (const [key, entry] of entries) if (entry.expires <= time) remove(key);
  };
  function expireNext(){
    if(expiryTimer!==undefined||!entries.size)return;
    const next=Math.min(...[...entries.values()].map(entry=>entry.expires));
    expiryTimer=setTimeout(()=>{
      expiryTimer=undefined;
      prune();
      expireNext();
    },Math.max(0,next-now()));
  }
  return {
    scope(identity:string) {
      if (viewer === identity) return;
      clear();
      viewer = identity;
    },
    get(key:string) {
      prune();
      const entry = entries.get(key);
      if (!entry) return;
      entries.delete(key);
      entries.set(key,entry);
      return entry;
    },
    set(key:string, html:string) {
      prune();
      remove(key);
      const size = new Blob([html]).size;
      if (size > Math.min(budget,maxReturnViewBytes)) return false;
      entries.set(key,{html,bytes:size,expires:now()+returnViewLifetime,dirty:false});
      bytes += size;
      while (bytes > budget || entries.size > limit) remove(entries.keys().next().value!);
      if(scheduleExpiry)expireNext();
      return true;
    },
    markDirty() {
      for (const entry of entries.values()) entry.dirty = true;
    },
    clear,
    prune,
  };
}

export const returnViews = createReturnViewCache(undefined,undefined,undefined,typeof window !== 'undefined');

function isBrowseHome(url:URL) {
  return /^\/browse\/?$/.test(url.pathname)&&!url.search&&!url.hash;
}

export function browseReturnTarget(url:URL, lastBrowse:string|undefined) {
  return lastBrowse&&isBrowseHome(url)?new URL(lastBrowse):undefined;
}

export function returnViewKey(url:URL) {
  const params = new URLSearchParams(url.search);
  params.sort();
  return url.pathname.replace(/\/$/,'') + (params.size ? '?' + params : '');
}

export function returnViewIdentity(body:HTMLElement) {
  return `${body.dataset.cacheUser || 'anonymous'}:${body.dataset.gamesApproved || 'false'}`;
}

export function isReturnViewTrackingWrite(url:URL) {
  return /^\/api\/proxy\/tracking\/(?:entry\/|games\/(?:entries|children|favorites)\/)/.test(url.pathname);
}

export type CaptureReturnView = {
  source:URL;
  save(url:URL, apply:(content:HTMLElement, body:HTMLElement, title:HTMLTitleElement|null)=>boolean):void;
};

export function writeReturnSeed(target:HTMLElement, selector:string, value:unknown) {
  target.querySelector(selector)!.textContent = JSON.stringify(value).replaceAll('<','\\u003c');
}

/** Preserve live form values when the snapshot is parsed into a fresh document. */
export function copyReturnForm(source:HTMLElement, target:HTMLElement) {
  const controls = [...source.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input,select')];
  target.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input,select').forEach((control,index) => {
    const live = controls[index];
    if (control instanceof HTMLInputElement) {
      if (control.type === 'password') return;
      control.setAttribute('value', live.value);
      if (live instanceof HTMLInputElement && ['checkbox','radio'].includes(live.type)) {
        control.toggleAttribute('checked',live.checked);
      }
    } else {
      [...control.options].forEach(option => option.toggleAttribute('selected',option.value === live.value));
    }
  });
}
