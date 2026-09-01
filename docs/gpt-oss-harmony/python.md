# Enable isolated native `python`

Use this only with the isolated Harmony profile. It adds a private, token-authenticated Jupyter service and does not change Guardian or the installed OpenWebUI service.

## Start the profile

From `tools/gpt-oss-harmony`, create or update the ignored local environment file with distinct values for `HARMONY_OPEN_TERMINAL_API_KEY` and `HARMONY_JUPYTER_TOKEN`, then run:

```powershell
docker compose --env-file .\terminal.env `
  -f .\docker-compose.harmony.yaml `
  -f .\docker-compose.repo-browser.yaml `
  -f .\docker-compose.python.yaml up -d --build
```

The Python service is reachable only as `http://harmony-jupyter:8888` from the isolated OpenWebUI container. It has no published host port, no host mount, no Docker socket, and no external network. The selected P1 `/home/user` volume is shared with Open Terminal so files can move between the two tools; Python variables are reset for every call.

## Model settings

For the isolated Harmony model, retain:

```json
{
  "capabilities": {
    "gpt_oss_harmony_native_tools": true,
    "code_interpreter": true
  },
  "builtin_tools": {
    "code_interpreter": true
  }
}
```

The standard OpenWebUI Code Interpreter permission and model capability must remain enabled. The Harmony adapter exposes bare `python` only when the server-side engine is authenticated Jupyter; it never falls back to Pyodide or a host shell.

## Verify

In a chat using the isolated Harmony model, run a harmless calculation. The model-facing recipient must be `python`, not `functions.python`, and the returned result should continue the same response. The isolated contract runner covers output, exceptions, timeout, Unicode, statelessness, shared files, output limits, image results, egress blocking, and workspace confinement.

To remove Python while retaining terminal/repo tools, stop and recreate the profile without `docker-compose.python.yaml`; do not remove the named volume unless its contents are intentionally disposable.
