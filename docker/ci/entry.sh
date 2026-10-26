#!/usr/bin/env bash
# ci-entry — 容器内 CI 入口, 模式对齐 scripts/ci-local.sh:
#   lint(默认)  uv sync + Prek + Typos + BasedPyright
#   fast        lint + pytest tests/others
#   full        fast + pytest tests/parsers(需 full 镜像的 ffmpeg/deno)
#   其它任意    透传给 bash -c, 例: ci-entry "uv run pytest tests/others/test_foo.py -x"
set -u

cd /work || exit 1

# 代理: 默认借宿主 mihomo(17890)。PROXY=0 关闭, PROXY_HOST 可换地址。
if [ "${PROXY:-1}" != "0" ]; then
  _p="http://${PROXY_HOST:-host.docker.internal:17890}"
  export HTTP_PROXY="$_p" HTTPS_PROXY="$_p" http_proxy="$_p" https_proxy="$_p"
  export NO_PROXY="localhost,127.0.0.1" no_proxy="localhost,127.0.0.1"
fi

# 对齐 CI env: uv run 不再隐式 sync(已显式 uv sync --locked)
export UV_NO_SYNC=1

G=$'\033[1;32m'; Y=$'\033[1;33m'; R=$'\033[1;31m'; B=$'\033[1;34m'; N=$'\033[0m'
step() { printf '\n%s=== %s ===%s\n' "$B" "$*" "$N"; }
ok()   { printf '%s[ok]%s %s\n' "$G" "$N" "$*"; }
fail() { printf '%s[FAIL]%s %s\n' "$R" "$N" "$*"; }

FAILED=0
run() { # run <名称> <命令...>
  local name="$1"; shift
  step "$name"
  if "$@"; then ok "$name 通过"; else fail "$name 失败"; FAILED=$((FAILED+1)); fi
}

MODE="${1:-lint}"

if [ "$MODE" = "lint" ] || [ "$MODE" = "fast" ] || [ "$MODE" = "full" ]; then
  # 依赖: venv 在 named volume(/work/.venv), 首次装完后续秒级; --locked 与 CI 同源
  run "uv sync --locked" uv sync --locked

  # --- CI job: Prek (ruff-check + ruff-format + uv-lock + uv-sync) ---
  run "Prek (ruff/uv-lock/uv-sync)" prek run --all-files

  # --- CI job: Typos ---
  run "Typos" typos

  # --- CI job: BasedPyright (npm 版, venv=.venv 即挂载卷, 配置原样生效) ---
  run "BasedPyright" basedpyright

  if [ "$MODE" = "lint" ]; then
    printf '\n'
    [ "$FAILED" -eq 0 ] && { ok "lint 检查全部通过"; exit 0; } || { fail "$FAILED 项失败"; exit 1; }
  fi

  # --- CI job: Test (tests/others) ---
  run "Test (tests/others)" uv run poe test-other

  if [ "$MODE" = "fast" ]; then
    printf '\n'
    [ "$FAILED" -eq 0 ] && { ok "fast 档全部通过"; exit 0; } || { fail "$FAILED 项失败"; exit 1; }
  fi

  # --- CI job: Test Parsers (需 full 镜像: ffmpeg + deno; 真网络, 上游按出口 IP 风控,
  #     代理出口可能比 runner IP 好也可能更差, 失败先 PROXY=0 复跑再下结论) ---
  if command -v deno >/dev/null 2>&1 && command -v ffmpeg >/dev/null 2>&1; then
    run "Test (tests/parsers)" uv run poe test-parser
  else
    step "Test (tests/parsers)"
    printf '%s[skip]%s 非 full 镜像(缺 ffmpeg/deno), 跳过 parsers 测试\n' "$Y" "$N"
  fi

  # --- CI job: Test Render (playwright chromium) 不进容器: 浏览器体量大 +
  #     htmlrender 栈在 debian11 有专门踩坑记录, 本地 Windows 侧跑更可靠。 ---
  step "Test (tests/renders)"
  printf '%s[skip]%s render 测试不在容器内跑(需 playwright chromium)\n' "$Y" "$N"

  printf '\n'
  [ "$FAILED" -eq 0 ] && { ok "full 档检查完成"; exit 0; } || { fail "$FAILED 项失败"; exit 1; }
fi

# 透传: ci-entry "<任意 shell 命令>"
exec bash -c "$*"
