# Enable isolated P1 `repo_browser.*`

P1 is an isolated-profile feature only. It must not be pointed at Guardian, the installed OpenWebUI service, port 8080, or a production repository.

## Default: private named-volume workspace

Start from the [container.exec guide](container-exec.md), then use a new path below Open Terminal's private `/home/user` volume:

```powershell
Set-Location .\tools\gpt-oss-harmony
$env:HARMONY_REPO_BROWSER_ROOT = '/home/user/.harmony-p1-private'
docker compose --env-file terminal.env -f .\docker-compose.harmony.yaml -f .\docker-compose.repo-browser.yaml up -d --build
```

This derives a tiny image from the pinned Open Terminal image and adds only the root-confined `apply_patch.py` helper. The adapter declares `repo_browser.*` only if the authorized selected terminal reports exactly `HARMONY_REPO_BROWSER_ROOT` as its workspace root.

## Optional: one explicitly chosen host repository

For a real repository, copy `docker-compose.repo-browser-bind.yaml.example` outside version control, set one reviewed host path through `HARMONY_REPO_BROWSER_HOST_PATH`, and combine that copy with the base profile. The bind template mounts only that repository at `/workspace/repo`; never add a broad host mount or Docker socket.

## Validation record

The recorded isolated benchmark ran 25 deterministic generic-versus-native cases and passed 25/25. It covers tree/list/search/open, add/modify/delete/multi-hunk patch operations, rejected bad context, and traversal rejection. Mean recorded latency was 37.863 ms for generic and 37.146 ms for native calls.

Run the fork's focused source tests from the repository root:

```powershell
pytest -q backend/tests/unit/integrations/test_gpt_oss_harmony_repo_browser.py backend/tests/unit/integrations/test_gpt_oss_harmony_terminal_workspace.py
```

The separate browser citation result remains 84.44%; it is unrelated to the 25/25 repo contract and has not met the 98% browser-default gate.
