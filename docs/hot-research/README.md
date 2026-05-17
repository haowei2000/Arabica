# Hot Research Implementation

Hot Research Implementation is a daily automation that turns the latest
`Structure Research Radar` output into implementation work. It runs after the
research scan so it can consume the latest feasible tasks.

## Schedule

- Time: 09:10 daily
- Branch: `hot-research`
- Execution environment: isolated worktree
- PR base: `develop`

## Inputs

The job reads:

- latest `docs/research-radar/YYYY-MM-DD.md`
- `docs/research-radar/README.md`
- `README.md`
- `docs/roadmap.md`
- `benchmarks/README.md`
- `benchmarks/`
- `paper/`

## Task Selection

Prioritize Research Radar follow-up tasks that can be implemented, tested, and
reviewed in one run:

- benchmark adapters, fixtures, scorers, and baseline updates
- benchmark report schema improvements
- small tool/runtime fixtures
- security smoke fixtures
- documentation and methodology updates
- LaTeX paper, table, result, and citation updates grounded in implemented work

Skip tasks that require unavailable proprietary datasets, unsafe execution,
secrets, paid APIs, or heavy infrastructure that cannot run locally. Record the
skip reason in the PR and email.

## Verification

The job should run real verification:

- targeted unit tests for changed code
- relevant integration tests
- benchmark commands from `benchmarks/README.md`
- strongest available local fixture or smoke benchmark when full datasets are
  unavailable
- LaTeX compilation for `paper/`

Do not claim benchmark improvements unless the command output supports them.
Neutral, fixture-only, or failed benchmark results should be reported plainly.

## Deliverables

Each run should produce:

- implemented code/doc/paper changes on branch `hot-research`
- compiled LaTeX PDF when available
- implementation report under `docs/research-radar/`
- commit using Conventional Commits
- pull request from `hot-research` into `develop`
- bilingual HTML email to configured recipients

Configured recipients:

- `wanghw00@gmail.com`
- `2021101040028@whu.edu.cn`
- `1161252028@qq.com`

## Email Requirements

Subject:

```text
Hot Research Implementation - YYYY-MM-DD
```

The email must include:

- implemented tasks
- skipped tasks with reasons
- tests and integration tests run
- benchmark before/after or fixture results
- PR link
- changed files summary
- compiled LaTeX PDF attachment when available

If the PDF, PR, or email cannot be produced, write the draft or failure reason
to a Markdown report in the repository.
