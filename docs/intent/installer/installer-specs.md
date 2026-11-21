# Installer — EARS Specs

Design: `installer-design.md`. Implementation: `claude/installer.sh` (entry point), `claude/install.py` (everything else).

"The installer" below means `claude/installer.sh` together with `claude/install.py`. Plugin-mode specs are marked `[D]`: the plugin is built after a second sandbox probe confirms how Claude Code runs plugin hooks and names plugin agents (design, Deferred 1).

## Entry point

- [ ] **INST-CLI-001**: When `claude/installer.sh` runs and `python3` is missing or older than 3.11, the installer shall print `forge-install: PYTHON_TOO_OLD: <message>` to stderr and exit `1` without running `install.py`.
- [ ] **INST-CLI-002**: When `claude/installer.sh` runs with `python3` 3.11 or newer, the installer shall run `claude/install.py` with the same arguments and exit with its exit code.

## Command line

- [ ] **INST-CLI-003**: When run with neither `--uninstall` nor `--vendor`, the installer shall install, at global scope unless `--project <repo>` is given.
- [ ] **INST-CLI-004**: If an install run is given both `--global` and `--project`, or `--plugin` with `--project`, then the installer shall exit `2` with `USAGE`.
- [ ] **INST-CLI-005**: If a `--uninstall` run is given `--plugin`, `--preset`, `--models`, or `--vendor`, or both `--global` and `--project`, then the installer shall exit `2` with `USAGE`.
- [ ] **INST-CLI-006**: If a `--vendor` run is given any other option, then the installer shall exit `2` with `USAGE`.
- [ ] **INST-CLI-007**: If the arguments contain an unknown option, a repeated option, an option missing its value, or a `--preset` value other than `balanced`, `sonnet`, `opus`, `fable-opus`, or `fable`, then the installer shall exit `2` with `USAGE`.
- [ ] **INST-CLI-008**: When the installer exits with `USAGE`, it shall print the usage text and shall have read and written no file.
- [ ] **INST-CLI-009**: When given `--project <repo>`, the installer shall resolve `<repo>` to its real path and use `<real path>/.claude` as the install's key in the install record.
- [ ] **INST-CLI-010**: If the real path of a `--project` argument has no `.git` directory directly inside it, then the installer shall refuse with `NOT_A_REPO_ROOT`.
- [ ] **INST-CLI-011**: When `--uninstall` is given without `--project`, the installer shall act on the install keyed `~/.claude`.

## State directory

- [ ] **INST-STATE-001**: The installer shall use as the state directory the real path of `$FORGE_HOME` when that variable is set and non-empty, and of `~/.forge` otherwise.
- [ ] **INST-STATE-002**: If the state directory, or its nearest existing ancestor, is inside a git working tree, then the installer shall refuse an install with `STATE_DIR_IN_REPO`.

## Source tree

- [ ] **INST-SRC-001**: When copying a skill, the installer shall write every file reachable under `claude/skills/<skill>/` as a regular file holding the content of the file the path resolves to, following every link.
- [ ] **INST-SRC-002**: When copying a skill, the installer shall skip directories named `tests` or `__pycache__` and every file or directory whose name starts with `.`.
- [ ] **INST-SRC-003**: The installer shall install each directory under `claude/skills/` as a skill of the same name.
- [ ] **INST-SRC-004**: The installer shall copy `claude/hooks/guard_paths.py` into the installed `forge` skill's `scripts/` directory.
- [ ] **INST-SRC-005**: The installer shall record the checkout's `HEAD` commit as `source_sha`, or `null` when the checkout is not a git repository.
- [ ] **INST-SRC-006**: While `claude/` or `reference/` in the checkout has uncommitted changes, the installer shall record `source_dirty` true and print a warning, and shall still install.

## Plain-files targets

- [ ] **INST-PLAIN-001**: Where the install is plain files at global scope, the installer shall write skills to `~/.claude/skills/` and agents to `~/.claude/agents/`.
- [ ] **INST-PLAIN-002**: Where the install is plain files at project scope, the installer shall write skills to `<repo>/.claude/skills/` and agents to `<repo>/.claude/agents/`.
- [ ] **INST-PLAIN-003**: Where the install is plain files, the installer shall add to every generated agent profile's frontmatter a `hooks:` block with one `PreToolUse` command hook, no matcher, whose command is `FORGE_HOME='<state dir>' python3 '<installed forge skill>/scripts/guard_paths.py'` with both paths absolute.
- [ ] **INST-PLAIN-004**: If a target path, or `<repo>/.claude/settings.local.json`, of a project install is tracked by git, then the installer shall refuse with `TRACKED_TARGET`.
- [ ] **INST-PLAIN-005**: When a project install writes its files, the installer shall append to `<repo>/.git/info/exclude` a line `/<path>` for each top-level installed path and for `.claude/settings.local.json`, relative to the repository root, skipping any line already present exactly.
- [ ] **INST-PLAIN-006**: When a plain-files install completes, the installer shall print that Claude Code skips agent frontmatter hooks in `-p` sessions and in folders whose workspace trust was not accepted, and that the router stops any dispatch whose guard did not run.

## Plugin

- [D] **INST-PLUG-001**: Where the install is the plugin, the installer shall write the plugin to `<state dir>/plugin/forge/` and a marketplace file `<state dir>/plugin/.claude-plugin/marketplace.json` naming marketplace `forge-local` with one plugin `forge` at source `./forge`.
- [D] **INST-PLUG-002**: Where the install is the plugin, the installer shall write `<state dir>/plugin/forge/.claude-plugin/plugin.json` holding `{"name": "forge"}` with no `version`.
- [D] **INST-PLUG-003**: Where the install is the plugin, the installer shall write `<state dir>/plugin/forge/hooks/hooks.json` registering one `PreToolUse` command hook, no matcher, whose command is `FORGE_HOME='<state dir>' python3 "${CLAUDE_PLUGIN_ROOT}"/skills/forge/scripts/guard_paths.py`.
- [D] **INST-PLUG-004**: Where the install is the plugin, the installer shall write generated agent profiles without a `hooks:` block.
- [D] **INST-PLUG-005**: If a plugin install runs without a `claude` command on `PATH`, then the installer shall refuse with `CLAUDE_MISSING`.
- [D] **INST-PLUG-006**: If a marketplace named `forge-local` is registered at a directory other than `<state dir>/plugin`, then the installer shall refuse a plugin install with `MARKETPLACE_CONFLICT`.
- [D] **INST-PLUG-007**: When a plugin install has written its files, the installer shall run `claude plugin marketplace add <state dir>/plugin` unless `forge-local` is registered at that directory, then `claude plugin install forge@forge-local`.
- [D] **INST-PLUG-008**: Where the install is the plugin, the installer shall record `agent_prefix` `forge:`; where it is plain files, `""`.

## Agent profiles and models

- [ ] **INST-MODEL-001**: The installer shall take the agent set from the files `claude/agents/*.md`, naming each agent by its file name without `.md`.
- [ ] **INST-MODEL-002**: When generating an agent profile, the installer shall copy the template unchanged except that the frontmatter `model:` line holds the agent's resolved model, plus the guard hook per INST-PLAIN-003 for plain files.
- [ ] **INST-MODEL-003**: If a template has no `model:` line in its frontmatter, or matches no row of the preset table, then the installer shall refuse with `TEMPLATE_INVALID`.
- [ ] **INST-MODEL-004**: The installer shall assign preset models as follows: `forge-planner`, `forge-reviewer`, and `forge-council-<persona>` get `opus` in `balanced`, `sonnet` in `sonnet`, `opus` in `opus`, `fable` in `fable-opus` and `fable`.
- [ ] **INST-MODEL-005**: The installer shall assign preset models as follows: `forge-qa`, `forge-dev`, `forge-verifier`, and `forge-council` get `sonnet` in `balanced` and `sonnet`, `opus` in `opus` and `fable-opus`, `fable` in `fable`.
- [ ] **INST-MODEL-006**: The installer shall assign preset models as follows: `forge-scout`, `forge-publisher`, and `forge-reporter` get `haiku` in `balanced` and `sonnet` in `sonnet`, `opus`, `fable-opus`, and `fable`.
- [ ] **INST-MODEL-007**: If the `--models` file is unreadable, is not a JSON object, has a key that is neither an agent name nor `default`, or has a value that is not a non-empty string without whitespace, then the installer shall refuse with `MODELS_INVALID`, naming the offending key when there is one.
- [ ] **INST-MODEL-008**: When `--preset` is given, the installer shall use that preset as each agent's base model; when only `--models` is given, `balanced`.
- [ ] **INST-MODEL-009**: When a reinstall is given neither `--preset` nor `--models`, the installer shall give each agent its recorded model, or its `balanced` model when the record names none for it.
- [ ] **INST-MODEL-010**: When a first install is given neither `--preset` nor `--models`, the installer shall give each agent its `balanced` model.
- [ ] **INST-MODEL-011**: When `--models` is given, the installer shall give each agent the file's value for its name, else the file's `default`, else its base model.
- [ ] **INST-MODEL-012**: The installer shall record every agent's resolved model under the install's `models`.

## Settings

- [ ] **INST-SET-001**: The installer shall use `~/.claude/settings.json` as the settings file for a global or plugin install, and `<repo>/.claude/settings.local.json` for a project install, creating it when absent.
- [ ] **INST-SET-002**: The installer shall ensure the settings file holds `env.FORGE_HOME` equal to the state directory, the state directory in `permissions.additionalDirectories`, and `Edit(//<state dir without its leading slash>/**)` in `permissions.allow`.
- [ ] **INST-SET-003**: The installer shall record under `settings_added` only the values it added, not values already present.
- [ ] **INST-SET-004**: If the settings file's `env.FORGE_HOME` holds a value other than the state directory and that value is not recorded in the install's `settings_added`, then the installer shall refuse with `FORGE_HOME_CONFLICT`, naming both values.
- [ ] **INST-SET-005**: When a reinstall runs, the installer shall keep the earlier install's `settings_added` entries that are still wanted and remove from the settings file and the record those no longer wanted.
- [ ] **INST-SET-006**: If the settings file exists and is not a JSON object, then the installer shall refuse with `SETTINGS_INVALID`.
- [ ] **INST-SET-007**: When changing the settings file, the installer shall read it at that moment and write it back in place, so a settings file that is a link stays a link, as two-space-indented JSON with key order kept.
- [ ] **INST-SET-008**: When a run is about to change `~/.claude/settings.json`, the installer shall first copy it, once per run, to `~/.claude/settings.json.forge-backup-<YYYYMMDDTHHMMSSZ>`, adding `-2`, `-3`, … when that name exists, and record the copy's path as `backup`.
- [ ] **INST-SET-009**: When `settings_file` did not exist before the install, the installer shall record `settings_created` true; otherwise false.

## Install record

- [ ] **INST-REC-001**: The installer shall keep the install record at `~/.forge/install.json` whatever the state directory is.
- [ ] **INST-REC-002**: The installer shall store each install in the record's `installs` object under the key of its configuration directory: `~/.claude` for a global or plugin install, `<repo>/.claude` for a project install.
- [ ] **INST-REC-003**: The installer shall write each install it makes with `cli` `claude`, `scope` (`global`, `project`, or `plugin`), `state_dir`, `source`, `source_sha`, `source_dirty`, `agent_prefix`, `models`, `files`, `settings_file`, `settings_created`, `settings_added`, `exclude_added`, `backup`, and `complete`, plus `repo` for a project install.
- [ ] **INST-REC-004**: The installer shall record under `files` every file it installs, mapped to the SHA-256 of the content it writes there.
- [ ] **INST-REC-005**: The installer shall leave every record entry whose `cli` is not `claude` unchanged.
- [ ] **INST-REC-006**: When a run finds a project entry whose `repo` does not exist, the installer shall ignore that entry in its checks, remove it from the record when the run next writes the record, and print its key.

## Install sequence

- [ ] **INST-SEQ-001**: When a run starts after parsing its arguments, the installer shall create `~/.forge/install.lock` exclusively, holding its process ID, and remove it when the run ends.
- [ ] **INST-SEQ-002**: If `~/.forge/install.lock` exists and names a live process, then the installer shall refuse with `INSTALL_BUSY`.
- [ ] **INST-SEQ-003**: If `~/.forge/install.lock` exists and names a process that is not alive, then the installer shall replace it and continue.
- [ ] **INST-SEQ-004**: The installer shall run every check that can refuse an install before writing any file other than the install lock.
- [ ] **INST-SEQ-005**: When an install's checks pass, the installer shall write its record entry with `complete` false and the planned `files` with their hashes, `settings_added`, and `exclude_added` before writing any install file.
- [ ] **INST-SEQ-006**: When writing an install file, the installer shall write it to `.<name>.forge-tmp` in the target directory and rename it into place.
- [ ] **INST-SEQ-007**: When a reinstall has written its files, the installer shall remove recorded files the new install does not produce, and directories left empty.
- [ ] **INST-SEQ-008**: The installer shall perform an install's writes in this order: record entry, files, `claude plugin` commands (plugin only), settings file, `.git/info/exclude` lines (project only), then the record entry with `complete` true.
- [ ] **INST-SEQ-009**: If an install step fails after the record entry is written, then the installer shall stop with `STEP_FAILED` naming the step and leave the record entry with `complete` false.
- [ ] **INST-SEQ-010**: When an install or uninstall writes or removes files, the installer shall also remove leftover `.forge-tmp` files in the target directories of that install.

## Overlap rules

- [ ] **INST-OVL-001**: The installer shall compare a requested install only with record entries whose `cli` is `claude`.
- [ ] **INST-OVL-002**: When the record holds an entry with the same key and scope as the requested install, the installer shall reinstall in place.
- [ ] **INST-OVL-003**: If a plain-files project install is requested while the record holds a plain-files global install, then the installer shall refuse with `INSTALL_OVERLAP`.
- [ ] **INST-OVL-004**: If a plain-files global install is requested while the record holds any project install, then the installer shall refuse with `INSTALL_OVERLAP`, naming each project install.
- [ ] **INST-OVL-005**: If a plugin install is requested while the record holds a plain-files install, or a plain-files install is requested while the record holds a plugin install, then the installer shall refuse with `INSTALL_OVERLAP`.
- [ ] **INST-OVL-006**: When refusing with `INSTALL_OVERLAP`, the installer shall name the existing install and the `--uninstall` command that removes it.
- [ ] **INST-OVL-007**: If a target file or top-level installed path exists as a file, directory, or link and the record lists neither it nor any file under it, then the installer shall refuse the install with `FILE_EXISTS`.
- [ ] **INST-OVL-008**: If a reinstall finds a recorded file whose content does not match its recorded hash, then the installer shall refuse with `FILES_EDITED`, naming every such file.
- [ ] **INST-OVL-009**: When a reinstall finds a recorded file absent, the installer shall write it again.
- [ ] **INST-OVL-010**: When a plain-files project install is requested while the record holds only project installs keyed in other repositories, the installer shall install.

## Running workstreams

- [ ] **INST-RUN-001**: If a reinstall or an uninstall finds a `state/<org>/<repo>/<workstream>/lock.json` under the state directory of the affected or the requested install whose `host` is another machine or whose `holder_pid` is a live process on this machine, then the installer shall refuse with `WORKSTREAM_ACTIVE`, naming each such workstream.

## Pins and plugin check

- [ ] **INST-PIN-001**: If `rev` does not parse as a JSON object of pins each with string `repo` and 40-hex `commit`, or a pin's `plugin` or `required` has the wrong type, then the installer shall refuse an install or vendor run with `REV_INVALID`.
- [ ] **INST-PIN-002**: When installing, the installer shall count, for each pin with `plugin`, the entries under that plugin ID in `~/.claude/plugins/installed_plugins.json` whose `scope` is `user`, treating a missing or unreadable file as no entries.
- [ ] **INST-PIN-003**: If a pin with `required` true has no counted entry, then the installer shall refuse with `PLUGIN_MISSING`.
- [ ] **INST-PIN-004**: When a counted entry's `gitCommitSha` differs from its pin's `commit`, the installer shall print a warning naming both commits and continue.

## Uninstall

- [ ] **INST-UNI-001**: If `--uninstall` finds no record entry for the requested key, then the installer shall refuse with `NOT_INSTALLED`.
- [ ] **INST-UNI-002**: When uninstalling, the installer shall take the install lock and apply INST-RUN-001 before removing anything.
- [ ] **INST-UNI-003**: When uninstalling, the installer shall remove each recorded file whose content matches its recorded hash, treat an absent recorded file as removed, keep a file whose content changed and name it in the output, and remove directories left empty.
- [D] **INST-UNI-004**: When uninstalling the plugin, the installer shall run `claude plugin uninstall forge@forge-local` and `claude plugin marketplace remove forge-local`, and remove `<state dir>/plugin/`.
- [ ] **INST-UNI-005**: When uninstalling, the installer shall remove each recorded `settings_added` value still present with the recorded value, and leave every other value untouched.
- [ ] **INST-UNI-006**: When uninstalling leaves `env`, `permissions.allow`, `permissions.additionalDirectories`, or then `permissions` empty in the settings file, the installer shall remove that key.
- [ ] **INST-UNI-007**: When uninstalling leaves the settings file as `{}` and `settings_created` is true, the installer shall delete the settings file.
- [ ] **INST-UNI-008**: When uninstalling, the installer shall remove each recorded `exclude_added` line except lines covering a kept file, which it names in the output.
- [ ] **INST-UNI-009**: When an uninstall step fails, the installer shall report it, run the remaining steps, keep in the record the entries whose removal failed, and exit with `STEP_FAILED`.
- [ ] **INST-UNI-010**: When an uninstall succeeds, the installer shall delete the install from the record, and delete the record file when no install is left.
- [ ] **INST-UNI-011**: The installer shall never restore a settings backup and never remove anything under the state directory's `state/`.

## Vendor

- [ ] **INST-VEN-001**: If `--vendor` runs without a `git` command on `PATH`, then the installer shall refuse with `GIT_MISSING`.
- [ ] **INST-VEN-002**: When `--vendor` runs, the installer shall clone each pin's `repo` into `<checkout>/.vendor/<name>/` when absent, fetch it when present, and check out the pin's `commit` detached.
- [ ] **INST-VEN-003**: If cloning, fetching, or checking out a pin fails, then the installer shall report that pin, continue with the others, and exit with `VENDOR_FAILED`.

## Output

- [ ] **INST-OUT-001**: When a refusal or failure ends a run, the installer shall print `forge-install: <CODE>: <message>` to stderr and exit `1`.
- [ ] **INST-OUT-002**: When an install succeeds, the installer shall exit `0` after one summary line naming the mode, scope, key, models choice, and number of files written.
- [ ] **INST-OUT-003**: If a refusal occurs, then the installer shall leave every file other than `~/.forge/install.lock` as it was before the run, and remove the lock.
