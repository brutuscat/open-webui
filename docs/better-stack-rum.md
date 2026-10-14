# Better Stack real user monitoring

## Error tracking

The Better Stack browser tag uses Sentry's browser SDK internally. The frontend passes `PUBLIC_BETTER_STACK_SENTRY_DSN` to that SDK before initialization; do not load a separate `@sentry/browser` SDK on the same page.

Set `PUBLIC_BETTER_STACK_SENTRY_DSN` in the ignored `.env.local` for local frontend builds, or pass it into the frontend build environment. The DSN is embedded in browser code and is public by design. For Docker builds, pass both public identifiers:

```sh
docker build --build-arg PUBLIC_BETTER_STACK_RUM_TOKEN --build-arg PUBLIC_BETTER_STACK_SENTRY_DSN -t open-webui .
```

The Error Tracking DSN overrides the tag's internal Sentry destination while RUM continues using the browser-tag token. Installed Python-wheel services must receive a rebuilt/published frontend or have the served `index.html` updated; changing this checkout alone does not change the wheel's active frontend.

Set `PUBLIC_BETTER_STACK_RUM_TOKEN` in an ignored `.env.local` file or the frontend build environment, then run `npm run build`. An empty token leaves RUM disabled. The asynchronous JavaScript tag initializes with `environment: 'production'`.

For a Docker build, export the variable and forward it to the frontend build:

```sh
docker build --build-arg PUBLIC_BETTER_STACK_RUM_TOKEN -t open-webui .
```

The static frontend embeds the public browser-tag identifier at build time. Changing only the backend environment does not update the tag; rebuild the frontend after changing the token. Keeping deployment configuration out of Git does not hide the token from browsers.

For an installed Python-wheel service, check `FRONTEND_BUILD_DIR`: a separate checkout's build is not deployed automatically. Publish the frontend to the directory the service actually serves. This HTML-only tag can also be rendered into the existing packaged SPA `index.html`, preserving its compiled assets and retaining a rollback copy. A wheel upgrade can replace that file; reapply the tag or deploy a frontend built with the token afterward.

When a signed-in user loads in the root layout, Better Stack receives the Open WebUI user `id`, `email`, and `name` as `username`. Anonymous/loading states are not identified. Signing out clears the Better Stack user association. No plan, profile image, permission, or token fields are sent.

Better Stack uses Sentry's browser SDK internally. No separate Sentry SDK was found in this checkout's frontend source or dependency manifests. Check for a collision before adding a standalone browser Sentry SDK.

Verify the actual deployed app in a browser: `https://betterstack.net/b.js?t=...` must load, and requests to the application's Better Stack ingestion host must return successful responses.

See [JavaScript tag installation](https://betterstack.com/docs/rum/js-tag/installation/) and [Sentry collision guidance](https://betterstack.com/docs/errors/js-tag/install/).
