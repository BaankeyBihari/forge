---
parent: high-level-design
prefix: INST
---

# Installer

## Context and Design Philosophy

The installer puts Forge where Claude Code finds it: the Commander and spoke skills, the agent profiles, the guard, and the permissions that let spokes write the state directory without prompting. It turns the repository's `claude/` tree into an install that keeps working when the checkout moves or changes, and that can be removed exactly.

Four rules shape it:

- **Copies, never links.** Every installed file is a regular file whose content was resolved from the checkout at install time. An edit in the checkout reaches a session only when the user reruns the installer.
- **One install per place it can collide.** Two guards on one tool call trip the duplicate-call circuit breaker, and mixed versions of skills and agents resolve unpredictably, so the installer refuses any install that would overlap an existing one.
- **Check everything before the first write.** Every check that can refuse runs before anything is written, so a refused install leaves the machine as it was. A write that fails afterwards is not rolled back: it leaves an incomplete install that a rerun finishes and `--uninstall` removes.
- **Never destroy the user's work.** The installer does not overwrite or remove a file it did not write, or one it wrote and the user has since edited, and does not change an install while a workstream is running.

`claude/installer.sh` is the entry point the user runs. It checks that `python3` is at least 3.11 and hands its arguments to `claude/install.py`, which does all the work with the Python standard library, like every other Forge script, and is tested with pytest.

## Command Line

```text
claude/installer.sh [--global | --project <repo>] [--plugin] [--preset <name>] [--models <file>]
claude/installer.sh --uninstall [--global | --project <repo>] [--force]
claude/installer.sh --vendor
```

| Option | Meaning |
|---|---|
| `--global` | Install into the Claude Code configuration directory (the default scope) |
| `--project <repo>` | Install into `<repo>/.claude/` |
| `--plugin` | Install as a Claude Code plugin instead of plain files; global scope only |
| `--preset <name>` | Model preset: `balanced`, `sonnet`, `opus`, `fable-opus`, `fable` |
| `--models <file>` | JSON map from agent name to model, applied over the preset |
| `--uninstall` | Remove the install at the given scope |
| `--force` | With `--uninstall`, also remove installed files the user has edited |
| `--vendor` | Clone every pin in `rev` into `.vendor/`; installs nothing |

Allowed combinations:

| Form | May also take |
|---|---|
| Install (no `--uninstall`, no `--vendor`) | at most one of `--global`, `--project <repo>`; `--plugin` unless `--project` is given; `--preset`, `--models`, or both |
| `--uninstall` | at most one of `--global`, `--project <repo>`; `--force` |
| `--vendor` | nothing else |

Any other combination, a repeated option, a missing option value, an unknown preset name, or an unknown option is a usage error: the installer prints `forge-installer: USAGE: <message>` and the usage text to stderr and exits `2`, having read and written nothing.

`--project <repo>` is resolved to its real path, which must be the root of a git repository's main worktree: `<repo>/.git` must be a directory. A linked worktree, a submodule, a subdirectory, or a path outside git refuses with `NOT_A_REPO_ROOT`. The install's key in the install record is `<real path>/.claude`.

`--uninstall` and `--uninstall --global` act on the install keyed `<config>`, which is either the global plain-files install or the plugin, since only one of them can exist.

### Claude Code configuration directory

`<config>` is the real path of `$CLAUDE_CONFIG_DIR` when that variable is set and non-empty, and of `~/.claude` otherwise: the directory Claude Code itself reads user settings, skills, agents, and plugins from. It is the global install's target and key, holds the global settings file, and holds `plugins/installed_plugins.json`. A project whose key equals `<config>` refuses with `INSTALL_OVERLAP`.

### Git

Every install and `--vendor` run needs the `git` command (else `GIT_MISSING`): the installer asks git whether a path is inside a working tree, whether a target is tracked, and what the checkout's state is.

### State directory

The state directory is `$FORGE_HOME` when that variable is set while the installer runs, and `~/.forge` otherwise, resolved to its real path. Everything the installer writes that names the state directory uses this one value.

A state directory inside a git working tree (git reports the directory, or its nearest existing ancestor, as inside one) refuses with `STATE_DIR_IN_REPO`: workstream state must never appear in a repository, and Claude Code's worktree isolation refuses spoke writes into the main checkout.

## Source Tree

The installer reads only `claude/`, `rev`, and `VERSION`:

| Source | What it is |
|---|---|
| `claude/skills/forge/` | The Commander skill; `scripts/` links to `reference/scripts/` |
| `claude/skills/forge-*/` | Links to `reference/skills/forge-*/`; `forge-spoke/scripts` and `forge-memory/scripts` link to `reference/scripts/` |
| `claude/agents/forge-*.md` | Agent profile templates: name, description, tools, preloaded skills, a `model:` line, and the body |
| `claude/hooks/guard_paths.py` | The guard's `PreToolUse` adapter |

The checkout is the directory containing `claude/install.py`. Each directory directly under `claude/skills/` is one skill of the same name. Copying follows every link, so each installed skill holds real files. At every depth, directories named `tests` or `__pycache__` and every file or directory whose name starts with `.` are not copied. `guard_paths.py` is copied into the installed Commander skill's `scripts/`, beside `guard_core.py`, and imports it from its own directory. A broken or cyclic link, a skill directory named `tests` or starting with `.`, a missing `claude/skills/forge/`, or a `guard_paths.py` already present in the Commander skill's `scripts/` refuses with `SOURCE_INVALID`.

`VERSION` at the checkout's root holds Forge's version as one line `yyyy.m.count`: the year, the month without a leading zero, and the release's count within that month starting at `1`, so `2026.11.1` is the first release of November 2026. It is bumped by hand at each release. A missing `VERSION`, or one whose content apart from a trailing newline does not match that form, refuses with `SOURCE_INVALID`. The installer records the version as `version` and writes it into the plugin's `plugin.json`.

The installer records the checkout's real path as `source` and its `HEAD` commit as `source_sha`. When `git status --porcelain` reports anything under `claude/` or `reference/` (untracked files included), the install proceeds, records `source_dirty: true`, and prints a warning. When the checkout is not a git repository, `source_sha` and `source_dirty` are `null`.

## Install Modes and Targets

| | Plain files, global | Plain files, project | Plugin |
|---|---|---|---|
| Skills | `<config>/skills/forge*/` | `<repo>/.claude/skills/forge*/` | `<plugin>/skills/forge*/` |
| Agents | `<config>/agents/forge-*.md` | `<repo>/.claude/agents/forge-*.md` | `<plugin>/agents/forge-*.md` |
| Guard | `hooks:` in each agent's frontmatter | `hooks:` in each agent's frontmatter | `<plugin>/hooks/hooks.json` |
| Settings file | `<config>/settings.json` | `<repo>/.claude/settings.local.json` | `<config>/settings.json` |
| Hidden from git | — | each installed path listed in `.git/info/exclude` | — |

`<plugin>` is `<state dir>/plugin/forge/`. Claude Code runs a plugin from a local-directory marketplace in place: its hooks run from, and `${CLAUDE_PLUGIN_ROOT}` points at, the marketplace's directory, even though `claude plugin install` also copies the plugin into Claude Code's plugin cache. The installer therefore builds the plugin outside the checkout, and that build is the installed copy.

The top-level installed paths are each skill directory and each agent file, and, for the plugin, `<state dir>/plugin/`. Removing directories left empty never goes above a top-level installed path, so `skills/`, `agents/`, and `.claude/` are never removed. Python writes a `__pycache__` directory beside the installed scripts when they run, so a directory holding nothing but `__pycache__` counts as left empty and is removed together with that cache.

### Plain files

The guard hook in each generated profile is:

```yaml
hooks:
  PreToolUse:
    - hooks:
        - type: command
          command: FORGE_HOME=<state dir> python3 <skills>/forge/scripts/guard_paths.py
```

with no matcher, so every tool call of the agent is checked, and with both paths absolute and each quoted by `shlex.quote`, so a path with no special characters stays bare. A template that already has a `hooks:` block refuses with `TEMPLATE_INVALID`. Every profile gets it, Council personas included: a nested agent runs its own frontmatter hooks, not its parent's.

The hook runs the `python3` found on the session's `PATH`. When that Python is older than 3.11, `guard_paths.py` blocks the call with a reason before importing anything else. When no `python3` is found, the hook fails without blocking, the guard records nothing, and the router's guard-ran check stops the workstream.

Project scope writes only untracked files. A planned file, or `.claude/settings.local.json`, that git tracks refuses with `TRACKED_TARGET`. Each top-level installed path and `.claude/settings.local.json` get a line in `.git/info/exclude` (`/.claude/skills/forge`, `/.claude/agents/forge-dev.md`, …) unless that exact line is already there. On a reinstall, lines the earlier install added stay recorded when still wanted and are removed when not.

Claude Code skips the frontmatter hooks of project agents (`<repo>/.claude/agents/`) in `-p` sessions and in folders whose workspace trust was not accepted; the frontmatter hooks of user agents (`~/.claude/agents/`) run in both. Every plain-files install ends by printing this, together with the note that the router stops any dispatch whose guard did not run.

### Plugin

The build is a local marketplace rooted at `<state dir>/plugin/`:

```text
<state dir>/plugin/
  .claude-plugin/marketplace.json   # {"name": "forge-local", "owner": {"name": "Forge"},
                                    #  "plugins": [{"name": "forge", "source": "./forge"}]}
  forge/
    .claude-plugin/plugin.json      # {"name": "forge", "version": "<VERSION>"}
    skills/  agents/
    hooks/hooks.json
```

`plugin.json` carries Forge's `version`. Claude Code loads a local-directory plugin as it is on disk at each session start or `/reload-plugins`, whatever its version says, so a rebuild needs no version bump to take effect.

`hooks.json` registers the guard once under `PreToolUse` with no matcher:
`FORGE_HOME=<state dir> python3 "${CLAUDE_PLUGIN_ROOT}"/skills/forge/scripts/guard_paths.py`, the state directory quoted by `shlex.quote` as in plain files. Generated profiles carry no `hooks:` block, since Claude Code ignores it in plugin agents.

Plugin mode needs the `claude` command on `PATH`; without it the install refuses with `CLAUDE_MISSING`. A marketplace is registered when `<config>/plugins/known_marketplaces.json` has an entry under its name; the entry's `installLocation` is its directory. A `forge-local` entry whose `installLocation` is another directory refuses with `MARKETPLACE_CONFLICT`. After writing the build, the installer runs `claude plugin marketplace add <state dir>/plugin` (skipped when `forge-local` is registered at that directory) and `claude plugin install forge@forge-local`, which is harmless to repeat on a reinstall.

In plugin mode Claude Code names the agents `forge:forge-<role>` and the Commander `/forge:forge` (plain `/forge` still works while no other skill takes it). It starts a plugin agent only by that name, from the main session and from inside another plugin agent alike, and reports it as the hook input's `agent_type`. Preloaded `skills:` entries with bare names resolve to the plugin's own skills, so profiles keep their bare names.

The build also writes `<plugin>/skills/forge/scripts/forge_install.json` holding `{"agent_prefix": "forge:"}`. The router reads it from its own directory and names every dispatch with it (router design); a plain-files install writes no such file, and the prefix is empty. A `forge_install.json` already present in the Commander skill's source `scripts/` refuses with `SOURCE_INVALID`. The install record's `agent_prefix` is `forge:`.

Force-enabling the plugin in managed settings, so the guard runs under `allowManagedHooksOnly`, is an administrator's step outside the installer.

## Agent Profiles and Models

The agents are the templates in `claude/agents/`, named by file name without `.md`. A generated profile is the template with its `model:` line replaced by the agent's model and, in plain-files mode, the guard `hooks:` block added to the frontmatter. Name, description, tools, preloaded skills, and body are copied unchanged. A template without a `model:` line in its frontmatter, or one that no preset row covers, refuses with `TEMPLATE_INVALID`.

### Presets

| Agent | `balanced` | `sonnet` | `opus` | `fable-opus` | `fable` |
|---|---|---|---|---|---|
| `forge-planner`, `forge-reviewer` | `opus` | `sonnet` | `opus` | `fable` | `fable` |
| `forge-council-*` personas | `opus` | `sonnet` | `opus` | `fable` | `fable` |
| `forge-qa`, `forge-dev`, `forge-verifier`, `forge-council` | `sonnet` | `sonnet` | `opus` | `opus` | `fable` |
| `forge-scout`, `forge-publisher`, `forge-reporter` | `haiku` | `sonnet` | `sonnet` | `sonnet` | `sonnet` |

No preset runs `forge-scout`, `forge-publisher`, or `forge-reporter` above `sonnet`, because their work is mechanical; `--models` can raise them.

### `--models FILE`

A JSON object. Each key is an agent name or `default`; each value is a model as Claude Code accepts it in a profile — an alias (`sonnet`, `opus`, `haiku`, `fable`, `inherit`) or a full model ID — given as a non-empty string with no whitespace. An unreadable file, a non-object, an unknown agent name, or an invalid value refuses with `MODELS_INVALID`, naming the offending key.

### Choosing each agent's model

| Flags given | Base | Applied over the base |
|---|---|---|
| `--preset` only | that preset | — |
| `--models` only | `balanced` | the file |
| both | that preset | the file |
| neither, reinstall | the recorded models | — |
| neither, first install | `balanced` | — |

Applying the file gives an agent its own key's value, else the file's `default`, else the base model. On a reinstall with neither flag, an agent the record does not name gets its `balanced` model. The record stores the resolved model of every agent.

## Settings

The installer adds to the scope's settings file, creating it when absent:

| Key | Value added |
|---|---|
| `env.FORGE_HOME` | the state directory |
| `permissions.additionalDirectories` | the state directory |
| `permissions.allow` | `Edit(//<state dir without its leading slash>/**)` |

`additionalDirectories` lets spokes read the state directory without prompts; the `Edit` rule lets them write their envelopes and memory without prompts, which `additionalDirectories` alone does not in the default permission mode. `env.FORGE_HOME` makes every Forge script in the session use the same state directory as the guard.

A value is recorded as an addition only when the installer adds it: a list entry already present, or an `env.FORGE_HOME` already holding the same value, is left alone and not recorded. An `env.FORGE_HOME` holding a different value refuses with `FORGE_HOME_CONFLICT`, naming both values, unless the record shows the installer wrote that value, in which case it is replaced. On a reinstall, the earlier install's recorded additions stay recorded when still wanted; recorded additions no longer wanted (an earlier state directory) are removed. A settings file that is not a JSON object, or whose `env` or `permissions` is not an object, or whose `permissions.allow` or `permissions.additionalDirectories` is not a list, refuses with `SETTINGS_INVALID`.

The settings file is read at the moment it is changed and written back in place as two-space-indented JSON with key order kept, so a settings file that is a link to a dotfiles repository stays a link. When a run changes `<config>/settings.json`, it first copies the file to `<config>/settings.json.forge-backup-<YYYYMMDDTHHMMSSZ>`, once per run, adding `-2`, `-3`, … when that name exists, and prints the backup's path. `.git/info/exclude` is also rewritten in place.

## Install Record

The install record is `~/.forge/install.json`, at that fixed path whatever the state directory is, so every install on the machine is in one place:

```json
{
  "installs": {
    "/abs/config": {"cli": "claude", "scope": "global", "...": "..."},
    "/abs/repo/.claude": {
      "cli": "claude",
      "scope": "project",
      "repo": "/abs/repo",
      "state_dir": "/abs/state",
      "source": "/abs/forge-checkout",
      "source_sha": "<checkout HEAD>",
      "source_dirty": false,
      "version": "2026.11.1",
      "agent_prefix": "",
      "models": {"forge-planner": "opus", "...": "..."},
      "files": {"/abs/repo/.claude/agents/forge-dev.md": "<sha256>", "...": "..."},
      "settings_file": "/abs/repo/.claude/settings.local.json",
      "settings_created": true,
      "settings_added": {"env.FORGE_HOME": "...", "permissions.additionalDirectories": ["..."], "permissions.allow": ["..."]},
      "exclude_added": ["/.claude/skills/forge", "..."],
      "backup": null,
      "complete": true
    }
  }
}
```

Each install is keyed by the configuration directory it installs into: `<config>` for the global scope and the plugin, `<repo>/.claude` for a project. Keying by that directory rather than by the repository lets installs for different CLIs live side by side in one repository. `cli` names the CLI the install is for; `claude` is the only value the installer writes or accepts, and an entry with another value is left untouched. `scope` is `global`, `project`, or `plugin`. `repo` is the repository's real path for a project install and `null` otherwise. `source` is the checkout's real path. `agent_prefix` is `""` for plain files and `forge:` for the plugin. `files` maps each installed file's absolute path to the SHA-256 of the content the installer writes; the hashes are computed before writing, so the record holds them from the start. `exclude_added` is `[]` outside project scope. `backup` is the path of the latest settings backup a run of this install made, or `null`. On a reinstall the first record entry written holds the earlier entry's files, settings additions, and exclude lines together with the new ones, so a run that stops part way loses track of nothing; `settings_created` keeps its recorded value.

The record is written to `install.json.forge-tmp` and renamed into place, as two-space-indented JSON with a trailing newline and non-ASCII characters kept. A record that cannot be read, does not parse, or has no `installs` object refuses with `RECORD_INVALID`; the installer never repairs or replaces it.

A `claude` project entry whose `repo` no longer exists is ignored by every check and removed the next time the run writes the record, and the run prints its key. An entry whose repository exists but whose files were deleted by hand stays until `--uninstall --project <repo>` removes it.

## Install Sequence

1. Parse the arguments.
2. Take the install lock `~/.forge/install.lock`, created exclusively and holding `{"pid": <process ID>}`. A lock whose process is alive, or that is empty or does not parse, refuses with `INSTALL_BUSY`, naming the lock's path, and is left in place. A lock whose process is gone is deleted and created again exclusively; when another run wins that race, this run refuses with `INSTALL_BUSY`.
3. Run every check: scope and state directory, overlap, running workstreams, existing and edited files, tracked targets, templates and models, settings file, plugin pins, and for the plugin the `claude` command and marketplace.
4. Write the record entry with `complete` false and the planned files with their hashes, settings additions, and exclude lines.
5. Write each file to `.<name>.forge-tmp` in its target directory and rename it into place; then remove recorded files this install no longer produces, and directories left empty.
6. Plugin only: run the `claude plugin` commands.
7. Back up and change the settings file.
8. Add the `.git/info/exclude` lines.
9. Rewrite the record entry with `complete` true, release the lock, and print the summary.

A step after 4 that fails stops the run with `STEP_FAILED`, naming the step, and leaves the record entry with `complete` false. A rerun of the same install treats it as a reinstall and finishes it; `--uninstall` removes what it wrote. Leftover `.forge-tmp` files are removed by the next run that writes or removes that install's files.

## Overlap Rules

The installer compares the requested install with the record's entries whose `cli` is `claude`; installs for other CLIs never overlap a Claude Code install:

| Existing | Requested | Result |
|---|---|---|
| none | any | install |
| the same scope and key | same | reinstall in place |
| plain, global | plain, project | refused: the global install already serves every repository |
| plain, project (any repository) | plain, global | refused, naming the project installs |
| plain, project in repository A | plain, project in repository B | install (repositories do not share `.claude/`) |
| plain (any) | plugin | refused: the plugin's hook would guard the plain agents a second time |
| plugin | plain (any) | refused, for the same reason |

An overlap refuses with `INSTALL_OVERLAP`, naming the existing install and the `--uninstall` command that removes it. The installer never removes another install on its own.

On every install, a target file or top-level installed path that exists (as a file, directory, or link) and that the record does not list — for a directory, with no recorded file under it — refuses with `FILE_EXISTS`, so a user's own `forge-*` skill or agent is never overwritten.

On a reinstall, a recorded file whose content no longer matches its recorded hash refuses with `FILES_EDITED`, naming every such file, whether or not the new install still produces it. The user moves or deletes them and reruns. A recorded file that is absent is written again.

## Running Workstreams

A reinstall or an uninstall refuses with `WORKSTREAM_ACTIVE` while any workstream lock (`state/<org>/<repo>/<workstream>/lock.json`) under the state directory of the affected install, or of the requested install, is held, whichever repository it belongs to: its `host` differs from this machine's host name (`socket.gethostname()`, as the workstream lock itself records it), its `holder_pid` is a live process on this machine, or it does not parse or lacks `host`. Replacing the guard or the skills under a running workstream would change its rules mid-dispatch. The message names each workstream and tells the user to finish it, stop it with `/forge stop` and close the session, or wait.

## Pins and Plugin Check

`rev` is a JSON object at the repository root mapping a dependency name to its pin:

```json
{
  "linked-intent-dev": {"repo": "https://github.com/jszmajda/lid", "commit": "<40-hex sha>",
                        "plugin": "linked-intent-dev@jszmajda-lid", "required": true}
}
```

`rev` sits at the checkout's root, and each pin's name is its key. Every pin has `repo` and `commit`. `plugin` names a Claude Code plugin ID and appears only for pins Forge needs installed; `required` true makes a missing plugin refuse the install. Pins without `plugin` are provenance only. A missing `rev`, one that does not parse, a pin lacking a field, or `required` true without `plugin` refuses with `REV_INVALID`.

For every pin with `plugin`, the installer reads `<config>/plugins/installed_plugins.json` (`plugins.<id>` is a list of install entries, each with `scope` and `gitCommitSha`). An entry counts when its scope is `user`. No counting entry for a required plugin refuses with `PLUGIN_MISSING`; an entry whose `gitCommitSha` differs from the pin prints a warning naming both commits and the install continues. A missing or unreadable `installed_plugins.json` counts as no plugins installed.

## Uninstall

`--uninstall` takes the install lock, refuses while a workstream runs, and acts on the record's install at the requested scope:

1. Each recorded file whose content matches its recorded hash is removed; an absent file counts as removed; a file that changed is kept and named in the output, unless `--force` is given, which removes it too. Directories left empty are removed.
2. Plugin only: `claude plugin uninstall forge@forge-local` runs when `<config>/plugins/installed_plugins.json` lists `forge@forge-local`, and `claude plugin marketplace remove forge-local` runs when `known_marketplaces.json` lists `forge-local`; each fails when its entry is already gone, so a plugin the user removed by hand is not a failed step. Claude Code's own leftovers, such as empty `enabledPlugins` and `extraKnownMarketplaces` objects and its plugin cache, stay.
3. Each recorded settings addition still present with the recorded value is removed; a value the user changed or added is untouched. `env`, `permissions.allow`, `permissions.additionalDirectories`, and then `permissions` are removed when left empty. A settings file left as `{}` is removed when `settings_created` is true.
4. Each recorded `.git/info/exclude` line is removed, except lines covering a kept file, which are named in the output for the user to remove once they deal with the file.
5. When no file was kept, the install is deleted from the record, and the record file is deleted when no install is left. When files were kept, the entry stays, listing only the kept files and the exclude lines covering them, and the run exits with `FILES_KEPT`; a later install at the same scope refuses with `FILES_EDITED` until the user moves or deletes them, or reruns `--uninstall --force`.

A step that fails is reported and the remaining steps still run; the run then exits with `STEP_FAILED` (which takes precedence over `FILES_KEPT`), and the record keeps the entries whose removal failed so a rerun can finish. Backups are never restored: later changes to the settings file would be lost. Workstream state under the state directory is never touched. Uninstalling a scope with no record entry refuses with `NOT_INSTALLED`.

## Vendor

`--vendor` needs `git` (else `GIT_MISSING`) and a valid `rev`. It clones every pin into `<checkout>/.vendor/<name>/` and checks out its pinned commit, detached; an existing clone is fetched and checked out again at the pin. A pin whose clone, fetch, or checkout fails is reported and the others continue; the run then exits with `VENDOR_FAILED`. `.vendor/` is gitignored. The clones are pinned sources for diffing and for a sandboxed Claude Code session that loads them; setting up that session is outside the installer.

## Output and Exit Codes

Each step prints one line to stdout, every line starting with `forge-installer: `. A successful install ends with `forge-installer: installed <plain|plugin> <scope> <key> models=<choice> files=<n>` as its last stdout line, where `<choice>` is the preset name, followed by `+<models file>` when one was given, or `recorded`. A refusal or failure prints `forge-installer: <CODE>: <message>` to stderr. Warnings print to stderr as `forge-installer: warning: <message>` and do not change the exit code.

| Exit | Codes |
|---|---|
| `0` | success |
| `1` | `PYTHON_TOO_OLD` (from `installer.sh`), `NOT_A_REPO_ROOT`, `STATE_DIR_IN_REPO`, `INSTALL_BUSY`, `WORKSTREAM_ACTIVE`, `INSTALL_OVERLAP`, `RECORD_INVALID`, `FILE_EXISTS`, `FILES_EDITED`, `FILES_KEPT`, `TRACKED_TARGET`, `SOURCE_INVALID`, `TEMPLATE_INVALID`, `MODELS_INVALID`, `SETTINGS_INVALID`, `FORGE_HOME_CONFLICT`, `REV_INVALID`, `PLUGIN_MISSING`, `CLAUDE_MISSING`, `MARKETPLACE_CONFLICT`, `NOT_INSTALLED`, `GIT_MISSING`, `STEP_FAILED`, `VENDOR_FAILED` |
| `2` | `USAGE` |

## Decisions & Alternatives

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Implementation | `installer.sh` hands off to stdlib-Python `install.py` | All in shell | JSON merges of settings files, profile generation, and hashing are fragile in shell without `jq`; Python matches every other Forge script and is tested with pytest. |
| Plugin location | Build a local marketplace under the state directory and install from it | Marketplace inside the checkout; `--plugin-dir` | Claude Code reads a local-directory plugin in place, so a marketplace in the checkout would make the install depend on the checkout and run edits nobody reinstalled. `--plugin-dir` lasts one session. |
| Plugin agent names | The router names each dispatch from `forge_install.json` beside it; the guard strips `forge:` | The installer rewrites agent names inside the copied skill files; the skills retry with `forge:` after a "not found" error | A script supplies the exact name, so the model only copies a field; text substitution breaks silently when a skill's wording changes, and a retry wastes a call per dispatch and depends on the model. |
| Plugin scope | Global (user) only | Project or local plugin scope | A project-scoped plugin is recorded in the tracked `.claude/settings.json`; a per-repository trial install is what plain-files project scope is for. |
| Install record location | Fixed `~/.forge/install.json` | Under the state directory; `~/.claude/forge-install.json`; a record inside each project repository | The overlap rules need every install in one place; a record under the state directory splits into one per state directory and lets a project install sit over a global one, and a record inside each repository is invisible to a global install. Keeping it beside Forge's default state keeps Forge files out of Claude Code's configuration directory and out of repositories. |
| Install record shape | One entry per configuration directory, each naming its `cli` | A list of installs; entries keyed by repository | A repository can hold installs for several CLIs, each in its own configuration directory, so that directory is the unique key; `cli` limits the overlap rules to installs of the same CLI. |
| Edited files on reinstall | Refuse and name them | Overwrite, keeping `<file>.forge-edited`; overwrite silently | The user's edits are never lost or moved behind their back; uninstall follows the same rule. |
| Guard interpreter | `python3` from the session's `PATH`; the adapter blocks on Python older than 3.11 | Absolute path of the interpreter the installer checked | An absolute path breaks when a version manager removes that interpreter. A missing `python3` cannot block the call, but the router's guard-ran check stops the workstream. |
| Changing an install under a running workstream | Refuse while any workstream lock is held | Warn and continue | Replacing the guard or skills mid-dispatch changes a running spoke's rules (*Stop over guess*). |
| State directory inside a git working tree | Refuse | Allow; refuse only inside the target repository | Workstream state must never appear in a repository, and worktree isolation refuses spoke writes into a main checkout. A user whose home directory is a git working tree sets `FORGE_HOME` outside it. |
| Write order | All checks, then the record with planned hashes, files, plugin commands, settings, exclude lines | Roll back on failure; write the record last | A record with planned hashes lets a rerun or uninstall recognize every file the run wrote; rollback adds a second path that can itself fail midway. Settings are changed after the `claude` commands, which write the same file. |
| Python bytecode caches | A directory holding only `__pycache__` counts as left empty and is removed with it | Remove each recorded script's own `__pycache__/<name>.*.pyc` only; stop Python writing caches (`sys.dont_write_bytecode` in every script, or `PYTHONDONTWRITEBYTECODE` in the settings file) | The cache is derived from installed files and never holds the user's work. Matching per script misses caches of scripts an earlier version installed under another name. Stopping the writes needs a line in every entry script and misses scripts started another way, and the settings variable would change every Python run in the user's sessions. |
| Settings write | In place, read at the moment of change | Write a temp file and rename | Renaming would replace a settings file that links into a dotfiles repository. |
| Rerun without model flags | Reuse the recorded models | Fall back to `balanced` | Rerunning to pick up checkout changes should not silently change what each agent runs on. |
| `--models` with a preset | File applied over the preset, or over `balanced` when no preset is named; `default` covers unnamed agents | Mutually exclusive flags; file applied over the recorded models | One rule covers every combination, and the result does not depend on what an earlier run chose. |
| State directory in sessions | Write `env.FORGE_HOME` into the scope's settings file | Rely on the default `~/.forge` | A non-default state directory chosen at install would otherwise reach the guard (through its command) but not the Commander's scripts. |
| Uninstall of edited files | Keep and report them, keep their record entry, exit `FILES_KEPT`; `--force` removes them | Remove every recorded path; keep them and drop the entry with exit `0` | A changed file holds the user's edits; removing it loses work the record cannot restore. Keeping the entry stops a later install from treating the kept files as foreign, and a non-zero exit tells a script the uninstall is not finished. `--force` is the explicit way to clean up anyway. |
| `--force` scope | Uninstall only | Also let a reinstall overwrite edited files | One destructive flag in one place; a user who wants a forced reinstall runs `--uninstall --force` first. |
| Configuration directory | Honour `$CLAUDE_CONFIG_DIR`, else `~/.claude` | Always `~/.claude` | Claude Code reads its configuration from `$CLAUDE_CONFIG_DIR` when set; an install elsewhere would never load. |
| Running workstreams that block a change | Every held lock under the install's state directory | Only workstreams of the affected repository | Simple and safe; matching workstreams to repositories needs state beyond the lock, for a rare case of shared state directories. |
| Backup restore | Never | Restore the backup on uninstall | Restoring would discard every settings change made after the install. |
| Overlapping installs | Refuse and name the `--uninstall` command | Replace the old install automatically | Removing another install is the user's call (*Stop over guess*). |
| Version source | `VERSION` at the checkout root, bumped by hand | The latest `vYYYY.M.N` git tag; computed from the commit date and a count of that month's commits | A file works outside git and in a dirty checkout and is read with no tooling; computing it would make every commit a version. |
| `rev` format | JSON | Line-based text | Parsed by the standard library with no format rules of Forge's own. |

## Open Questions & Future Decisions

### Resolved

1. ✅ Frontmatter `PreToolUse` hooks fire for copied project agents on every tool call, carry the agent's own `agent_type` and `agent_id` and the main session's `session_id`, and nested agents run their own hooks without inheriting the parent's (sandbox probe).
2. ✅ `additionalDirectories` plus an `Edit` allow rule lets spokes write the state directory without prompts (sandbox run 1).
3. ✅ A session started in the main checkout keeps the project install's skills, agents, and settings after entering a Forge worktree (sandbox runs 1 to 3).
4. ✅ Claude Code accepts `fable` as a profile model alias, and reads a plugin from a local-directory marketplace in place, picking up edits at the next session start or `/reload-plugins` (sub-agents and plugin marketplace documentation).
5. ✅ Plugin hooks fire under `-p`: they merge with settings-file hooks, which run in `-p` sessions (Claude Code hooks and permissions documentation).
6. ✅ Plugin agents are started only by their scoped name `forge:forge-<role>`, including from inside other plugin agents, and report that name as `agent_type`; bare `skills:` names resolve to the plugin's own skills, whose base directory is shown so `scripts/` can be found; the plugin hook fires for the main session and every agent, under `-p` too; an installed local-directory plugin runs from the marketplace directory; `known_marketplaces.json` records each marketplace's `installLocation` (second sandbox probe).

### Deferred

1. How a project-scope plugin install appears in `installed_plugins.json`. Until then only `user` entries count for the plugin check, so a project install with `linked-intent-dev` installed only for that project refuses.
2. Repository URLs and commits for the provenance pins (BMAD, Obra Superpowers, memoir-skill) in `rev`.
3. Whether a session started directly inside a Forge worktree loads the main checkout's untracked project install and `settings.local.json`.

## References

- `docs/high-level-design.md` — Installation, Repository layout, *Shared first*, *Stop over guess*.
- Guard design (`docs/intent/guard/`) — the adapter, `agent_type` scope, the duplicate-call breaker.
- Workstream state design (`docs/intent/workstream-state/`) — `lock.json` fields.
- Claude Code sub-agents, hooks, settings, plugins, and plugin marketplace documentation — frontmatter hooks and when they are skipped, plugin agents ignoring `hooks`, `${CLAUDE_PLUGIN_ROOT}`, local marketplaces, permission rule paths.
