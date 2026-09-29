/// <reference path="../.astro/types.d.ts" />
/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    user: import('./lib/api').UserProfile | null;
    token: string | undefined;
    settings?: import('./lib/api').UserSettings;
    hasRpdbKey?: boolean;
  }
}

interface Window {
  showConfirm: (title: string, body: string) => Promise<boolean>;
  __HAS_RPDB__: boolean;
  ratingPosterUrl: typeof import('./lib/posters').ratingPosterUrl;
}
