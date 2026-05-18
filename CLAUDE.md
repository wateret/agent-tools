# agent-tools

## Plugin updates: bump version on commit

When committing changes to any plugin under `plugins/`, bump the `version` field in that plugin's `.claude-plugin/plugin.json` as part of the same commit.

- Patch bump (e.g. `0.1.1` → `0.1.2`): bug fixes, hook tweaks, doc-only changes that ship with the plugin
- Minor/Major bump: done manually

If the plugin has no `version` field yet, add one starting at `0.1.0`. Stage the manifest change together with the rest of the commit — never ship a plugin change without bumping.
