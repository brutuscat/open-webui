# Updating OWU while preserving Harmony

Target reviewed on 2026-10-14: **Open WebUI v0.12.0**, release commit
`15818d88fd7bde1b73de33590f50d15d21e8d7c1`.

The current fork is v0.11.1 plus 20 Harmony commits, ending at
`d3d9663f4b2c761f50473301854c75dfcd6acaa3`. Its uncommitted Better Stack RUM,
Sentry destination, user identification, and sign-out changes must also be kept.

## Run

From the fork directory, in a normal PowerShell session with GitHub and Docker
access:

```powershell
.\tools\gpt-oss-harmony\upgrade.ps1 -Build
```

The runner makes a separate clone under the ignored `tools/gpt-oss-harmony/upgrades/`
directory, checkpoints tracked local edits and the explicitly allowed new files,
then merges the exact upstream release. The original branch and working files
stay in place. It does not push, replace a service, or access production data.
Unknown conflicts or untracked files stop preparation for review.

`-Build` builds the complete custom frontend/backend image and runs the Harmony
unit suite inside a disposable container with an empty temporary database, an
isolated static directory, no network, no host volumes, and no published ports.
Without `-Build`, only the source merge and preservation checks run; that is not
runtime qualification. A failed candidate is retained for diagnosis.

Optional flags: `-Destination <new-path>` and `-PythonPath <python.exe>`.
For monitoring in the image, supply `PUBLIC_BETTER_STACK_RUM_TOKEN` and
`PUBLIC_BETTER_STACK_SENTRY_DSN` as environment variables before building.
Ignored local environment files are not copied into the candidate.

## Reviewed integration changes

- Keep both Harmony's private `terminal_cwd` and upstream's `user_shell_tools`
  metadata. Losing the former disables the selected repository boundary.
- Keep native browser/container/repository/Python adapters, namespace activation,
  citation conversion, Jupyter header authentication, output bounds, tests,
  vendored browser files, and isolated runtime overlays.
- Exempt `native_harmony_hidden` tools from v0.12.0 Tool Search deferral. Their
  dispatcher schemas are needed for native calls and continuation even though
  the Harmony template hides them from the generic functions namespace.
- Combine Better Stack build arguments with upstream build-channel and slim-image
  handling. Upstream's `requirements*.txt` copy already includes our extra
  Harmony requirements; keep their separate install step.
- Preserve the monitoring tag, user association, sign-out reset, and documentation.

## Current verification and remaining work

The eight overlapping files were fetched from the pinned upstream tag and merged
against v0.11.1 and the current working tree in a temporary preview. There are
five reviewed conflict blocks in tools.py, Dockerfile, and .env.example. The
other overlapping files merge mechanically.

Offline resolution tests cover terminal metadata, Docker build options, refusal
of unknown conflicts, and native-tool deferral using the real upstream selector.
These checks do not establish that the complete v0.12.0 application works.

Run the runner's tests with Python 3.11 or 3.12:

```powershell
python -m unittest discover -s tools/gpt-oss-harmony -p test_upgrade.py -v
```

The real Tool Search selector test uses the upgraded checkout's source, or the
file supplied through `OWU_UPGRADE_TOOL_SEARCH_SOURCE` during offline review.
It is skipped on the original v0.11.1 checkout unless that file is supplied.

This task's sandbox cannot resolve GitHub from the terminal, write the shared
worktree Git index, access Docker, or control Windows services. The actual merge,
image build, complete Harmony unit suite, migrated-copy startup check, and live
GPT-OSS canaries remain pending. Existing-suite attempts were blocked by missing
dependencies or stalled during application startup and were stopped. No complete
application-suite pass is claimed. No upgrade has been deployed.

Before replacing a deployed instance, retain its image/package and a consistent
data backup, check the candidate against a disposable copy of its data, and run
native browser, container, repo_browser, and raw Python calls through the actual
Harmony-enabled llama.cpp backend. A stock OWU wheel or image does not contain
these fork changes. The Windows wheel service and isolated Docker profile are
different deployments; qualify and deploy the intended one explicitly.

Rollback must restore both the previous application artifact and its matching
data backup if new migrations have run. Never run an older version against a
newly migrated database without checking compatibility.

Sources: [upstream release](https://github.com/open-webui/open-webui/releases/tag/v0.12.0),
[pinned tool handling](https://github.com/open-webui/open-webui/blob/v0.12.0/backend/open_webui/utils/tools.py),
[pinned Tool Search](https://github.com/open-webui/open-webui/blob/v0.12.0/backend/open_webui/utils/tool_search.py).
