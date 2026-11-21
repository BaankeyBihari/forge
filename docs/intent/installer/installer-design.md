---
parent: high-level-design
prefix: INST
---

# Installer

## Context and Design Philosophy

The installer puts Forge where Claude Code finds it: the Commander and spoke skills, the agent profiles, the guard, and the permissions that let spokes write the state directory without prompting. It turns the repository's `claude/` tree into an install that keeps working when the checkout moves or changes, and that can be removed exactly.

Three rules shape it:

- **Copies, never links.** Every installed file is a regular file whose content was resolved from the checkout at install time. An edit in the checkout reaches a session only when the user reruns the installer.
- **One install per place it can collide.** Two guards on one tool call trip the duplicate-call circuit breaker, and mixed versions of skills and agents resolve unpredictably, so the installer refuses any install that would overlap an existing one.
- **Validate everything, then write.** Every check that can fail runs before the first file is written, so a refused install leaves the machine as it was.

`claude/installer.sh` is the entry point the user runs. It checks that `python3` is at least 3.11 and hands its arguments to `claude/install.py`, which does all the work with the Python standard library, like every other Forge script, and is tested with pytest.

## Command Line

```text
claude/installer.sh [--global | --project <repo>] [--plugin] [--preset <name> | --models <file>]
claude/installer.sh --uninstall [--project <repo>]
claude/installer.sh --vendor
```

| Option | Meaning |
|---|---|
| `--global` | Install into `~/.claude/` (the default scope) |
| `--project <repo>` | Install into `<repo>/.claude/`; `<repo>` must be the root of a git repository's main worktree |
| `--plugin` | Install as a Claude Code plugin instead of plain files; global scope only |
| `--preset <name>` | Model preset: `balanced`, `sonnet`, `opus`, `fable-opus`, `fable` |
| `--models <file>` | JSON map from agent name to model, applied over the preset |
| `--uninstall` | Remove the install of the given scope (`--global` when no `--project`) |
| `--vendor` | Clone every pin in `rev` into `.vendor/`; installs nothing |

`--plugin` with `--project`, `--uninstall` or `--vendor` with any install option, and an unknown option are usage errors: the installer prints usage and exits `2` having written nothing.

The state directory is `$FORGE_HOME` when that variable is set while the installer runs, and `~/.forge` otherwise, resolved to an absolute path. Everything the installer writes that names the state directory uses this one value.

## Source Tree

The installer reads only `claude/` and `rev`:

| Source | What it is |
|---|---|
| `claude/skills/forge/` | The Commander skill; `scripts/` links to `reference/scripts/` |
| `claude/skills/forge-*/` | Links to `reference/skills/forge-*/`; `forge-spoke/scripts` and `forge-memory/scripts` link to `reference/scripts/` |
| `claude/agents/forge-*.md` | Agent profile templates: name, description, tools, preloaded skills, a `model:` line, and the body |
| `claude/hooks/guard_paths.py` | The guard's `PreToolUse` adapter |

Copying follows every link, so each installed skill holds real files. `tests/` directories and `__pycache__/` are not copied. `guard_paths.py` is copied into the installed Commander skill's `scripts/`, beside `guard_core.py`, which it imports from its own directory.

## Install Modes and Targets

| | Plain files, global | Plain files, project | Plugin |
|---|---|---|---|
| Skills | `~/.claude/skills/forge*/` | `<repo>/.claude/skills/forge*/` | `<plugin>/skills/forge*/` |
| Agents | `~/.claude/agents/forge-*.md` | `<repo>/.claude/agents/forge-*.md` | `<plugin>/agents/forge-*.md` |
| Guard | `hooks:` in each agent's frontmatter | `hooks:` in each agent's frontmatter | `<plugin>/hooks/hooks.json` |
| Settings file | `~/.claude/settings.json` | `<repo>/.claude/settings.local.json` | `~/.claude/settings.json` |
| Hidden from git | — | each installed path listed in `.git/info/exclude` | — |

`<plugin>` is `$FORGE_HOME/plugin/forge/`. Claude Code reads a plugin from a local-directory marketplace in place rather than copying it into its plugin cache, so the installer builds the plugin outside the checkout and that build is the installed copy.

### Plain files

The guard hook in each generated profile is:

```yaml
hooks:
  PreToolUse:
    - hooks:
        - type: command
          command: FORGE_HOME='<state dir>' python3 '<skills>/forge/scripts/guard_paths.py'
```

with no matcher, so every tool call of the agent is checked, and with both paths absolute and shell-quoted. Every profile gets it, Council personas included: a nested agent runs its own frontmatter hooks, not its parent's.

Project scope writes only untracked files. Each installed top-level path (`.claude/skills/forge/`, `.claude/agents/forge-dev.md`, …) and `.claude/settings.local.json` are appended to `.git/info/exclude`, unless a line already covers them. Linked worktrees of the repository read the main checkout's untracked `.claude/skills/` and `.claude/agents/`, so one project install serves every Forge worktree.

Claude Code skips frontmatter hooks in `-p` sessions and in folders whose workspace trust was not accepted. The installer prints this once after a project install; the router's guard-ran check stops any dispatch whose guard did not run.

### Plugin

The build is a local marketplace rooted at `$FORGE_HOME/plugin/`:

```text
$FORGE_HOME/plugin/
  .claude-plugin/marketplace.json   # {"name": "forge-local", "owner": {"name": "Forge"},
                                    #  "plugins": [{"name": "forge", "source": "./forge"}]}
  forge/
    .claude-plugin/plugin.json      # {"name": "forge", "version": "<checkout HEAD sha, 12 chars>"}
    skills/  agents/
    hooks/hooks.json
```

`hooks.json` registers the guard once under `PreToolUse` with no matcher:
`FORGE_HOME='<state dir>' python3 "${CLAUDE_PLUGIN_ROOT}"/skills/forge/scripts/guard_paths.py`. Generated profiles carry no `hooks:` block, since Claude Code ignores it in plugin agents.

After writing the build, the installer runs `claude plugin marketplace add $FORGE_HOME/plugin` (skipped when `forge-local` is already registered) and `claude plugin install forge@forge-local`. Plugin mode needs the `claude` command; without it on `PATH` the install is refused before anything is written. A rerun rewrites the build in place; running sessions pick it up at their next start or after `/reload-plugins`.

In plugin mode Claude Code names the agents `forge:forge-<role>` and the Commander `/forge:forge` (plain `/forge` still works while no other skill takes it). The installer records the agent-name prefix `forge:` in the install record for the components that dispatch agents or match their names.

Force-enabling the plugin in managed settings, so the guard runs under `allowManagedHooksOnly`, is an administrator's step outside the installer.

## Agent Profiles and Models

A generated profile is the template with its `model:` line replaced by the chosen model and, in plain-files mode, the guard `hooks:` block added to the frontmatter. Name, description, tools, preloaded skills, and body are copied unchanged.

### Presets

| Agent | `balanced` | `sonnet` | `opus` | `fable-opus` | `fable` |
|---|---|---|---|---|---|
| `forge-planner`, `forge-reviewer` | `opus` | `sonnet` | `opus` | `fable` | `fable` |
| `forge-council-*` personas | `opus` | `sonnet` | `opus` | `fable` | `fable` |
| `forge-qa`, `forge-dev`, `forge-verifier`, `forge-council` | `sonnet` | `sonnet` | `opus` | `opus` | `fable` |
| `forge-scout`, `forge-publisher`, `forge-reporter` | `haiku` | `sonnet` | `sonnet` | `sonnet` | `sonnet` |

`balanced` is the default. The three tool-running agents are capped at `sonnet` in every preset, because their work is mechanical; `--models` can raise them.

### `--models FILE`

A JSON object. Each key is an agent name (`forge-planner`, `forge-council-critic`, …) or `default`; each value is a model as Claude Code accepts it in a profile — an alias (`sonnet`, `opus`, `haiku`, `fable`, `inherit`) or a full model ID — given as a non-empty string with no whitespace. Each agent's model is, in order: its own key in the file, else the file's `default`, else the preset's model. An unknown agent name, a non-object file, or an invalid value refuses the install, naming the offending key.

The installer records the resolved model for every agent. A rerun with neither `--preset` nor `--models` reuses the recorded models; either flag replaces them.

## Settings

The installer adds to the scope's settings file, creating it when absent:

| Key | Value added |
|---|---|
| `env.FORGE_HOME` | the state directory |
| `permissions.additionalDirectories` | the state directory |
| `permissions.allow` | `Edit(//<state dir without leading slash>/**)` |

`additionalDirectories` lets spokes read the state directory without prompts; the `Edit` rule lets them write their envelopes and memory without prompts, which `additionalDirectories` alone does not in the default permission mode. `env.FORGE_HOME` makes every Forge script in the session use the same state directory as the guard.

Each addition is recorded only when the installer made it: a list entry already present, or an `env.FORGE_HOME` already holding the same value, is left alone and not recorded. An `env.FORGE_HOME` holding a different value refuses the install, naming both values. A settings file that is not a JSON object refuses the install.

Before changing `~/.claude/settings.json` the installer copies it to `~/.claude/settings.json.forge-backup-<UTC timestamp>`. The settings file is rewritten whole, as two-space-indented JSON with key order kept.

## Install Record

The install record is `$FORGE_HOME/install.json`, one file for the whole machine:

```json
{
  "mode": "plain",
  "installs": [
    {
      "scope": "project",
      "root": "/abs/repo",
      "source": "/abs/forge-checkout",
      "source_sha": "<checkout HEAD>",
      "agent_prefix": "",
      "models": {"forge-planner": "opus", "...": "..."},
      "files": {"/abs/repo/.claude/agents/forge-dev.md": "<sha256>", "...": "..."},
      "settings_file": "/abs/repo/.claude/settings.local.json",
      "settings_added": {"env.FORGE_HOME": "...", "permissions.additionalDirectories": ["..."], "permissions.allow": ["..."]},
      "exclude_added": [".claude/skills/forge/", "..."],
      "backup": null,
      "complete": true
    }
  ]
}
```

`mode` is `plain` or `plugin`. `scope` is `global`, `project`, or `plugin`; `root` is `~/.claude` for the global scope and the plugin, the repository root for a project. `agent_prefix` is `""` for plain files and `forge:` for the plugin.

The record is written before the first install file, with `complete` false and the full planned `files` list, and rewritten with hashes and `complete` true at the end. An install that stops part way therefore leaves a record that `--uninstall` and a rerun both act on.

## Overlap Rules

Before writing, the installer compares the requested install with the record:

| Existing | Requested | Result |
|---|---|---|
| none | any | install |
| the same scope and root | same | reinstall in place |
| plain, global | plain, project | refused: the global install already serves every repository |
| plain, project (any repository) | plain, global | refused, naming the project installs |
| plain, project in repository A | plain, project in repository B | install (repositories do not share `.claude/`) |
| plain (any) | plugin | refused: the plugin's hook would guard the plain agents a second time |
| plugin | plain (any) | refused, for the same reason |

A refusal names the existing install and the `--uninstall` command that removes it. The installer never removes another install on its own.

Before reinstalling, every target path that exists and is not listed in the record refuses the install (`FILE_EXISTS`), so a user's own `forge-*` skill or agent is never overwritten. On reinstall, listed files the new install no longer produces are removed.

## Pins and Plugin Check

`rev` is a JSON object at the repository root mapping a dependency name to its pin:

```json
{
  "linked-intent-dev": {"repo": "https://github.com/jszmajda/lid", "commit": "<40-hex sha>",
                        "plugin": "linked-intent-dev@jszmajda-lid", "required": true}
}
```

`plugin` names a Claude Code plugin ID and appears only for pins Forge needs installed; `required` true makes a missing plugin fail the install. Pins without `plugin` are provenance only.

For every pin with `plugin`, the installer reads `~/.claude/plugins/installed_plugins.json` (`plugins.<id>` is a list of install entries, each with `scope` and `gitCommitSha`). An entry counts when its scope is `user`, or, for a project install, when it belongs to the target repository. No counting entry for a required plugin refuses the install; an entry whose `gitCommitSha` differs from the pin prints a warning naming both commits and the install continues. A missing or unreadable `installed_plugins.json` counts as no plugins installed.

## Uninstall

`--uninstall` acts on the record's install for the requested scope:

1. Each recorded file whose content still matches its recorded hash is removed; a file that changed since install is kept and named in the output. Directories left empty by the removal are removed.
2. Each recorded settings addition is removed from the settings file; values the user added are untouched. A settings file left as `{}` that the installer created is removed.
3. Each recorded `.git/info/exclude` line is removed.
4. In plugin mode, `claude plugin uninstall forge@forge-local` and `claude plugin marketplace remove forge-local` run, and `$FORGE_HOME/plugin/` is removed.
5. The install is deleted from the record; the record file is deleted when no install is left.

Backups are never restored: later changes to the settings file would be lost. Workstream state under `$FORGE_HOME/state/` is never touched. Uninstalling a scope with no record entry exits `1` with a message.

## Vendor

`--vendor` clones every pin in `rev` into `<checkout>/.vendor/<name>/` and checks out its pinned commit, detached. An existing clone is fetched and checked out again at the pin. `.vendor/` is gitignored. The clones are pinned sources for diffing and for a sandboxed Claude Code session that loads them; setting up that session is outside the installer.

## Output and Exit Codes

The installer prints each step it takes and ends with one summary line naming the mode, scope, root, preset or models file, and number of files written. Exit `0` on success, `1` on a refusal or failed step (the message names the reason), `2` on a usage error. Warnings (pin mismatches, kept files on uninstall) print and do not change the exit code.

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Implementation | `installer.sh` hands off to stdlib-Python `install.py` | All in shell | JSON merges of settings files, profile generation, and hashing are fragile in shell without `jq`; Python matches every other Forge script and is tested with pytest. |
| Plugin location | Build a local marketplace under `$FORGE_HOME/plugin/` and install from it | Marketplace inside the checkout; `--plugin-dir` | Claude Code reads a local-directory plugin in place, so a marketplace in the checkout would make the install depend on the checkout and run unreviewed edits. `--plugin-dir` lasts one session. |
| Plugin scope | Global (user) only | Project or local plugin scope | A project-scoped plugin is recorded in the tracked `.claude/settings.json`; a per-repository trial install is what plain-files project scope is for. |
| Install record | One machine-wide `$FORGE_HOME/install.json` | A manifest inside each install | The overlap rules need every install on the machine in one place; a manifest inside a project install is invisible from a global install. |
| Rerun without model flags | Reuse the recorded models | Fall back to `balanced` | Rerunning to pick up checkout changes should not silently change what each agent runs on. |
| `--models` with a preset | File overrides the preset per agent; `default` covers unnamed agents | Mutually exclusive flags; unnamed agents always `balanced` | One rule covers every combination, and `balanced` is still what unnamed agents get when no preset is named. |
| State directory in sessions | Write `env.FORGE_HOME` into the scope's settings file | Rely on the default `~/.forge` | A non-default state directory chosen at install would otherwise reach the guard (through its command) but not the Commander's scripts. |
| Uninstall of edited files | Keep and report files whose hash changed | Remove every recorded path | A changed file holds the user's edits; removing it loses work the record cannot restore. |
| Backup restore | Never | Restore the backup on uninstall | Restoring would discard every settings change made after the install. |
| Overlapping installs | Refuse and name the `--uninstall` command | Replace the old install automatically | Removing another install is the user's call (*Stop over guess*). |
| `rev` format | JSON | Line-based text | Parsed by the standard library with no format rules of Forge's own. |

## Open Questions & Future Decisions

### Resolved

1. ✅ Frontmatter `PreToolUse` hooks fire for copied project agents on every tool call, carry the agent's own `agent_type` and `agent_id` and the main session's `session_id`, and nested agents run their own hooks without inheriting the parent's (sandbox probe).
2. ✅ `additionalDirectories` plus an `Edit` allow rule lets spokes write the state directory without prompts (sandbox run 1).
3. ✅ Claude Code accepts `fable` as a profile model alias (sub-agents documentation).

### Deferred

1. Plugin mode, before it is built: whether plugin hooks fire under `-p`, what `agent_type` a plugin agent reports (`forge:forge-dev` is expected), and whether preloaded `skills:` in a plugin agent resolve bare names. A second sandbox probe answers these.
2. How a project-scope plugin install appears in `installed_plugins.json` (the field that names its repository), for the plugin check of a project install.
3. Repository URLs and commits for the provenance pins (BMAD, Obra Superpowers, memoir-skill) in `rev`.

## References

- `docs/high-level-design.md` — Installation, Repository layout, *Shared first*, *Stop over guess*.
- Guard design (`docs/intent/guard/`) — the adapter, `agent_type` scope, the duplicate-call breaker.
- Claude Code sub-agents, hooks, settings, and plugins documentation — frontmatter hooks and when they are skipped, plugin agents ignoring `hooks`, `${CLAUDE_PLUGIN_ROOT}`, local marketplaces, permission rule paths.
