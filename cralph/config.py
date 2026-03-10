import os

# Models
PLAN_MODEL = os.getenv("CRALPH_PLAN_MODEL", "claude-sonnet-4-6")
BUILD_MODEL = os.getenv("CRALPH_BUILD_MODEL", "codex-5.4")

# Loop limits
MAX_PLAN_ITERATIONS = int(os.getenv("CRALPH_MAX_PLAN_ITERATIONS", "6"))
MAX_BUILD_SUBAGENTS = int(os.getenv("CRALPH_MAX_BUILD_SUBAGENTS", "500"))

# Codex CLI invocation template — {model} and {task} are substituted at runtime
CODEX_CMD = os.getenv(
    "CRALPH_CODEX_CMD",
    "codex --model {model} --approval-mode full-auto {task}",
)

# State directory name (relative to project root / cwd)
STATE_DIR = ".cralph"
