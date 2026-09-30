# Isolated Harmony runtime support

These files make the exact, non-production Docker/Open Terminal setup visible in the OpenWebUI fork-review PR.

## Files

- `docker-compose.harmony.yaml` starts the loopback-only OpenWebUI profile and a private Open Terminal service.
- `docker-compose.repo-browser.yaml` adds the optional P1 named-volume workspace and root-confined patch helper.
- `docker-compose.python.yaml` adds the optional isolated Jupyter service used by native `python`.
- `docker-compose.repo-browser-bind.yaml.example` shows the alternative one-repository bind mount without committing a host path.
- `open-terminal/apply_patch.py` is the standard-library-only helper installed only in the optional derived image.
- `jupyter/` pins and hardens the server-side Jupyter image; it has no published port or host mount.
- `terminal.env.example` declares the local secrets needed by the profile.

Use [the container.exec guide](../../docs/gpt-oss-harmony/container-exec.md) first. These are fork-review assets, not a request to change production service configuration.

For native Python, combine all three overlays and set the second Jupyter secret:

```powershell
docker compose --env-file .\terminal.env `
  -f .\docker-compose.harmony.yaml `
  -f .\docker-compose.repo-browser.yaml `
  -f .\docker-compose.python.yaml up -d --build
```

The Python adapter delegates to OpenWebUI's existing Jupyter Code Interpreter. It does not use Pyodide, a host shell, a Docker socket, or a browser-side kernel.
