#!/usr/bin/env bash
# Claude Code PostToolUse hook (Edit|Write): auto-format the edited Python
# file and feed lint/type errors back to the model (plan WS1,
# tndm-workspace docs/log/2026-07-09_2041_plan_agent-infra-automation.md).
#
# stdin: the hook JSON; the edited file is at .tool_input.file_path.
# exit 0 = fine / not ours to check; exit 2 = errors on stderr -> Claude
# self-corrects. Anything unexpected (missing tools, unknown files) exits 0:
# the hook must never block edits for infrastructure reasons.
set -u

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

command -v jq >/dev/null 2>&1 || exit 0
file="$(jq -r '.tool_input.file_path // empty' 2>/dev/null || true)"
[ -n "$file" ] || exit 0
[ -f "$file" ] || exit 0

# aimformat has no env of its own (it is installed editable into the tndm
# env): prefer PATH, fall back to the main tndm conda env, else skip.
tool() {
  if command -v "$1" >/dev/null 2>&1; then
    command -v "$1"
    return
  fi
  local envbin="/opt/homebrew/Caskroom/miniforge/base/envs/tndm/bin/$1"
  [ -x "$envbin" ] && printf '%s' "$envbin" || true
}

# NB: in a `case` pattern (unlike pathname globbing) `*` also matches `/`,
# so these patterns match .py files at ANY depth under the repo root.
case "$file" in
  "$repo_root"/*.py)
    cd "$repo_root" || exit 0
    ruff_bin="$(tool ruff)"
    [ -n "$ruff_bin" ] || exit 0
    "$ruff_bin" format --quiet "$file" 2>/dev/null
    if ! lint_out="$("$ruff_bin" check --fix "$file" 2>&1)"; then
      printf 'ruff check failed for %s:\n%s\n' "$file" "$lint_out" >&2
      exit 2
    fi
    mypy_bin="$(tool mypy)"
    # mypy only for files under src/ (tests are ruff-only, matching CI).
    case "$file" in
      "$repo_root"/src/*)
        if [ -n "$mypy_bin" ]; then
          if ! type_out="$("$mypy_bin" "$file" 2>&1)"; then
            printf 'mypy failed for %s:\n%s\n' "$file" "$type_out" >&2
            exit 2
          fi
        fi
        ;;
    esac
    ;;
  *)
    exit 0
    ;;
esac

exit 0
