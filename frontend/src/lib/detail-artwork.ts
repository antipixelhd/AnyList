import { artworkIdentity, responsiveArtwork, type ArtworkRoute, type ResponsiveArtworkAttributes } from './responsive-artwork.ts';

/** Related cards use the same provider buckets and disk policy as Browse. */
export function detailWorkArtwork(path: string | null | undefined, similar = false, route: ArtworkRoute = 'auto') {
  return responsiveArtwork(path, {
    size: 'w342',
    sizes: similar ? '(max-width:650px) calc((100vw - 50px) / 2), 65px' : '(max-width:650px) 74px, 62px',
    route,
  });
}

/** When the preview shows the hero, reuse its exact responsive sources. */
export function detailSceneArtwork(path: string | null | undefined, hero: ResponsiveArtworkAttributes | null, route: ArtworkRoute = 'auto') {
  if (path && hero && artworkIdentity(path) === artworkIdentity(hero.src)) return hero;
  return responsiveArtwork(path, {size: 'w780', route});
}
