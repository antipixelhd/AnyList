/**
 * Shared responsive image URL handling for provider artwork.
 *
 * TMDB exposes a fixed set of width buckets.  Keeping the bucket list here
 * means server-rendered Astro components and browser-rendered content build
 * the same `srcset`.  Providers with no equivalent width API (TVDB, RPDB,
 * and arbitrary URLs) intentionally produce only a `src`.
 */

// Keep this list in sync with the backend image cache's supported TMDB
// buckets.  Asking the proxy for any other width would produce a 400.
export const ARTWORK_WIDTHS = [92, 154, 185, 300, 342, 500, 780, 1280] as const;

export type ArtworkRoute = "proxy" | "direct" | "auto";

export interface ResponsiveArtworkOptions {
  /** Desired fallback/source bucket, such as `w500` or `w1280`. */
  size?: string;
  /** CSS image width expression, passed through as the `sizes` attribute. */
  sizes?: string;
  /** Public provider images use their CDN; proxy is an explicit opt-in. */
  route?: ArtworkRoute;
}

export interface ResponsiveArtworkAttributes {
  src: string;
  srcset?: string;
  sizes?: string;
}

interface ParsedArtwork {
  kind: "tmdb" | "tvdb" | "igdb" | "imdb" | "other";
  suffix?: string;
  raw: string;
}

const TMDB_HOST = "image.tmdb.org";
const TVDB_HOST = "artworks.thetvdb.com";
const PROXY_PREFIX = "/api/proxy/media/image/";

function suffixWithQuery(pathname: string, search = "", hash = "") {
  return `${pathname}${search}${hash}`;
}

function parseArtwork(path: string): ParsedArtwork {
  if(path.startsWith('//images.igdb.com/'))return parseArtwork(`https:${path}`);
  // The frontend's backend image route contains the TMDB size bucket in the
  // path.  Rating-poster URLs deliberately do not match this expression, so
  // they remain one source URL.
  if (path.startsWith(PROXY_PREFIX)) {
    const remainder = path.slice(PROXY_PREFIX.length);
    const match = /^(w\d+|h\d+|original)(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(remainder);
    if (match) {
      return {
        kind: "tmdb",
        suffix: suffixWithQuery(match[2] ?? "", match[3] ?? "", match[4] ?? ""),
        raw: path,
      };
    }

    const tvdb = /^tvdb(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(remainder);
    if (tvdb) {
      return {
        kind: "tvdb",
        suffix: suffixWithQuery(tvdb[1] ?? "", tvdb[2] ?? "", tvdb[3] ?? ""),
        raw: path,
      };
    }

    return { kind: "other", raw: path };
  }

  // The rating-poster endpoint and other application URLs are already final
  // image sources. They must not be interpreted as bare TMDB file paths.
  if (path.startsWith("/api/") || path.startsWith("/icons/") || path.startsWith("/profile/")) {
    return { kind: "other", raw: path };
  }

  if (path.startsWith("http://") || path.startsWith("https://")) {
    try {
      const url = new URL(path);
      const pathname = url.pathname;
      if(pathname.startsWith(PROXY_PREFIX)&&typeof location!=='undefined'&&url.origin===location.origin)return parseArtwork(`${pathname}${url.search}${url.hash}`);
      if (url.hostname.toLowerCase() === TMDB_HOST) {
        const match = /^\/t\/p\/[^/]+(\/.*)?$/i.exec(pathname);
        if (match) {
          return {
            kind: "tmdb",
            suffix: suffixWithQuery(match[1] ?? "", url.search, url.hash),
            raw: path,
          };
        }
      }
      if (url.hostname.toLowerCase() === TVDB_HOST) {
        return {
          kind: "tvdb",
          suffix: suffixWithQuery(url.pathname, url.search, url.hash),
          raw: path,
        };
      }
      if(url.hostname.toLowerCase()==='images.igdb.com'){
        const match=/^\/igdb\/image\/upload\/t_[^/]+(\/[^/]+)$/.exec(url.pathname);
        if(match)return {kind:'igdb',suffix:suffixWithQuery(match[1],url.search,url.hash),raw:path};
      }
      if(url.hostname==='m.media-amazon.com'&&url.protocol==='https:'&&!url.search&&!url.hash){
        // Retain the provider's crop; remove only our verified resize/quality
        // modifiers so cards and the portrait viewer share one asset identity.
        const match=/^(\/images\/M\/[^/]+\._V1[^.]*?)\.(jpg|jpeg|png)$/.exec(url.pathname);
        if(match)return {kind:'imdb',suffix:`${match[1].replace(/_(?:QL\d+|UX\d+)(?=_|$)/g,'').replace(/_+$/,'')}_.${match[2]}`,raw:path};
      }
    } catch {
      // Keep malformed or unsupported absolute URLs as-is below.
    }
    return { kind: "other", raw: path };
  }

  // A leading slash is the shape returned by the TMDB API.  The API helper
  // historically also accepted a path without the slash, so retain that
  // behavior for callers that pass a bare filename/path.
  if (path.startsWith("/") && !path.startsWith("//")) {
    return { kind: "tmdb", suffix: path, raw: path };
  }
  if (!path.includes(":") && !path.startsWith("//")) {
    const normalized = path.startsWith("/") ? path : `/${path}`;
    return { kind: "tmdb", suffix: normalized, raw: path };
  }

  return { kind: "other", raw: path };
}

function widthFromSize(size: string): number | null {
  const match = /^w(\d+)$/i.exec(size);
  return match ? Number(match[1]) : null;
}

/** One identity across provider buckets and the optional local disk proxy. */
export function artworkIdentity(path:string):string {
  const parsed=parseArtwork(path);
  return parsed.kind==='other'?parsed.raw:`${parsed.kind}:${parsed.suffix}`;
}

function candidateWidths(size: string): number[] {
  const requested = widthFromSize(size);
  if (requested == null || !(ARTWORK_WIDTHS as readonly number[]).includes(requested)) return [];
  return [...ARTWORK_WIDTHS];
}

function buildUrl(route: ArtworkRoute, size: string, suffix: string): string {
  if (route === "direct") return `https://${TMDB_HOST}/t/p/${size}${suffix}`;
  return `${PROXY_PREFIX}${size}${suffix}`;
}

/**
 * Build an image source and, for TMDB artwork, responsive width candidates.
 * `src` remains a valid fallback even when the caller omits `sizes`.
 */
export function responsiveArtwork(
  path: string | null | undefined,
  options: ResponsiveArtworkOptions = {},
): ResponsiveArtworkAttributes | null {
  if (!path) return null;

  const parsed = parseArtwork(path);
  const route=options.route==='proxy'?'proxy':'direct';
  if(parsed.kind==='imdb'){
    const original=`https://m.media-amazon.com${parsed.suffix}`;
    const url=(width:number)=>original.replace(/_\.(jpg|jpeg|png)$/,`_QL75_UX${width}_.$1`);
    const width=widthFromSize(options.size||'w500');
    if(options.size==='original')return {src:original};
    const bucket=width!=null&&width<=185?185:width!=null&&width<=342?342:780;
    return {src:url(bucket),...(options.sizes?{sizes:options.sizes,
      srcset:[185,342,780].map(value=>`${url(value)} ${value}w`).join(', ')}:{})};
  }
  if(parsed.kind==='igdb'){
    const width=widthFromSize(options.size||'w500');
    const bucket=options.size==='original'?'1080p':width!=null&&width<=185?'cover_small_2x':width!=null&&width<=500?'cover_big_2x':'720p';
    return {src:`https://images.igdb.com/igdb/image/upload/t_${bucket}${parsed.suffix}`};
  }
  if (parsed.kind === "tvdb") {
    return {src:route==='proxy'?`${PROXY_PREFIX}tvdb${parsed.suffix??''}`:`https://${TVDB_HOST}${parsed.suffix??''}`};
  }
  if (parsed.kind !== "tmdb") return { src: parsed.raw };

  const size = options.size ?? "w500";
  const src = buildUrl(route, size, parsed.suffix ?? "");
  const widths = candidateWidths(size);

  // `original`, height buckets, and other non-width sources do not have a
  // meaningful width-descriptor set to advertise.
  // Without a matching `sizes` value the browser treats the image as 100vw,
  // which can make a responsive candidate list choose an unnecessarily large
  // file.  Callers that want `srcset` must declare the rendered width.
  if (!widths.length || !options.sizes) return { src };

  const srcset = widths
    .map((width) => `${buildUrl(route, `w${width}`, parsed.suffix ?? "")} ${width}w`)
    .join(", ");
  const attributes: ResponsiveArtworkAttributes = { srcset, src };
  if (options.sizes) attributes.sizes = options.sizes;
  return attributes;
}

/** Apply responsive artwork attributes to a reusable browser image element. */
export function applyResponsiveArtwork(
  image: HTMLImageElement,
  path: string | null | undefined,
  options: ResponsiveArtworkOptions = {},
): HTMLImageElement {
  const attributes = responsiveArtwork(path, options);

  image.removeAttribute("srcset");
  image.removeAttribute("sizes");
  if (!attributes) {
    image.removeAttribute("src");
    return image;
  }

  // Assign the responsive hints before src so a browser does not start a
  // fallback request before it has the candidate list.
  if (attributes.srcset) image.setAttribute("srcset", attributes.srcset);
  if (attributes.sizes) image.setAttribute("sizes", attributes.sizes);
  image.setAttribute("src", attributes.src);
  return image;
}
