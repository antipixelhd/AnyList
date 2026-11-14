const buildMeta = 'meta[name="anylist-build"]';

export function frontendBuild(document: Document) {
  return document.querySelector<HTMLMetaElement>(buildMeta)?.content;
}

export function frontendBuildChanged(current: Document, incoming: Document) {
  const previous = frontendBuild(current);
  const next = frontendBuild(incoming);
  return !!previous && !!next && previous !== next;
}

export class FrontendBuildChanged extends Error {}

export function routeAssetUnavailable(cause: unknown) {
  return cause instanceof Error && /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|unable to preload css/i.test(cause.message);
}

export function guardFrontendBuild(event: Event & {loader: () => Promise<void>; newDocument: Document; signal: AbortSignal; formData?: FormData}, current: Document) {
  if (event.formData) return;
  const load = event.loader;
  event.loader = async () => {
    await load();
    // ClientRouter falls back to a document navigation when preparation is canceled.
    if (!event.signal.aborted && frontendBuildChanged(current, event.newDocument)) event.preventDefault();
  };
}
