#!/usr/bin/env bash
# install.sh: register this repo as the global video-production skill repo.
#
#   ./install.sh                 link repo -> ~/.config/global-agent-skills/video-production, link skills into ~/.claude/skills
#   ./install.sh --venv          + create .venv and install requirements (add --optional for qdrant/whisper/youtube)
#   ./install.sh --npm           + npm ci (Remotion)
#   ./install.sh --mcp           + register the ffmpeg-bridge MCP server at user scope (claude CLI)
#   ./install.sh --uninstall     remove the links this script created (never touches real directories)
#
# Safe while other agents run: no process is started, stopped or restarted, no ports are bound,
# and existing non-symlink paths are never overwritten.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VP_HOME="${VP_HOME:-$HOME/.config/global-agent-skills/video-production}"
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
DO_VENV=0; DO_OPT=0; DO_NPM=0; DO_MCP=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --venv) DO_VENV=1 ;; --optional) DO_OPT=1 ;; --npm) DO_NPM=1 ;; --mcp) DO_MCP=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

link() {  # link <target> <linkpath>
  local target="$1" lp="$2"
  if [ -L "$lp" ]; then ln -sfn "$target" "$lp"
  elif [ -e "$lp" ]; then
    if [ "$(cd "$lp" 2>/dev/null && pwd -P)" = "$target" ]; then return 0; fi
    echo "SKIP $lp exists and is not a symlink (not overwriting)" >&2; return 0
  else ln -s "$target" "$lp"; fi
  echo "link $lp -> $target"
}

mapfile -t SKILL_FILES < <(cd "$REPO" && find . -name SKILL.md -not -path "./node_modules/*" | sort)

if [ "$UNINSTALL" = 1 ]; then
  for f in "${SKILL_FILES[@]}"; do
    name="$(sed -n 's/^name: //p' "$REPO/$f" | head -1)"
    [ -L "$SKILLS_DIR/$name" ] && rm "$SKILLS_DIR/$name" && echo "unlink $SKILLS_DIR/$name"
  done
  [ -L "$VP_HOME" ] && rm "$VP_HOME" && echo "unlink $VP_HOME"
  exit 0
fi

mkdir -p "$(dirname "$VP_HOME")" "$SKILLS_DIR"
[ "$REPO" != "$(cd "$VP_HOME" 2>/dev/null && pwd -P || true)" ] && link "$REPO" "$VP_HOME"

for f in "${SKILL_FILES[@]}"; do
  name="$(sed -n 's/^name: //p' "$REPO/$f" | head -1)"
  [ -n "$name" ] && link "$REPO/$(dirname "${f#./}")" "$SKILLS_DIR/$name"
done

command -v ffmpeg >/dev/null || echo "WARN ffmpeg not on PATH (or set VP_FFMPEG)" >&2

if [ "$DO_VENV" = 1 ]; then
  python3 -m venv "$REPO/.venv"
  "$REPO/.venv/bin/pip" install -q --upgrade pip
  "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"
  [ "$DO_OPT" = 1 ] && "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements-optional.txt"
  echo "venv ready: $REPO/.venv"
fi

if [ "$DO_NPM" = 1 ]; then (cd "$REPO" && npm ci --no-audit --no-fund); fi

if [ "$DO_MCP" = 1 ]; then
  PY="$VP_HOME/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
  CMD=(claude mcp add --scope user ffmpeg-bridge -- "$PY" "$VP_HOME/mcp-extensions/ffmpeg-bridge/server.py")
  if command -v claude >/dev/null; then
    if claude mcp get ffmpeg-bridge >/dev/null 2>&1; then echo "MCP ffmpeg-bridge already registered"
    else "${CMD[@]}"; fi
  else echo "claude CLI not found; register manually: ${CMD[*]}"; fi
fi

echo "done. Skills: $(ls -1 "$SKILLS_DIR" | grep -c '^vp-' || true) vp-* linked in $SKILLS_DIR"
