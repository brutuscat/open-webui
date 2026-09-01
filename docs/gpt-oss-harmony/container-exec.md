# Enable isolated `container.exec`

Use this only with the isolated Harmony profile. It does not start, stop, reconfigure, or share state with `GPTOSS20B` or the installed OpenWebUI service.

## Prerequisites

- Docker Desktop and Docker Compose are available.
- The isolated Harmony llama.cpp server is already listening on `127.0.0.1:8082`.
- You are working from this branch, not an installed OpenWebUI checkout.
- Choose a new, high-entropy Open Terminal API key and a separate OpenWebUI secret; do not commit either.

## Start the isolated profile

```powershell
Set-Location .\tools\gpt-oss-harmony
Copy-Item .\terminal.env.example .\terminal.env
# Edit terminal.env and replace the placeholder API key.
$env:HARMONY_WEBUI_SECRET_KEY = '<new isolated secret>'
$env:HARMONY_LLAMA_API_KEY = '<isolated llama key, if configured>'
docker compose --env-file terminal.env -f .\docker-compose.harmony.yaml up -d --build
```

The profile publishes only `127.0.0.1:3002`, creates separate Docker volumes, and keeps Open Terminal on a private Docker network without a published host port.

## Configure the isolated UI

At `http://127.0.0.1:3002`, add the isolated model endpoint and set exactly this model metadata:

```json
{
  "capabilities": {
    "gpt_oss_harmony_native_tools": true
  }
}
```

Add an enabled system Open Terminal connection in the isolated profile only:

- URL: `http://open-terminal-harmony:8000`
- Authentication: bearer token
- Token: the value in `HARMONY_OPEN_TERMINAL_API_KEY`

Select that terminal in the specific Harmony chat. Only then, and only when OpenWebUI's normal terminal authorization permits it, the native `container.exec` schema is exposed. The adapter delegates to OpenWebUI's existing `run_command` tool; it never starts a host shell.

## Verify

Run the focused unit tests from the repository root with the project's normal backend test environment:

```powershell
pytest -q backend/tests/unit/integrations/test_gpt_oss_harmony_browser.py backend/tests/unit/integrations/test_gpt_oss_harmony_terminal_workspace.py
```

In the isolated chat, ask for a harmless command such as `pwd`. Confirm that the selected terminal executes it, then disconnect the terminal to confirm that `container.exec` is no longer offered.

To shut down this disposable isolated profile, run `docker compose --env-file terminal.env -f .\docker-compose.harmony.yaml down` from `tools/gpt-oss-harmony`. Do not run volume removal commands unless you intentionally want to discard the isolated test data.
