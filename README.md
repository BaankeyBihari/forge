# Forge

A multi-agent development orchestrator for [Claude Code](https://code.claude.com).

A single coding-agent session that plans, tests, implements, reviews, and publishes a change carries every role's context at once. It loses track of the stage it is in, edits tests to make code pass, overwrites code the developer wrote by hand, pushes work no independent check passed, and forgets everything when the session ends. Forge keeps the roles apart, has scripts rather than the agent's own judgment check every transition, respects human-authored code, and lets a change stop and resume days later.

## How it works

- **Hub and spokes.** The `/forge` skill (the Commander) runs in your main Claude Code session and is the only part that talks to you. It dispatches **spokes**: subagents with one role and a fixed write boundary (planner, qa, dev, verifier, reviewer, scout, publisher, reporter). A **council** of persona subagents weighs in when a spoke is blocked, and you approve its ruling.
- **Scripts own deterministic work.** Routing, commit and publish gates, path guards, workstream state, and memory are standard-library Python scripts in `reference/scripts/`, so every transition is decided the same way each time.
- **Human code is protected.** A line whose last change came from a commit without a `Forge-Agent` trailer is human-authored; agents change it only with your recorded approval.
- **You merge.** Forge pushes only work the verifier and reviewer both passed, and never merges into your main branch itself.

## Status

Under active development. Designs, specs, scripts, agent profiles, and skills for the hub and spokes exist and the pytest suite passes; the installer and an end-to-end sandbox run are next.

## Layout

```
reference/   CLI-neutral scripts (and their tests) and skills
claude/      Claude Code adapter: Commander skill, agent profiles, guard hook
docs/        Forge's own design tree: high-level design, component designs, EARS specs
```

Forge is built with linked-intent development: start at `docs/high-level-design.md`, then `docs/intent/`.

## Tests

```bash
uvx pytest reference/scripts/tests claude/hooks/tests -q
```

## License

[MIT](LICENSE)
