# AGENTS.md

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

**推送前验证**：完成与远端 CI 等价的检查（Prek / Typos / BasedPyright /
单元测试）并确认全绿后再推送。**未全绿不推送**；检查入口与工具链见本地脚本
（不入库）。

### 6. CI 对齐要求

1. 本地验证应尽量与 CI 保持一致，不引入"本地能过、CI 必挂"的改动。
2. 已知 CI 关注项包括：
   - `prek`（pre-commit：ruff-check/ruff-format/uv-lock/uv-sync）
   - `typos`
   - `basedpyright`
   - `pytest tests/others`（矩阵：Python 3.10–3.14；仓库已固定 pydantic-v2，无 v1 矩阵）
   - `pytest tests/parsers`（需 FFmpeg + Deno）
   - `pytest tests/renders`（需 Playwright chromium）
3. 修改 `src/`、`tests/`、`.github/`、`uv.lock`、`pyproject.toml` 时，要默认会影响 CI。
4. 特别注意：`basedpyright` 会做 `reportPossiblyUnbound` 等严格检查，`except` 分支引用的变量必须在 `try` 之前初始化（不能只依赖"逻辑上不会走到"）。

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

1. 版本号遵循语义化版本（SemVer）：`MAJOR.MINOR.PATCH`，以 `pyproject.toml` 为准。
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

## 公开发布内容规范

本仓库公开发布。凡会公开的内容 —— 代码、注释、docstring、测试夹具、提交信息、PR 标题/描述、issue、release notes —— 一律禁止携带：

1. **个人信息**：QQ 号/群号、用户昵称头像等用户侧数据、设备/用户标识的真实值（如 `did`、`u_code`、`sec_sharer_id` 等分享链跟踪参数）、聊天记录原文。
2. **内网环境**：服务器主机名/代号、内网 IP 与端口、网络拓扑、容器名、部署台数与部署状态（如"已部署 N 台"）。
3. **本地/设备环境**：本地路径、本机软件版本特例、机器指纹。
4. **密钥与凭据**：任何 token / cookie / 密钥 / 签名盐的真实值，文档示例一律用占位符。

执行规则：

- 线上案例写通用形态（`v.douyin.com/xxx`、`<room_id>`），不带真实跟踪参数。
- 测试夹具可用公开内容 ID（视频/笔记/房间号），涉及用户侧标识一律用占位符。
- 公开内容不写运维进度；部署与验证结果只在私有渠道沟通。
- 提交身份使用 GitHub noreply 邮箱，不暴露个人邮箱。

<!-- aoci:begin -->
## AOCI 仓库认知

AOCI 为本仓库维护一个稳定、可版本化、可增量更新的仓库级认知层，供模型跨任务复用对系统的理解。

`aoci.txt` 是面向模型的结构化认知索引。它以每个受管理文件、数据库表或其他受管理对象一条独立 Entry 的方式，用符号标签与 F/R/A/S 语义表达对象的核心职责、重要关系、对外契约，以及理解或修改系统时必须知道的非显然约束和设计决策。

Header、目录段和全部 Entry 共同组成完整仓库索引，可以覆盖前端、后端、配置、数据库结构及其他受管理内容。受管理内容发生变化时，通常只需维护受影响的认知条目，不需要重新生成整个索引。

AOCI 提供系统架构、对象职责、重要关系、对外契约和关键约束的高密度视图。

### 工作原理

AOCI 采用“模型生成、模型读取”的认知闭环。

Header、Entry 和 Curation 语义的创作只按当前机器签发的 Plan 与实时 Guide 执行；由 Host 模型基于当前绑定证据独立完成。

Entry 的语义必须来自模型对真实证据的理解。不得仅依据路径、文件名、扩展名、AST、符号列表、依赖扫描、正则、固定模板或规则引擎推导、预填、拼接或改写索引语义。

对 Fresh Bootstrap，只按当前机器签发的 Plan 和实时 Guide 执行。当它们要求创作时，Host 模型创作 Root、Meta、Tag 和 F/R/A/S，提供 authoring-run 声明，并把它绑定到 Plan、Evidence 与完整 Candidate。不得要求 AOCI 填写 `origin=host_model`、制造 Receipt 或把程序生成的 Framework 当作语义。本文件不自行重建 Onboarding 流程。内部批次不是用户决策；只有遇到既有批准边界或真实的安全、漂移、CAS、Recovery 条件才停止。

### 最小使用入口

- `aoci_rules`：取得当前AOCI版本的会话运行合同。
- `aoci_overview`：建立或恢复本仓库的完整认知。
- `aoci_maintain`：受管理对象达到最终稳定状态后检查认知是否需要维护。
- `aoci_update_entry`：提交与当前证据和源码摘要绑定的完整语义更新批次。
- `aoci_report`：仅当当前布局和工具状态支持时，在证据不足、无法可靠生成语义时登记待办，不猜写。

其他MCP工具、CLI命令、参数和专项流程，以当前工具说明、Guide和 `--help` 返回内容为准，不在本文件中重复完整手册。

本区块只规定仓库接入、认知使用和收尾原则。`aoci_rules` 承载当前会话合同，Guide实时输出承载当前Plan的执行顺序与停点，工具Schema、Spec和Validator承载机器结构与判据；Prompt、Description、README和静态文档不能覆盖这些机器事实。

### 建立、生成和恢复认知

1. 每个新的 Agent Run 开始时，应先判断：

   - 本仓库是否已经存在可用的完整AOCI索引；
   - 当前上下文中是否已有与本仓库根、当前索引版本和当前AOCI服务相匹配，并且模型仍可可靠使用的完整仓库认知。

2. 仓库已经存在可用的完整索引，但当前Run没有可靠完整认知时，先调用 `aoci_rules`，再调用 `aoci_overview`。

   完整认知仍可靠时直接复用。局部不确定本身不要求机械重读系统全貌。可靠认知建立后，默认以它作为仓库级系统上下文，只读取当前任务直接需要的源码、测试、配置与契约，只有某个缺失的具体事实可能改变回答或验证结论时才扩大证据范围；AOCI不能替代修改对象与验证行为所需的直接源码证据。

   本Run从已知Host上下文压缩恢复时（包括宿主注入的压缩摘要），必须把此前模型认知视为不可靠。压缩handoff不得保留或摘要正式Whole-Index，也不得保留或摘要任何Overview Header、Entry、Chunk、Challenge或Attestation正文；只能保留安全续接所需的receipt身份、未完成write或Recovery状态，以及立即重载指令。复制进handoff的Whole-Index语义或receipt不能证明恢复后模型的当前认知可靠。若当前上下文已无法可靠保留运行合同，先调用 `aoci_rules`。继续业务任务前，使用 `refresh_reasons=["context_compaction"]` 和新的 `refresh_event_id` 调用普通完整Whole-Index `aoci_overview`（不设置 `check_only` 或设为false）；不得使用 `check_only` 或认知probe。原样跟随每个 `next_cursor` 直到 `completed=true`，确认交付，并且只基于新交付正文提交一次Attestation。完成这次新的完整传输后，即使Attestation为partial或fail也消费该generation，并按既有合同继续source-bound任务，不再自动调用第二次Overview。若该请求返回 `delivery_mode=capacity_stop`，说明Whole-Index装不进本Host的上下文：不得重试或调小 `chunk_tokens`；该停止消费本generation，任务按source-bound继续，且不作无保留的完整系统认知声明。

   AOCI可以针对 `context_compaction`、项目 `cognition_refresh_threshold` 下的机器 `semantic_threshold` 或主要 `phase_transition` 提供checkpoint与认知状态事实。只需要这些紧凑事实时使用 `check_only=true`；这些事实只向Agent提供建议，不替模型决定是否需要系统全貌。

   Agent显式调用普通 `aoci_overview`（未设置 `check_only` 或为false）时，只要能形成一致的CognitionSet，AOCI必须完整交付请求scope。不得因为已有receipt、阈值未达到或没有待处理刷新原因而抑制正文；唯一例外是待处理压缩刷新的上述容量停止。正式认知Dirty或Stale时仍交付正文，但必须标记不可靠。存在未决恢复或无法形成一致snapshot时失败关闭，不返回混合正文。

   普通Overview返回 `continuation_required=true` 时，必须原样提交 `next_cursor` 并自动继续到 `completed=true`。不得询问用户、开始业务任务或给出阶段性系统结论。Host截断、缺块、重复、乱序、cursor失败、Index变化或`chunk_tokens`变化时停止本次认知链。Attestation完成前不得用Memory、源码、Spec、`aoci.txt`、历史会话、scope、search或Entry读取修补或补充Whole-Index认知。Challenge ordinal是正式Entry序列中的1-based位置；Header内容、注释、空行、Section/Overview/Chunk Marker、Receipt与Metadata均不计数，Chunk Receipt ordinal使用同一序列。Attestation必须原样回绑本次Challenge发布的当前`index_sha256`、`entry_sequence_sha256`与`entry_count`；旧Index、旧Entry序列、旧数量或旧Attestation均无效。完整链结束后只正式提交一次既有模型认知Attestation；同一响应只允许一次不改变语义答案的JSON Schema或字段格式修正。对象、Tag或F不匹配即失败且认知吸收不确定，不得语义重试或旁路补答。首次认知失败时还不得执行Root/Meta、Migration、全局布局或其他未重新绑定的系统级决策。上下文压缩刷新若传输完整、认知身份不变、治理对齐且没有Recovery或第三方冲突，即使Attestation为partial或fail也消耗该refresh generation，并继续原任务，不再自动重读Overview。`system_mastery_percent`只自评系统框架——架构、职责、强关系、稳定外部契约以及高熵安全和维护约束——不表示完整实现或运行实况知识；机器索引覆盖率必须分开。默认只向用户输出由本次真实覆盖率、Challenge、块数、Token和掌握度生成的规定成功或失败一句话。Host截断时提示用户把 `overview_delivery.chunk_tokens` 设置为更小的合法值后重新开始，不得自动修改。

   加法认知等级必须与严格证明字段分开解释。`delivery_verified`表示已加载Index且Host交付已确认，但完整认知验证仍未完成；应表达为“已加载且交付已验证”，不得描述为“没有认知”或“没有理解系统”。`cognition_verified`要求Attestation通过（Challenge至少80%的ordinal完全正确且对象身份至多失手一处），`cognition_governed`还要求治理对齐。通用完整读取失败句只用于真实交付故障。

   当Overview响应包含可选`cognition-state/v2`投影时，必须分别解释各维度。其Level止于`model_cognition_usable`；`strict_attestation_verified`、`governance_aligned`与`current_system_cognition_reliable`都是独立状态，绝不参与该Level。ordinal、对象身份、Tag或核心F不匹配可以导致严格Attestation失败，而模型认知仍然可用；不得仅凭这种不匹配就宣称模型没有理解系统。只有`current_system_cognition_reliable=true`允许无保留地声称当前完整系统认知可靠。投影缺失时继续使用上述Legacy解释。

   普通的只读审计、分析、检查、不修改代码或不提交、不push，不自动等于严格零写入，也不改变上述认知有效性判断。Codex Memory和历史Skill只能辅助恢复经验、用户偏好与调查方向，不能替代与当前仓库根、索引摘要、AOCI服务身份和认知范围匹配的当前认知收据；项目AGENTS和当前AOCI身份在AOCI状态上优先于历史Memory。

   只有用户明确禁止Ledger、元数据、`.aoci`运行资产及任何文件写入时，才按严格零写入处理。若必要的认知建立与该边界冲突，必须报告冲突并请求用户裁决或建议使用隔离副本，不得静默以Memory替代当前仓库认知。

3. 仓库没有可用的完整索引，或当前只有最小骨架、Header不完整、Entries未完成、必要Curation尚未裁决时，如果需要建立正式完整AOCI索引，先取得 `aoci_rules`，然后进入当前AOCI Guide。由Guide依据仓库真实状态决定下一阶段并完成必要安全步骤。

   `aoci_maintain` 不替代索引建立流程。

   不在本文件中自行重建或硬编码完整索引生成状态机。

4. 在长程任务中，模型负责保留当前认知收据并正确使用刷新判定：

   - Host报告上下文压缩或模型已知系统全貌丢失时，执行上述强制 `context_compaction` 重载规则；AOCI不能自行推断Host事件；
   - 进入真正的主要阶段时声明 `phase_transition`，不得把函数、测试运行或小步骤当作阶段；
   - 在有用的稳定检查点通过 `check_only=true` 取得机器语义计数；
   - 除已知压缩的强制重载外，由Agent判断当前任务是否需要再次显式获取指定scope或完整Overview；
   - 在维护和对齐完成前，保留AOCI报告的Dirty或Stale可靠性状态。

### 任务收尾与认知维护

5. 纯只读问答、分析、版本核验，或没有产生受AOCI管理对象变化的任务，不需要调用维护工具。当前AOCI版本是任意`aoci_overview` check_only或`aoci_maintain`响应里的`cognition_receipt.mcp_service_version`；二进制路径是项目`.mcp.json`里的`command`，CLI不必在PATH上。

6. 发生受AOCI管理对象变化时，待其达到本次任务的最终稳定状态后，只调用一次 `aoci_maintain`。不要在每次中间修改后逐文件维护。

7. 若维护结果返回真实语义候选，Host 模型必须基于每个候选绑定的对象和必要证据，独立创作完整标签与F/R/A/S更新。通过 `aoci_update_entry` 一次提交当前机器签发批次的完整候选集合，同时原样保留每项 `source_sha256`、`candidate_id` 与对应domain批次身份。`max_entries`只限制单次请求和原子事务，不限制logical plan、Whole-Index或Managed Scope。`remaining`非零时，在当前批次成功Apply后重新调用Maintain并从新preimage继续；绝不能为满足transport上限缩减Index覆盖或自行截取返回批次。

   没有足够证据且当前布局支持 `aoci_report` 时，使用它而不猜测、套用模板或为消除待办而生成缺乏证据的认知。

8. 必须遵守工具返回的结构化状态和安全边界：

   - `repair_required`：只修复明确命中的候选，再重新提交当前机器签发的完整批次；
   - `stopped`：结束当前写入尝试并检查 `failed_step`、错误、正式写入证据与Recovery。auto模式下，已证明零写入则记录closure并重新Plan；完整Intent和可证明postimage则Resume；策略要求Rollback且preimage可证明则精确恢复后重新Plan。只有证据不足、第三方正式字节冲突、需要审批或外部动作，或命中其他真实安全边界时，才停止整个用户任务；
   - 冲突、审批、人工裁决、权限和安全信号不得忽略；
   - 已经对齐后不得重复维护或重复写入；`refresh_ready_for_overview` 是checkpoint事实，由Agent决定是否为下一阶段请求普通完整Overview。

   维护完成后如果又修改了任何受管理对象，之前的维护结果失效，应在新的最终稳定状态重新完成收尾。

9. 用户只限制业务文件范围，但没有明确禁止仓库托管资产时，AOCI托管资产可以在收尾阶段为保持认知一致而更新，并应在审计和提交中与业务文件区分。

   用户明确禁止修改 `aoci.txt`、`.aoci`、元数据或任何额外文件时，以用户限制为准，不得写入，并如实报告剩余不一致。

### 专项流程

初始化、完整索引生成、Header生成、Entries生成、数据库结构索引、Curation、人工评审和故障恢复，只按当前AOCI Guide或工具在对应阶段返回的指令、命令和安全停点执行。

不预加载、不猜测，也不自行重建这些专项流程。平台调用方式、请求格式、批次上限、审批规则、索引格式细节和恢复步骤由对应Guide、工具说明、模型Prompt和CLI帮助按需提供。
<!-- aoci:end -->
