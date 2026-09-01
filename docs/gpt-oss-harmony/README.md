# GPT-OSS Harmony fork-review guide

This Draft-PR support area documents the full P0/P1 isolated Harmony implementation carried by this branch. It is deliberately reviewable in this fork; it is not an automatic upstream proposal or a production deployment guide.

## Included implementation

- W1: `backend/open_webui/integrations/gpt_oss_harmony/` provides the generic native-tool adapter seam.
- W2: native browser dispatch, citation handling, limits, tests, and the vendored GPT-OSS browser reference are included for fork review.
- W3: `terminal.py`, related dispatch/middleware logic, and terminal-workspace tests implement native `container.exec` through an authorized Open Terminal connection.
- P1: `repo_browser.py`, its tests, and the optional isolated runtime helper under `tools/gpt-oss-harmony/` implement bounded repository inspection and patching.

The runtime support files are intentionally separate from the OpenWebUI application source. They expose only a loopback isolated profile and never modify the installed OpenWebUI or the Guardian `GPTOSS20B` service.

## Status carried with this branch

- P1 was promoted for the isolated Harmony profile at stack commit `a910d94`; its deterministic repository contract passed 25/25 cases.
- The prior browser citation measurement remains 38/45, or 84.44%. It is below the 98% default-promotion gate. Nothing here claims production or browser-default promotion.
- The full source in this fork is preserved for review. A later upstream proposal would need to split and narrow it.

## Operator guides

- [Enable isolated `container.exec`](container-exec.md)
- [Enable the optional isolated P1 `repo_browser.*` profile](repo-browser.md)
- [Runtime support bundle](../../tools/gpt-oss-harmony/README.md)
