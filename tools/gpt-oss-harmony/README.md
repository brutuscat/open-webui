# Isolated Harmony runtime support

These files make the exact, non-production Docker/Open Terminal setup visible in the OpenWebUI fork-review PR.

## Files

- `docker-compose.harmony.yaml` starts the loopback-only OpenWebUI profile and a private Open Terminal service.
- `docker-compose.repo-browser.yaml` adds the optional P1 named-volume workspace and root-confined patch helper.
- `docker-compose.repo-browser-bind.yaml.example` shows the alternative one-repository bind mount without committing a host path.
- `open-terminal/apply_patch.py` is the standard-library-only helper installed only in the optional derived image.
- `terminal.env.example` declares the local secrets needed by the profile.

Use [the container.exec guide](../../docs/gpt-oss-harmony/container-exec.md) first. These are fork-review assets, not a request to change production service configuration.
