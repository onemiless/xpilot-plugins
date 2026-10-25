# Contributing to xpilot-plugins

This repository holds the store plugins for [xpilot](https://github.com/onemiless/xpilot). Read `XPILOT.md` in the
xpilot repository (plugin interfaces, claims, code layout) and this repository's `README.md` first.

## Which repository

- **Here**: plugin code (`plugins/<id>/`), its tests, `dist/` and `index.json`.
- **onemiless/xpilot**: the host, the store, the UI, the core plugins and panda safety code.
  CAN senders require host/firmware runtime rule ABI 1 support merged there first. Declare `rules` and
  `requires.rule_abi` here; legacy `safety` manifests are rejected.

## Pull requests

1. Fork, branch from `main`, add or change `plugins/<id>/`. Bump `version` in `plugin.json` when you change a published plugin.
2. Run `python3 tools/build_index.py` and commit `dist/` and `index.json` with the source.
3. Run the plugin tests and the store end to end check against an xpilot checkout (see `.github/workflows/ci.yml`).
   CI runs the same and must pass before merge.
4. In the pull request, describe what the plugin changes on the road, which claims it takes, and how you tested it
   (host tests, device).

## Automatic merge

When CI passes, `.github/workflows/automerge.yml` squash-merges a pull request from a collaborator if `tools/pr_risk.py`
finds only display plugins (category `ui`/`overlays`, no claims, no safety/rules, no platform, no binaries, only the UI
host API and a few stdlib imports, no dynamic code). Anything else, or any outside author, gets one comment asking the
owner to review. A failing check on `main` after a merge opens an issue. `tools/pr_risk_check.py` lists the failure
modes the classifier is tested against.
