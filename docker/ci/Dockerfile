# nonebot-plugin-parser 本地容器化 CI
# 复刻 .github/workflows/ci.yml 可本地快速跑的部分:
#   Prek (ruff/uv-lock/uv-sync) + Typos + BasedPyright + test-other
# full 变体(--build-arg FULL=1)追加 test-parser 所需的 ffmpeg + deno。
# test-render 需要 playwright chromium, 体量大且上游按 IP 风控, 不进容器。
#
# 构建代理: build 时 GitHub/ghcr/npm 均需代理, scripts/ci-docker.sh 已自动传
# --build-arg http_proxy=http://host.docker.internal:17890 (宿主 mihomo)。
#
# 运行期设计(见 scripts/ci-docker.sh):
#   /work        bind mount 仓库(含 .git, prek 需要枚举跟踪文件)
#   /work/.venv  named volume 遮盖 —— 容器 venv 不污染 Windows 侧 .venv,
#                且 pyproject [tool.pyright] venv=".venv" 原样生效
#   /root/.cache named volume —— uv/prek 缓存持久化, 二次运行秒级

FROM ghcr.io/astral-sh/uv:python3.12-bookworm

ARG TYPOS_VERSION=1.51.1
ARG FULL=0
# build 阶段代理(由 ci-docker.sh 注入, 未传则直连)
ARG http_proxy=""
ARG https_proxy=""

# git: prek 需在 git 仓库内工作; curl/ca-certificates: 装工具用
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      git ca-certificates curl xz-utils

# node 22: basedpyright 要求较新 node, bookworm 自带 18 偏旧(与 CI 用
# ubuntu-latest 预装 node 同理走 npm 版, 绕开 PyPI 版捆绑的 nodejs-wheel 挂起问题)。
# 不用 COPY --from=node:22: daemon 拉 docker.io auth 被直连重置, 改走 nodejs.org
# 官方 tarball(RUN 内代理可达); bin/npm 是指向 ../lib 的相对软链, 解包后依然有效
ARG NODE_VERSION=v22.14.0
RUN curl -fsSL "https://nodejs.org/dist/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz" \
      | tar -xJ -C /usr/local --strip-components=1 \
 && node --version \
 && npm install -g basedpyright

# typos: 与 CI crate-ci/typos@master 对齐, 固定版本保证本地/容器一致
# (tarball 内带版本目录层级, find 定位二进制安装)
RUN curl -fsSL \
      "https://github.com/crate-ci/typos/releases/download/v${TYPOS_VERSION}/typos-v${TYPOS_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
      -o /tmp/typos.tar.gz \
 && mkdir /tmp/typos-x && tar -xzf /tmp/typos.tar.gz -C /tmp/typos-x \
 && install -m755 "$(find /tmp/typos-x -type f -name typos)" /usr/local/bin/typos \
 && rm -rf /tmp/typos.tar.gz /tmp/typos-x \
 && typos --version

# prek: 与 CI prek job 同工具(rust 版 pre-commit, PyPI 轮子)
RUN uv tool install prek

# 多项目复用(经 D:/app/bin/ci-box.sh):
#   libegl/libgl 等 —— meme-generator 的 skia-python(pil_utils 渲染) 运行库
#   pnpm 11  —— node 项目(hw-img-host vitest/eslint); store 指向共享缓存卷
#   poetry   —— poetry.lock 项目(meme-generator); venv 就地建 /work/.venv
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
      libegl1 libgl1 libfontconfig1 libx11-6 libxext6 \
 && npm install -g pnpm@11 \
 && pnpm config set --global store-dir /root/.cache/pnpm-store \
 && uv tool install poetry

ENV POETRY_VIRTUALENVS_IN_PROJECT=1

# full 变体: test-parser job 需要 ffmpeg(短视频合成) + deno(部分平台签名)
RUN if [ "$FULL" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ffmpeg \
      && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
      && ffmpeg -version | head -1 && deno --version | head -1; \
    fi

ENV PATH="/root/.local/bin:${PATH}" \
    UV_PROJECT_ENVIRONMENT=/work/.venv \
    UV_PYTHON_DOWNLOADS=never \
    PREK_HOME=/root/.cache/prek

COPY entry.sh /usr/local/bin/ci-entry
# 防 CRLF(Windows 侧编写时): bash 见 \r 会语法报错
RUN sed -i 's/\r$//' /usr/local/bin/ci-entry && chmod +x /usr/local/bin/ci-entry

WORKDIR /work
ENTRYPOINT ["ci-entry"]
