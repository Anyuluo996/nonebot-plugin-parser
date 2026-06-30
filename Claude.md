# Claude.md

## 适用范围

本文件用于约束本仓库的代码修改、测试编写、提交前验证、版本发布与分支协作行为。

项目为 `NoneBot2` 链接解析插件，主代码位于 `src/nonebot_plugin_parser/`，测试位于 `tests/`。

## 基础约定

1. 使用 `uv` 作为项目命令入口与依赖管理工具。
2. Python 版本下限为 `3.10`，新增代码需兼容 `pyproject.toml` 中声明的版本范围（当前 3.10–3.14）。
3. 修改依赖时优先使用 `uv add` / `uv remove` / `uv sync`，不要手改锁文件。
4. 不提交本地运行产物、缓存文件、临时调试文件或测试输出。

## 代码规范标准

### 1. 风格与格式

1. 以 `ruff` 配置为准：
   - 行长上限 `120`
   - 行尾使用 `LF`
   - 导入顺序交给 `ruff` / `isort` 规则维护
2. 保持现有代码风格一致，优先做最小必要修改，不做与当前任务无关的重构。
3. 新增或修改类型标注时，兼容运行时类型检查需求，避免破坏现有运行时注解行为。
4. 禁止留下 `print`、调试断点、临时注释掉的重要逻辑。

### 2. 导入与模块组织

1. 优先沿用当前模块的导入风格，不混用多种风格。
2. 仓库一方包按 `nonebot_plugin_parser` 视为 first-party。
3. 测试代码按 `tests` 视为 first-party 测试模块。
4. 不为了规避循环依赖而随意下沉导入；确需下沉时，应写清原因并保持局部最小化。

### 3. Python 与数据结构约束

1. 避免可变默认值，如 `[]`、`{}`、`set()` 直接作为默认参数或结构默认值。需要可变默认值时使用 `field(default_factory=…)`（dataclass）或等价写法。
2. 缓存、全局字典、锁表等运行态结构要考虑生命周期与清理策略，避免无限增长。
3. 需要持久化的数据应放在项目既有数据目录约定下，不要随意新增散落文件。
4. 修改数据模型时，同步检查类型标注与运行时返回值是否一致。

### 4. 异步与资源管理

1. 网络请求、下载器、客户端等资源必须显式管理生命周期。
2. `async` 代码中优先使用异步 API，避免阻塞式文件或网络操作混入主流程。
3. 新增缓存、下载、渲染等逻辑时，要考虑异常路径是否会遗留状态或资源。

### 5. NoneBot / 插件开发约定

1. 修改 matcher、handler、rule、permission 时，必须同时确认：
   - 命令是否能被正确命中
   - 权限是否符合预期
   - `to_me()` / `block=True` 等行为是否符合当前功能设计
2. 使用 NoneBot 依赖注入时，参数类型标注必须写成"可注入的真实类型"，不要把参数提供器写进类型标注。
3. 命令参数注入统一遵循类似如下模式：处理命令参数时，优先使用 `args: Message = CommandArg()` 这一类写法，而不是把 `CommandArg` 写成参数注解类型。
4. 使用 `Session = UniSession()` 一类注入时，要同时考虑群聊与私聊路径。
5. 新增解析器若继承公共基类，初始化时必须调用 `super().__init__()`，除非有明确理由且已验证无副作用。

### 6. 日志与错误处理

1. 使用 `nonebot.logger` 记录必要日志，禁止使用标准库 `logging`。
2. 问题定位期间可加临时日志，但修复完成后应尽量收敛为必要日志，避免长期保留噪声调试输出。
3. 错误处理应尽量给出可定位信息，但不要吞掉会影响排障的关键异常上下文。

### 7. 平台解析器健壮性约定

平台解析器是本项目核心域，上游 API 易变、易踩坑，新增或修改解析逻辑时须遵守：

1. **超时**：所有网络请求、子进程调用、浏览器渲染必须有显式 `timeout`。
   - 浏览器渲染：`browser.py` 以 `timeout_ms`（默认 30_000ms）控制页面加载。
   - 子进程下载（tdl/ytdlp）：`_run` 以 `timeout`（默认秒级）控制，超时抛 `ParseException`。
   - 不允许出现"无超时的 `await`"。
2. **重试与降级**：对外部依赖不稳定的关键路径须有兜底。
   - 浏览器渲染遇传输断连，走 `browser_retry.with_browser_retry` 重启并重试一次。
   - 上游接口字段变更时优先做兜底解析（如 bilibili hvc1/hev1 兜底识别），而非直接抛错。
3. **限流**：注意各平台 API 频率限制，避免短时间内对同一接口高频请求；需要时引入最小间隔或退避。
4. **并发去重**：对同一资源的并发请求（下载、渲染、登录二维码）须做单飞（singleflight）去重，避免重复下载/重复占用资源；相关锁/任务表需有清理策略。

### 8. 凭据与安全约定

1. **Cookie / Token 禁止入库**：`parser_bili_ck`、`parser_ytb_ck`、`parser_pixiv` 等真实凭据不得提交到仓库。
2. 本地测试用的 `.env.test` 已被 `.gitignore` 忽略，不得重新 `git add`；如需提交测试环境样例，使用 `.env.test.example` 仅含占位符。
3. 凭据相关的 netscape cookie 文件、token 缓存等运行态产物，仅写入 `localstore` 缓存目录，不入库、不入日志明文。
4. 日志中不得打印完整 cookie / token 明文，必要时脱敏（仅前若干字符）。

### 9. 文档同步

1. 修改用户可见行为、命令格式、配置项、权限规则时，需要同步检查 `README.md` 与相关文档是否过期。
2. 重大修复或较大功能变更的总结文档统一放在 `docs/` 下（按用途归入 `docs/features/`、`docs/merge-analysis/`、`docs/merge-history/`）。
3. 小改动不强制补总结文档，但如果改动改变了使用方式，至少要补主文档说明。

## 测试标准规范

### 1. 测试框架与目录

1. 测试框架使用 `pytest`。
2. 异步测试使用 `pytest-asyncio`，并遵循仓库现有配置：
   - `asyncio_mode = auto`
   - loop scope 由 `tests/conftest.py` 统一设为 `session`，不要在单个测试里覆盖。
3. 测试文件命名使用 `test_*.py`。
4. 按现有结构放置测试：
   - 通用或入口逻辑放 `tests/`
   - 平台解析器测试放 `tests/parsers/`
   - 渲染测试放 `tests/renders/`
   - 其他辅助测试放 `tests/others/`
   - 测试输出快照（API 抓取数据、渲染结果）放 `tests/pipeline_output/`、`tests/render_output/`，这些目录已被 `typos.toml` 与 lint 排除，不入版本管理主体。

### 2. 调试脚本约定（重要）

1. **CI 只收集 `tests/parsers/`、`tests/others/`、`tests/renders/` 三个子目录**；`tests/` 根目录下的 `test_*.py` 不在 CI 收集范围，但会被本地默认 `pytest` 收集，可能拖慢或误报。
2. 一次性调试/对比脚本不要使用 `test_` 前缀，放到 `tests/` 根目录且命名清晰（如 `inspect_*.py`、`compare_*.py`），或归入 `tests/scratch/`。
3. 已经存在的根目录历史调试脚本属于历史遗留，新增功能时不要继续堆砌同类脚本；能用回归测试覆盖的，优先写成 `tests/others/` 下的正式测试。
4. 不要为了验证单次修改去新建临时测试脚本。

### 3. 编写原则

1. 新功能必须补测试；修 bug 必须补能锁定问题的回归测试。
2. 优先写最小作用域、最稳定的测试：
   - 先单元测试
   - 再文件级行为测试
   - 最后才是完整集成链路测试
3. 测试应尽量避免依赖不稳定外部环境；能 mock 的网络请求优先 mock（使用 `respx`）。
4. 对 NoneBot 注入、matcher 行为等问题，优先测试"签名是否正确""handler 是否能处理真实消息对象"这类高稳定性断言。
5. 对解析器、缓存、权限、状态开关类逻辑，要覆盖至少一个正常路径和一个关键边界路径。

### 4. NoneBot 相关测试约定

1. 依赖 `tests/conftest.py` 初始化 NoneBot 与 OneBot V11 适配器，不要重复造初始化逻辑。
2. 测试环境通过 `ENVIRONMENT` 环境变量区分（存在 `.env.test` 时为 `test`，否则为 `dev`），由 `conftest.py` 自动设置，测试代码无需手动设置。
3. 涉及命令参数注入时，优先验证：
   - handler 参数签名是否正确
   - 真实 `Message` 对象能否被正确处理
4. 如果完整 matcher 集成测试容易被 fake adapter、会话注入或外部插件干扰，可退一步写更稳定的行为级回归测试，但必须能锁定实际缺陷。

### 5. 提交前最小验证要求

任何代码修改完成后，至少执行以下检查：

1. 语法检查
   - `uv run python -m compileall src`
   - 如改了测试文件，同时对相关测试文件执行 `compileall`
2. 定向测试
   - 只运行与本次改动直接相关的最小 pytest 范围
3. Diagnostics 检查
   - 相关源码与测试文件应无 IDE 报错

如改动涉及风格、导入、静态类型或 CI 相关问题，追加执行：

1. `uv run ruff check <相关路径>`
2. 必要时执行 `uvx basedpyright`

### 6. CI 对齐要求

1. 本地验证应尽量与 CI 保持一致，不引入"本地能过、CI 必挂"的改动。
2. 已知 CI 关注项包括：
   - `prek`（pre-commit：ruff-check/ruff-format/uv-lock/uv-sync）
   - `typos`
   - `basedpyright`
   - `pytest tests/others`（矩阵：Python 3.10–3.14 × pydantic-v1/v2，3.14 排除 v1）
   - `pytest tests/parsers`（需 FFmpeg + Deno）
   - `pytest tests/renders`（需 Playwright chromium）
3. 修改 `src/`、`tests/`、`.github/`、`uv.lock`、`pyproject.toml` 时，要默认会影响 CI。

## Git 协作规范

### 1. 分支策略

1. **`master` 为发布分支**，保持可发布、CI 全绿；只接受来自 `dev` 或特性分支的合并，不直接在 `master` 上开发。
2. **`dev` 为开发分支**，集成日常功能与修复。
3. **特性分支**：新功能 / 修复从 `dev` 拉取，命名 `<type>/<简述>`，如 `feat/telegram-cover`、`fix/bilibili-codec`。
4. **依赖更新**统一走 Renovate 自动 PR（`renovate.json` 配置），不在特性分支里手改依赖版本。

### 2. 合并策略

1. `dev` → `master` 合并使用 **merge commit**（`--no-ff`），保留双分支历史，便于追溯。
2. 特性分支 → `dev` 视情况用 squash 或 merge commit，小型改动可 squash 压成单提交。
3. 合并前确认目标分支已 `git pull --ff-only` 与远端同步；冲突在合并分支上解决，不在被合并分支上解决（除非冲突明确属于被合并方）。
4. 合并提交信息使用 `Merge <源分支> into <目标分支>`，并在正文简述带入的内容与冲突解决说明。

### 3. Commit Message 规范

本仓库使用 **Conventional Commits**，格式：

```
<type>(<scope>): <subject>
```

1. **type 白名单**（与实际历史一致）：`feat` / `fix` / `refactor` / `chore` / `docs` / `test` / `perf` / `build` / `ci` / `style` / `revert`。
2. **scope** 可选，使用模块或平台名：`bilibili` / `douyin` / `kuaishou` / `weibo` / `xiaohongshu` / `youtube` / `twitter` / `telegram` / `acfun` / `nga` / `pixiv` / `download` / `render` / `deps` / `renovate` / `ci` / `lint` / `cookie` 等。
3. **subject** 用中文简述（与仓库历史风格一致），祈使语气，不加句号。
4. 依赖更新类遵循 Renovate /既有风格，如 `chore(deps): …`、`fix(deps): …`。

## 版本发布规范

1. 版本号遵循语义化版本（SemVer）：`MAJOR.MINOR.PATCH`（当前 2.7.19）。
2. 发版使用 `bump-my-version`，通过 poe 任务：
   - `uv run poe bump patch`（默认）/ `minor` / `major`
   - `uv run poe show-bump` 查看可用的 bump 配置
3. `bump` 会自动：修改 `pyproject.toml` 与 `uv.lock` 中的版本 → 提交（`commit=true`）→ 打 tag（`tag=true`）。提交信息格式为 `release: bump version from {current_version} to {new_version}`（沿用现有模板）。
4. 发版动作只在 `master` 上执行；发版 tag 推送后由 `release.yml` / `release-draft.yml` workflow 负责构建与草稿 Release。

## 推荐执行顺序

1. 先确认修改范围与影响模块。
2. 再补最小必要代码。
3. 同步补回归测试。
4. 执行语法检查、定向 pytest、diagnostics。
5. 若改动影响配置、命令或使用方式，再补文档。
6. 提交前确认 commit message 符合规范、改动未夹带凭据/产物。

## 本仓库特别注意事项

1. 平台控制、强制解析、权限判断这类逻辑很容易因 rule / permission / 注入签名细节出问题，修改时必须同时看源码与回归测试。
2. 解析器和下载器代码要特别注意异步资源关闭、缓存副作用和返回类型一致性。
3. 渲染与外部平台解析常受环境影响，测试时优先选择最小、最稳定的验证方式。
4. `.env.test` 含本地凭据占位符，已被 `.gitignore` 忽略，严禁提交真实凭据或重新跟踪该文件。
