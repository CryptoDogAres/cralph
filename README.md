# cralph

**Claude + Ralph** — AI-powered feature planning and implementation for any software project.

cralph adapts the [Ralph Loop](https://block.github.io/goose/docs/tutorials/ralph-loop/) pattern into a two-phase workflow: an iterative plan generation loop powered by Claude Sonnet 4.6, followed by parallel implementation via the OpenAI Codex CLI.

```
cralph generate "Add OAuth2 authentication with GitHub and Google"
cralph build
```

---

## How it works

### Phase 1 — `generate`

Runs a **6-iteration plan/review loop** using Claude Sonnet 4.6:

```
Iteration 1..6:
  Planner  (fresh context) → reads task + previous feedback → writes plan draft
  Reviewer (fresh context) → reads task + draft → outputs APPROVED or REVISE
  If APPROVED → finalize plan and stop early
  If REVISE   → record feedback, continue to next iteration
  If iter 6   → finalize plan regardless
```

Each iteration starts with a fresh context window — no accumulated noise from prior attempts. State is persisted in files so nothing is lost between iterations.

### Phase 2 — `build`

Decomposes the approved plan into a **task dependency graph (DAG)**, then executes tasks using the OpenAI Codex CLI:

```
Claude decomposes plan → JSON task DAG
Topological sort → execution levels
Each level: parallel Codex subagents (max 500 concurrent)
Claude aggregates results → build report
```

Tasks in the same level are independent (disjoint file ownership), so they safely run in parallel. Tasks with dependencies wait for their level to complete first.

---

## Installation

**Prerequisites:**
- Python 3.11+
- `ANTHROPIC_API_KEY` environment variable set
- [OpenAI Codex CLI](https://github.com/openai/codex) installed (`codex` in PATH)

**Install cralph globally:**

```bash
git clone https://github.com/CryptoDogAres/cralph.git
cd cralph
pip install -e .
```

Or with pipx (recommended for global CLI tools):

```bash
pipx install .
```

---

## Usage

Run from the root of any software project.

```bash
# Generate a plan for a feature
cralph generate "Add rate limiting middleware to the Express API"

# Build the implementation from the approved plan
cralph build

# Check progress
cralph status

# Read the approved plan before building
cralph review

# List all features in the project
cralph list

# Read the build report
cralph report

# Retry failed build tasks
cralph retry

# Target a specific feature by ID
cralph build add-rate-limiting-a3f9b2c1
cralph status add-rate-limiting-a3f9b2c1
```

---

## State

cralph stores all state in `.cralph/` within your project directory (add to `.gitignore` or commit it for team visibility).

```
.cralph/
└── <feature-id>/
    ├── task.md               # original intent (immutable)
    ├── plan-draft.md         # working plan (updated each iteration)
    ├── plan-feedback.txt     # reviewer feedback for next iteration
    ├── plan-result.txt       # APPROVED | REVISE
    ├── plan-iteration.txt    # current iteration (1-6)
    ├── plan-final.md         # locked approved plan
    ├── feature-status.json   # state machine + task statuses
    ├── build-tasks.json      # decomposed task DAG
    ├── build-report.md       # final aggregated build summary
    └── build-log/
        └── task-001.md       # per-task Codex output
```

Feature IDs are `<slug>-<8hexchars>`, e.g. `add-oauth2-auth-a3f9b2c1`.

---

## Configuration

All settings via environment variables:

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | required | Anthropic API key |
| `CRALPH_PLAN_MODEL` | `claude-sonnet-4-6` | Model for planning/reviewing/decomposing |
| `CRALPH_BUILD_MODEL` | `codex-5.4` | Model passed to Codex CLI |
| `CRALPH_MAX_PLAN_ITERATIONS` | `6` | Max plan refinement iterations |
| `CRALPH_MAX_BUILD_SUBAGENTS` | `500` | Max concurrent Codex processes |
| `CRALPH_CODEX_CMD` | `codex --model {model} --approval-mode full-auto {task}` | Codex invocation template |

---

## Design principles

- **Fresh context per iteration** — no accumulated conversation noise; state lives in files
- **Cross-role review** — planner and reviewer use separate system prompts and clean contexts
- **File-disjoint parallelism** — the decomposer is instructed to assign file ownership per task, enabling safe parallel execution
- **Stateful recovery** — `cralph retry` re-runs only failed tasks; `cralph status` shows exactly where things stand
- **Any project** — `cralph` operates on whatever directory you run it from

---

## License

MIT
