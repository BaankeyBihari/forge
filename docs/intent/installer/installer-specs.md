# Installer — EARS Specs

Design: `installer-design.md`. Implementation: `claude/installer.sh` (entry point), `claude/install.py` (everything else).

"The installer" below means `claude/installer.sh` together with `claude/install.py`.

## Entry point

- [x] **INST-CLI-001**: When `claude/installer.sh` runs and `python3` is missing or older than 3.11, the installer shall print `forge-install: PYTHON_TOO_OLD: <message>` to stderr and exit `1` without running `install.py`.
- [x] **INST-CLI-002**: When `claude/installer.sh` runs with `python3` 3.11 or newer, the installer shall run `claude/install.py` with the same arguments and exit with its exit code.

## Command line

- [x] **INST-CLI-003**: When run with neither `--uninstall` nor `--vendor`, the installer shall install, at global scope unless `--project <repo>` is given.
- [x] **INST-CLI-004**: If an install run is given both `--global` and `--project`, or `--plugin` with `--project`, then the installer shall exit `2` with `USAGE`.
- [x] **INST-CLI-005**: If a `--uninstall` run is given `--plugin`, `--preset`, `--models`, or `--vendor`, or both `--global` and `--project`, then the installer shall exit `2` with `USAGE`.
- [x] **INST-CLI-006**: If a `--vendor` run is given any other option, then the installer shall exit `2` with `USAGE`.
- [x] **INST-CLI-007**: If the arguments contain an unknown option, a bare word, an `--option=value` form, a repeated option, an option whose value is missing or starts with `--`, or a `--preset` value other than `balanced`, `sonnet`, `opus`, `fable-opus`, or `fable`, then the installer shall exit `2` with `USAGE`.
- [x] **INST-CLI-008**: When the installer exits with `USAGE`, it shall print `forge-install: USAGE: <message>` and the usage text to stderr and shall have written no file.
- [x] **INST-CLI-009**: When given `--project <repo>`, the installer shall resolve `<repo>` to its real path and use `<real path>/.claude` as the install's key in the install record.
- [x] **INST-CLI-010**: If the real path of an install's `--project` argument has no `.git` directory directly inside it, then the installer shall refuse with `NOT_A_REPO_ROOT`.
- [x] **INST-CLI-011**: When `--uninstall` is given without `--project`, the installer shall act on the install keyed by the Claude Code configuration directory (INST-CLI-013).
- [x] **INST-CLI-012**: If `--force` is given without `--uninstall`, then the installer shall exit `2` with `USAGE`.
- [x] **INST-CLI-013**: The installer shall use as the Claude Code configuration directory the real path of `$CLAUDE_CONFIG_DIR` when that variable is set and non-empty, and of `~/.claude` otherwise.
- [x] **INST-CLI-014**: If an install or `--vendor` run finds no `git` command on `PATH`, then the installer shall refuse with `GIT_MISSING`; an uninstall shall not need `git`.

## State directory

- [x] **INST-STATE-001**: The installer shall use as the state directory the real path of `$FORGE_HOME` when that variable is set and non-empty, and of `~/.forge` otherwise.
- [x] **INST-STATE-002**: If git reports the state directory, or its nearest existing ancestor, as inside a git working tree, then the installer shall refuse an install with `STATE_DIR_IN_REPO`; an uninstall shall not make this check.

## Source tree

- [x] **INST-SRC-001**: When copying a skill, the installer shall write every file reachable under `claude/skills/<skill>/` as a regular file holding the content of the file the path resolves to, following every link.
- [x] **INST-SRC-002**: When copying a skill, the installer shall skip, at every depth, directories named `tests` or `__pycache__` and every file or directory whose name starts with `.`.
- [x] **INST-SRC-003**: The installer shall install each directory directly under `claude/skills/` as a skill of the same name.
- [x] **INST-SRC-004**: The installer shall copy `claude/hooks/guard_paths.py` into the installed `forge` skill's `scripts/` directory.
- [x] **INST-SRC-005**: The installer shall record the checkout's real path as `source` and its `HEAD` commit as `source_sha`, with `source_sha` `null` when the checkout is not a git repository.
- [x] **INST-SRC-006**: While `git status --porcelain` reports any path under the checkout's `claude/` or `reference/`, untracked paths included, the installer shall record `source_dirty` true and print a warning, and shall still install; otherwise it shall record `source_dirty` false, or `null` when the checkout is not a git repository.
- [x] **INST-SRC-007**: The installer shall take as the checkout the directory containing `claude/install.py`.
- [ ] **INST-SRC-009**: The installer shall read Forge's version from `VERSION` at the checkout's root and record it as `version`.
- [ ] **INST-SRC-010**: If `VERSION` at the checkout's root is missing or, apart from one trailing newline, does not match `^[0-9]{4}\.([1-9]|1[0-2])\.[1-9][0-9]*$`, then the installer shall refuse with `SOURCE_INVALID`.
- [ ] **INST-SRC-008**: If a link under `claude/skills/` is broken or cyclic, a directory directly under `claude/skills/` is named `tests` or starts with `.`, `claude/skills/forge/` is missing, or the Commander skill's source `scripts/` already holds a `guard_paths.py` or a `forge_install.json`, then the installer shall refuse with `SOURCE_INVALID`.

## Plain-files targets

- [x] **INST-PLAIN-001**: Where the install is plain files at global scope, the installer shall write skills to `<config>/skills/` and agents to `<config>/agents/`, `<config>` being the Claude Code configuration directory (INST-CLI-013).
- [x] **INST-PLAIN-002**: Where the install is plain files at project scope, the installer shall write skills to `<repo>/.claude/skills/` and agents to `<repo>/.claude/agents/`.
- [x] **INST-PLAIN-003**: Where the install is plain files, the installer shall add to every generated agent profile's frontmatter, in block style, a `hooks:` block with one `PreToolUse` command hook, no matcher, whose command is `FORGE_HOME=<state dir> python3 <installed forge skill>/scripts/guard_paths.py` with both paths absolute and each quoted by `shlex.quote`.
- [x] **INST-PLAIN-004**: If a planned file of a project install, or `<repo>/.claude/settings.local.json`, is tracked by git, then the installer shall refuse with `TRACKED_TARGET`.
- [x] **INST-PLAIN-005**: When a project install writes its files, the installer shall append to `<repo>/.git/info/exclude` a line `/<path>`, with no trailing slash, for each top-level installed path and for `.claude/settings.local.json`, relative to the repository root, skipping any line already present exactly, and record each line it appends under `exclude_added`.
- [x] **INST-PLAIN-006**: When a plain-files install completes, the installer shall print to stdout that Claude Code skips the frontmatter hooks of project agents in `-p` sessions and in folders whose workspace trust was not accepted, and that the router stops any dispatch whose guard did not run.
- [x] **INST-PLAIN-007**: The installer shall treat as top-level installed paths each installed skill directory and each installed agent file, and, for the plugin, `<state dir>/plugin/`.
- [x] **INST-PLAIN-008**: When removing directories left empty, the installer shall remove none above a top-level installed path.
- [x] **INST-PLAIN-009**: When a project reinstall runs, the installer shall keep in `exclude_added` the earlier install's lines still wanted and remove from `.git/info/exclude` and the record those no longer wanted.
- [x] **INST-PLAIN-010**: When changing `.git/info/exclude`, the installer shall rewrite it in place.

## Plugin

- [ ] **INST-PLUG-001**: Where the install is the plugin, the installer shall write the plugin to `<state dir>/plugin/forge/` and a marketplace file `<state dir>/plugin/.claude-plugin/marketplace.json` naming marketplace `forge-local` with one plugin `forge` at source `./forge`.
- [ ] **INST-PLUG-002**: Where the install is the plugin, the installer shall write `<state dir>/plugin/forge/.claude-plugin/plugin.json` holding `{"name": "forge", "version": <version>}`, `<version>` being the version read per INST-SRC-009.
- [ ] **INST-PLUG-003**: Where the install is the plugin, the installer shall write `<state dir>/plugin/forge/hooks/hooks.json` registering one `PreToolUse` command hook, no matcher, whose command is `FORGE_HOME=<state dir> python3 "${CLAUDE_PLUGIN_ROOT}"/skills/forge/scripts/guard_paths.py` with the state directory quoted by `shlex.quote`.
- [ ] **INST-PLUG-004**: Where the install is the plugin, the installer shall write generated agent profiles without a `hooks:` block.
- [ ] **INST-PLUG-005**: If a plugin install runs without a `claude` command on `PATH`, then the installer shall refuse with `CLAUDE_MISSING`.
- [ ] **INST-PLUG-006**: If `<config>/plugins/known_marketplaces.json` has a `forge-local` entry whose `installLocation` is a directory other than `<state dir>/plugin`, then the installer shall refuse a plugin install with `MARKETPLACE_CONFLICT`.
- [ ] **INST-PLUG-007**: When a plugin install has written its files, the installer shall run `claude plugin marketplace add <state dir>/plugin` unless `<config>/plugins/known_marketplaces.json` has a `forge-local` entry whose `installLocation` is `<state dir>/plugin`, then `claude plugin install forge@forge-local`.
- [ ] **INST-PLUG-008**: Where the install is the plugin, the installer shall record `agent_prefix` `forge:`; where it is plain files, `""`.
- [ ] **INST-PLUG-009**: Where the install is the plugin, the installer shall write `<state dir>/plugin/forge/skills/forge/scripts/forge_install.json` holding `{"agent_prefix": "forge:"}`; where it is plain files, it shall write no `forge_install.json`.

## Agent profiles and models

- [x] **INST-MODEL-001**: The installer shall take the agent set from the files `claude/agents/*.md`, naming each agent by its file name without `.md`.
- [x] **INST-MODEL-002**: When generating an agent profile, the installer shall copy the template unchanged except that the frontmatter `model:` line holds the agent's resolved model, plus the guard hook per INST-PLAIN-003 for plain files.
- [x] **INST-MODEL-003**: If a template has no `model:` line in its frontmatter, or matches no row of the preset table, then the installer shall refuse with `TEMPLATE_INVALID`.
- [x] **INST-MODEL-013**: If a template's frontmatter already has a `hooks:` key, then the installer shall refuse with `TEMPLATE_INVALID`.
- [x] **INST-MODEL-004**: The installer shall assign preset models as follows: `forge-planner`, `forge-reviewer`, and `forge-council-<persona>` get `opus` in `balanced`, `sonnet` in `sonnet`, `opus` in `opus`, `fable` in `fable-opus` and `fable`.
- [x] **INST-MODEL-005**: The installer shall assign preset models as follows: `forge-qa`, `forge-dev`, `forge-verifier`, and `forge-council` get `sonnet` in `balanced` and `sonnet`, `opus` in `opus` and `fable-opus`, `fable` in `fable`.
- [x] **INST-MODEL-006**: The installer shall assign preset models as follows: `forge-scout`, `forge-publisher`, and `forge-reporter` get `haiku` in `balanced` and `sonnet` in `sonnet`, `opus`, `fable-opus`, and `fable`.
- [x] **INST-MODEL-007**: If the `--models` file is missing or unreadable, is not a JSON object, has a key that is neither an agent name nor `default`, or has a value that is not a non-empty string without whitespace, then the installer shall refuse with `MODELS_INVALID`, naming the offending key when there is one.
- [x] **INST-MODEL-008**: When `--preset` is given, the installer shall use that preset as each agent's base model; when only `--models` is given, `balanced`.
- [x] **INST-MODEL-009**: When a reinstall is given neither `--preset` nor `--models`, the installer shall give each agent its recorded model, or its `balanced` model when the record names none for it.
- [x] **INST-MODEL-010**: When a first install is given neither `--preset` nor `--models`, the installer shall give each agent its `balanced` model.
- [x] **INST-MODEL-011**: When `--models` is given, the installer shall give each agent the file's value for its name, else the file's `default`, else its base model.
- [x] **INST-MODEL-012**: The installer shall record every agent's resolved model under the install's `models`.

## Settings

- [x] **INST-SET-001**: The installer shall use `<config>/settings.json` as the settings file for a global or plugin install, `<config>` being the Claude Code configuration directory, and `<repo>/.claude/settings.local.json` for a project install, creating it when absent.
- [x] **INST-SET-002**: The installer shall ensure the settings file holds `env.FORGE_HOME` equal to the state directory, the state directory in `permissions.additionalDirectories`, and `Edit(//<state dir without its leading slash>/**)` in `permissions.allow`.
- [x] **INST-SET-003**: The installer shall record `settings_added` with all three keys: `env.FORGE_HOME` as the value it set or `null`, and `permissions.additionalDirectories` and `permissions.allow` as lists of the entries it added, possibly empty.
- [x] **INST-SET-004**: If the settings file's `env.FORGE_HOME` holds a value other than the state directory and that value is not recorded in the install's `settings_added`, then the installer shall refuse with `FORGE_HOME_CONFLICT`, naming both values.
- [x] **INST-SET-005**: When a reinstall runs, the installer shall keep the earlier install's `settings_added` entries that are still wanted and remove from the settings file and the record those no longer wanted.
- [x] **INST-SET-006**: If the settings file exists and is not a JSON object, or its `env` or `permissions` is present and not an object, or its `permissions.allow` or `permissions.additionalDirectories` is present and not a list, then the installer shall refuse with `SETTINGS_INVALID`.
- [x] **INST-SET-007**: When changing the settings file, the installer shall read it at that moment and write it back in place, so a settings file that is a link stays a link, as two-space-indented JSON with key order kept.
- [x] **INST-SET-008**: When a run is about to change an existing `<config>/settings.json`, the installer shall first copy it, once per run, to `<config>/settings.json.forge-backup-<YYYYMMDDTHHMMSSZ>`, adding `-2`, `-3`, … when that name exists, print the copy's path, and record it as the install's `backup`; a run that creates the file or leaves its content unchanged shall make no backup.
- [x] **INST-SET-009**: When a first install creates its settings file, the installer shall record `settings_created` true, and false when the file existed; a reinstall shall keep the recorded value.

## Install record

- [x] **INST-REC-001**: The installer shall keep the install record at `~/.forge/install.json` whatever the state directory is.
- [x] **INST-REC-002**: The installer shall store each install in the record's `installs` object under the key of its configuration directory: the Claude Code configuration directory (INST-CLI-013) for a global or plugin install, `<repo>/.claude` for a project install.
- [ ] **INST-REC-003**: The installer shall write each install it makes with exactly these fields: `cli` `claude`, `scope` (`global`, `project`, or `plugin`), `repo` (the repository's real path, `null` outside project scope), `state_dir`, `source`, `source_sha`, `source_dirty`, `version`, `agent_prefix`, `models`, `files`, `settings_file`, `settings_created`, `settings_added`, `exclude_added` (`[]` outside project scope), `backup` (`null` until a backup is made), and `complete`.
- [x] **INST-REC-004**: The installer shall record under `files` every file it installs, keyed by absolute path and mapped to the SHA-256 of the content it writes there.
- [x] **INST-REC-005**: The installer shall leave every record entry whose `cli` is not `claude` unchanged.
- [x] **INST-REC-006**: When a run finds a record entry with `cli` `claude` and `scope` `project` whose `repo` does not exist, the installer shall ignore that entry in its checks, remove it from the record when the run next writes the record, and print its key.
- [x] **INST-REC-007**: When writing the install record, the installer shall write `install.json.forge-tmp` beside it and rename it into place, as two-space-indented JSON with a trailing newline and non-ASCII characters kept.
- [x] **INST-REC-008**: If `~/.forge/install.json` exists and cannot be read, does not parse, or has no `installs` object, then the installer shall refuse with `RECORD_INVALID` and leave it unchanged.

## Install sequence

- [x] **INST-SEQ-001**: When a run starts after parsing its arguments, the installer shall create `~/.forge/install.lock` exclusively, holding the JSON object `{"pid": <its process ID>}`, and remove it when the run ends.
- [x] **INST-SEQ-002**: If `~/.forge/install.lock` exists and names a live process, is empty, or does not parse, then the installer shall refuse with `INSTALL_BUSY`, naming the lock's path, and leave the lock in place.
- [x] **INST-SEQ-003**: When `~/.forge/install.lock` names a process that is not alive, the installer shall delete it and create it again exclusively, refusing with `INSTALL_BUSY` if that creation fails.
- [x] **INST-SEQ-004**: The installer shall run every check that can refuse an install before writing any file other than the install lock.
- [x] **INST-SEQ-005**: When an install's checks pass, the installer shall write its record entry with `complete` false and the planned `files` with their hashes, `settings_added`, and `exclude_added` before writing any install file.
- [x] **INST-SEQ-006**: When writing an install file, the installer shall write it to `.<name>.forge-tmp` in the target directory and rename it into place.
- [x] **INST-SEQ-007**: When a reinstall has written its files, the installer shall remove recorded files the new install does not produce, and directories left empty.
- [x] **INST-SEQ-008**: The installer shall perform an install's writes in this order: record entry, files, `claude plugin` commands (plugin only), settings file, `.git/info/exclude` lines (project only), then the record entry with `complete` true.
- [x] **INST-SEQ-009**: If an install step fails after the record entry is written, then the installer shall stop with `STEP_FAILED` naming the step and leave the record entry with `complete` false.
- [x] **INST-SEQ-010**: When an install or uninstall writes or removes files, the installer shall also remove leftover `.forge-tmp` files in the target directories of that install.
- [x] **INST-SEQ-011**: When a reinstall writes its first record entry, the installer shall include the earlier entry's `files`, `settings_added`, and `exclude_added` together with the planned ones.

## Overlap rules

- [x] **INST-OVL-001**: The installer shall compare a requested install only with record entries whose `cli` is `claude`.
- [x] **INST-OVL-002**: When the record holds an entry with the same key and scope as the requested install, the installer shall reinstall in place.
- [x] **INST-OVL-003**: If a plain-files project install is requested while the record holds a plain-files global install, then the installer shall refuse with `INSTALL_OVERLAP`.
- [x] **INST-OVL-004**: If a plain-files global install is requested while the record holds any project install, then the installer shall refuse with `INSTALL_OVERLAP`, naming each project install.
- [x] **INST-OVL-005**: If a plugin install is requested while the record holds a plain-files install, or a plain-files install is requested while the record holds a plugin install, then the installer shall refuse with `INSTALL_OVERLAP`.
- [x] **INST-OVL-006**: When refusing with `INSTALL_OVERLAP`, the installer shall name the existing install and the `--uninstall` command that removes it.
- [x] **INST-OVL-007**: If a planned file or top-level installed path exists as a file, directory, or link and the record lists neither it nor any file under it, then the installer shall refuse the install with `FILE_EXISTS`.
- [x] **INST-OVL-008**: If a reinstall finds a recorded file whose content does not match its recorded hash, then the installer shall refuse with `FILES_EDITED`, naming every such file.
- [x] **INST-OVL-009**: When a reinstall finds a recorded file absent, the installer shall write it again.
- [x] **INST-OVL-010**: When a plain-files project install is requested while the record holds only project installs keyed in other repositories, the installer shall install.
- [x] **INST-OVL-011**: If a project install's key equals the Claude Code configuration directory, then the installer shall refuse with `INSTALL_OVERLAP`.

## Running workstreams

- [x] **INST-RUN-001**: If a reinstall or an uninstall finds, under the state directory of the affected or the requested install and for any repository, a `state/<org>/<repo>/<workstream>/lock.json` that does not parse, lacks `host`, has a `host` other than `socket.gethostname()`, or has a `holder_pid` that is a live process, then the installer shall refuse with `WORKSTREAM_ACTIVE`, naming each such workstream.

## Pins and plugin check

- [x] **INST-PIN-001**: If `rev` at the checkout's root is missing, does not parse as a JSON object of pins each with string `repo` and 40-hex `commit`, has a pin whose `plugin` is present and not a string or whose `required` is present and not a boolean, or has a pin with `required` true and no `plugin`, then the installer shall refuse an install or vendor run with `REV_INVALID`; an absent `required` means false.
- [x] **INST-PIN-002**: When installing, the installer shall count, for each pin with `plugin`, the entries of the list at `plugins.<plugin>` in `<config>/plugins/installed_plugins.json` whose `scope` is `user`, treating a missing, unreadable, or malformed file as no entries and saying so in any refusal it leads to.
- [x] **INST-PIN-003**: If a pin with `required` true has no counted entry, then the installer shall refuse with `PLUGIN_MISSING`.
- [x] **INST-PIN-004**: When a counted entry's `gitCommitSha` differs from its pin's `commit`, the installer shall print a warning naming both commits and continue.

## Uninstall

- [x] **INST-UNI-001**: If `--uninstall` finds no record entry for the requested key, then the installer shall refuse with `NOT_INSTALLED`.
- [x] **INST-UNI-002**: When uninstalling, the installer shall take the install lock and apply INST-RUN-001 before removing anything.
- [x] **INST-UNI-003**: When uninstalling, the installer shall remove each recorded file whose content matches its recorded hash, treat an absent recorded file as removed, and keep a file whose content changed, naming it in the output, unless `--force` is given, in which case it shall remove that file too; it shall then remove directories left empty.
- [ ] **INST-UNI-004**: When uninstalling the plugin, the installer shall run `claude plugin uninstall forge@forge-local` when `<config>/plugins/installed_plugins.json` lists `forge@forge-local`, then `claude plugin marketplace remove forge-local` when `<config>/plugins/known_marketplaces.json` lists `forge-local`, and remove `<state dir>/plugin/`.
- [x] **INST-UNI-005**: When uninstalling, the installer shall remove each recorded `settings_added` value still present with the recorded value, and leave every other value untouched.
- [x] **INST-UNI-006**: When uninstalling leaves `env`, `permissions.allow`, `permissions.additionalDirectories`, or then `permissions` empty in the settings file, the installer shall remove that key.
- [x] **INST-UNI-007**: When uninstalling leaves the settings file as `{}` and `settings_created` is true, the installer shall delete the settings file.
- [x] **INST-UNI-008**: When uninstalling, the installer shall remove each recorded `exclude_added` line except lines covering a kept file, which it names in the output.
- [x] **INST-UNI-009**: When an uninstall step fails, the installer shall report it, still attempt every other removal in that step and the remaining steps, keep in the record only the entries whose removal failed or that were kept, and exit with `STEP_FAILED`, even when files were also kept.
- [x] **INST-UNI-010**: When an uninstall keeps no file and no step fails, the installer shall delete the install from the record, and delete the record file when no install is left.
- [x] **INST-UNI-011**: The installer shall never restore a settings backup and never remove anything under the state directory's `state/`.
- [x] **INST-UNI-012**: When an uninstall keeps edited files and no step fails, the installer shall leave the install's record entry listing only the kept files and the exclude lines covering them, and exit with `FILES_KEPT`.

## Vendor

- [x] **INST-VEN-001**: When `--vendor` runs, the installer shall name each pin by its key in `rev`.
- [x] **INST-VEN-002**: When `--vendor` runs, the installer shall clone each pin's `repo` into `<checkout>/.vendor/<name>/` when absent, fetch it when present, and check out the pin's `commit` detached.
- [x] **INST-VEN-003**: If cloning, fetching, or checking out a pin fails, then the installer shall report that pin, continue with the others, and exit with `VENDOR_FAILED`.

## Output

- [x] **INST-OUT-001**: When a refusal or failure ends a run, the installer shall print `forge-install: <CODE>: <message>` to stderr and exit `1`.
- [x] **INST-OUT-002**: When an install succeeds, the installer shall exit `0` with, as its last stdout line, `forge-install: installed <plain|plugin> <scope> <key> models=<choice> files=<n>`, where `<choice>` is the preset name, followed by `+<models file>` when one was given, or `recorded`, and `<n>` is the number of files the install holds.
- [x] **INST-OUT-003**: If a refusal occurs, then the installer shall leave every file and directory as it was before the run, except that it may have created `~/.forge/` and shall remove an install lock this run created.
- [x] **INST-OUT-004**: The installer shall print progress lines and notices to stdout, and warnings to stderr as `forge-install: warning: <message>`.
