# Better Stack real user monitoring

Better Stack RUM is disabled unless `PUBLIC_BETTER_STACK_RUM_TOKEN` is supplied when building the frontend. Use the application token from the Frontend tab of the Better Stack application. Do not commit its value or put a private Better Stack management API token in this variable.

For a source build, set this variable in an untracked `.env.local` file or the build environment, then run `npm run build`. For a Docker build, export the variable in your build environment and pass it by name:

```sh
docker build --build-arg PUBLIC_BETTER_STACK_RUM_TOKEN -t open-webui .
```

Configure this through your deployment's variable or secret store. The static frontend embeds the value at build time; setting it only on a running backend container does not enable RUM. Rebuild after changing it. The JavaScript tag uses `environment: 'production'`.

The application token is visible in the deployed HTML and browser requests by design. Keeping it out of Git protects repository configuration; it cannot make a browser tag token confidential.

The tag uses Sentry's browser SDK internally. No existing Sentry SDK was detected in the checked dependency manifests, lockfiles, and frontend startup. Review this integration before adding a separate browser Sentry SDK.

Official documentation:

- [Install the JavaScript tag](https://betterstack.com/docs/rum/js-tag/installation/)
- [Management API tokens](https://betterstack.com/docs/rum/api/getting-api-token/)
