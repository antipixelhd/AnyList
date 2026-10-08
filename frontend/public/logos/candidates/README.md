# AnyList logo candidates

Each design has a high-resolution transparent PNG extraction and a clean,
resolution-independent SVG recreation. The SVGs are used in the development
navigation and favicons. Backgrounds, counters, and gaps are transparent.

| Candidate | Reference | Development preview |
| --- | --- | --- |
| Fold | 01 / Fold (attachment 4) | `/dev/details?design=folio` |
| Journal | 02 / Journal (attachment 2) | `/dev/details?design=marquee`, `/dev/cards` |
| Frame | 03 / Frame (attachment 1) | `/dev/details?design=marquee-25`, `/dev/details?design=marquee-27` |
| Monogram | 04 / Monogram (attachment 3) | `/dev/details?design=panorama` |

The main app retains its current branding while these candidates are compared.
The PNGs preserve the built-in imagegen output. SVG geometry was recreated from
the supplied references, with smooth paths and subtle cyan gradients.

## Extraction prompts

Built-in imagegen was used with `transparent_background: true` and each matching
attachment as the edit target. No CLI or API key was used.

Frame:

> Use case: background-extraction. Edit target: the supplied AnyList Frame brand board. Extract ONLY the large cyan icon from the upper left, without any wordmark or labels. Preserve exactly the rounded rectangular silhouette, two right-edge notches, and the triangular play cutout. Remove ALL background including inside the play cutout and right notches; real transparent alpha. Faithfully recreate as a crisp high-resolution clean logo with smooth antialiased edges and the original cyan color, removing noisy photographic texture. No new design, no extra outline, no shadows, no text. Center the single icon tightly with a small even transparent margin; render at highest available resolution. This is one icon, not a board.

The other three used the following prompt with their respective design description:

> Use case: background-extraction. Edit target: the supplied AnyList brand board. Extract ONLY the large cyan icon from upper left. [Design description below.] Remove all background including inside cutouts: real transparent alpha. Faithfully recreate as a crisp high-resolution clean logo with smooth antialiased edges and the original cyan palette, removing noisy photographic texture. No wordmark, labels, outlines, shadows, new design, or extra elements. Center the single icon tightly with a small even transparent margin; render at highest available resolution. This is one icon, not a board.

- Journal: Journal icon: two cyan book-page halves, sloped upper outer edges and rounded lower outer corners, separated by a narrow vertical transparent gap. Preserve the exact original silhouette and proportions.
- Monogram: Monogram icon: cyan angular AL letters separated by a diagonal transparent slit, with a triangular transparent counter inside A. Preserve the exact original silhouette and proportions.
- Fold: Fold icon: cyan ribbon A with slightly darker overlapping fold at upper left, triangular transparent counter, and detached triangular folded tip lower right separated by a diagonal transparent slit. Preserve the exact original silhouette, proportions, and cyan tonal fold treatment.
