# coming-soon

The placeholder served at wasthebusontime.com until launch. Plain static files, no build step: Cloudflare Pages publishes this branch as it is (build command empty, output directory `/`).

- `index.html`: the placeholder page.
- `unavailable/index.html`: where visitors outside the US and Canada are redirected. Inline CSS and icon, so it loads nothing else.
- `404.html`: shown for any other path.
- `_headers`: security headers, the same set the real site uses at launch, plus `X-Robots-Tag: noindex` for this placeholder only.

The site itself is built from the `main` branch. At launch, the Pages production branch switches to `main` and this branch is deleted.

Unofficial. Not affiliated with or endorsed by Intercity Transit.
