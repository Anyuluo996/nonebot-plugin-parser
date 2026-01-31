# Fork 项目与原项目差异对比报告

生成时间: 2025-01-31
原项目: https://github.com/fllesser/nonebot-plugin-parser
Fork项目: https://github.com/Anyuluo996/nonebot-plugin-parser

## 1. 版本信息

**原项目 (upstream/master):**
- 最新版本: v2.3.8 (commit 15653d7)
- 距离共同祖先 (78225f2) 的新提交: 9 个

**Fork 项目 (master):**
- 当前版本: 自定义版本
- 距离共同祖先 (78225f2) 的新提交: 11 个

## 2. 原项目新增的提交 (需要合并)

### 核心功能更新：

1. **feat(bilibili): 增强哔哩哔哩图文动态解析 (#444)** ⭐ 重要
   - 修改了 `bilibili/__init__.py` 和 `bilibili/dynamic.py`
   - 新增了 `is_article()` 检测
   - 优化了 opus 和 dynamic 的处理逻辑
   - 添加了 DynamicMajor.desc 字段支持

2. **fix(helper): enhance helper to simpify code (#448)**
   - 修改了 `helper.py`
   - 简化了辅助函数代码

3. **fix(nga): add more url for nga test (#446)**
   - 修改了 `parsers/nga.py`
   - 添加了更多测试 URL

4. **fix(nga, tiktok): 更新解析器的处理规则 (#445)**
   - 修改了 `parsers/nga.py` 和 `parsers/tiktok.py`
   - 更新了处理规则

5. **fix(test): add test url for yt-dlp download audio (#447)**
   - 添加了 yt-dlp 音频下载测试 URL

### 依赖更新：

6. **chore(deps): bump urllib3 from 2.6.2 to 2.6.3 (#450)**
   - 更新 urllib3 依赖

7. **chore(deps): update yt-dlp and types-yt-dlp 2015.1.29 (#449)**
   - 更新 yt-dlp 相关依赖

### CI/CD 更新：

8. **fix(ci): only update comment on the owner's pull requests (#451)**
   - 修改 CI 配置

## 3. Fork 项目特有的提交 (不合并)

这些是我们修复 B站解析的提交，原项目没有：

1. 75b444b refactor(bilibili): 放宽结构体定义并增加容错处理
2. 11abff0 fix(bilibili): 修复转发动态逻辑混乱和普通动态图片丢失问题
3. e828c55 fix(bilibili): 修复 DynamicInfo 缺少 desc_text 属性的错误
4. 534e6dd fix(bilibili): 修复转发评论和图片渲染问题
5. 367528f fix(bilibili): 修复转发动态无法获取原动态内容的问题
6. 8abd19d fix: 修复转发类型动态的orig类型判断
7. c02c06b fix: 修复B站转发类型动态无法显示图片的问题
8. 408d197 fix: 配置bilibili_api使用正确的headers
9. 94a6335 fix: 修复B站opus解析失败问题
10. feb764f fix: 修复 current_event 导入路径错误
11. [可能还有其他配置相关提交]

## 4. 需要对比的关键文件

### B站解析相关文件（重要）：

1. **`src/nonebot_plugin_parser/parsers/bilibili/__init__.py`**
   - 原项目新增了 `is_article()` 检测
   - 原项目合并了 opus 和 dynamic 处理逻辑
   - 原项目新增了 `parse_bilibili_api_opus` 方法
   - 我们的 fork 有转发动态的修复
   - ⚠️ **冲突可能性高**

2. **`src/nonebot_plugin_parser/parsers/bilibili/dynamic.py`**
   - 原项目新增了 `DynamicMajor.desc` 字段
   - 原项目修改了 `major_info` 返回逻辑（转发类型没有 major）
   - 我们的 fork 有 Draw 类型支持
   - 我们的 fork 有容错的 `_major` 方法
   - ⚠️ **冲突可能性高**

### 其他可能冲突的文件：

3. **`src/nonebot_plugin_parser/helper.py`**
   - 原项目简化了代码
   - 可能需要查看具体改动

4. **`src/nonebot_plugin_parser/parsers/nga.py`**
   - 原项目更新了测试 URL
   - 可能需要合并

5. **`src/nonebot_plugin_parser/parsers/tiktok.py`**
   - 原项目更新了处理规则
   - 可能需要合并

6. **`src/nonebot_plugin_parser/renders/base.py`**
   - 原项目可能有改动
   - 需要检查具体内容

7. **配置文件：**
   - `pyproject.toml` - 依赖版本
   - `uv.lock` - 锁文件
   - `.pre-commit-config.yaml` - pre-commit 配置
   - `.github/workflows/ci.yml` - CI 配置

## 5. 详细对比结果

### bilibili/__init__.py 关键差异：

**原项目新增：**
- `@handle("/opus/", r"bilibili\.com/opus/(?P<dynamic_id>\d+)")`
- `is_article()` 检测逻辑
- `parse_bilibili_api_opus()` 统一方法
- 动态和 opus 统一处理逻辑

**我们的项目：**
- 转发动态的完整修复
- 区分 `current_info` 和 `content_source`
- 完善的转发评论显示

### bilibili/dynamic.py 关键差异：

**原项目新增：**
- `DynamicMajor.type: str | None = None`
- `DynamicMajor.desc: OpusSummary | None = None`
- `major_info` 返回 `module_dynamic`（转发类型）

**我们的项目：**
- `Draw` 和 `DrawItem` 结构体
- `DynamicMajor.draw` 字段
- 容错的 `_major` 方法
- 更多字段的默认值

## 6. 合并建议

### 方案 A：完全合并（推荐）

1. **先合并原项目的更新：**
   ```bash
   git fetch upstream
   git merge upstream/master
   ```

2. **解决冲突：**
   - `bilibili/__init__.py` - 保留我们的转发动态修复，同时合并原项目的 opus 处理改进
   - `bilibili/dynamic.py` - 合并双方的功能（desc 字段 + Draw 支持 + 容错）
   - 其他文件 - 通常采用原项目的更新

3. **测试验证：**
   - 测试普通动态
   - 测试转发动态
   - 测试 opus 动态

### 方案 B：选择性合并

只合并非 bilibili 相关的更新：
- `helper.py` 的简化
- `nga.py` 的更新
- `tiktok.py` 的更新
- 依赖更新

保留我们的 bilibili 修复不动。

### 方案 C：手动合并（最安全）

1. 创建新分支：
   ```bash
   git checkout -b merge-upstream
   ```

2. 手动 cherry-pick 需要的提交：
   - 只合并不冲突的文件
   - bilibili 相关文件手动对比合并

3. 测试后合并到 master

## 7. 风险评估

⚠️ **高风险冲突：**
- `bilibili/__init__.py` - 双方都有大量修改
- `bilibili/dynamic.py` - 结构完全不同

✅ **低风险冲突：**
- 配置文件（直接采用原项目的）
- 其他 parser（nga, tiktok等）
- helper.py

## 8. 建议下一步操作

1. **备份当前代码**
   ```bash
   git branch backup-before-merge
   ```

2. **尝试合并并查看冲突**
   ```bash
   git merge upstream/master
   ```

3. **逐个文件解决冲突**
   - 优先保留我们的 B站修复
   - 合并原项目的其他改进

4. **完整测试**
   - 特别是 B站各种类型的动态

## 9. 文件变更统计

原项目变更的文件：
- M  .github/workflows/ci.yml
- M  .pre-commit-config.yaml
- M  pyproject.toml
- M  src/nonebot_plugin_parser/helper.py
- M  src/nonebot_plugin_parser/matchers/__init__.py
- M  src/nonebot_plugin_parser/parsers/bilibili/__init__.py
- M  src/nonebot_plugin_parser/parsers/bilibili/dynamic.py
- M  src/nonebot_plugin_parser/parsers/nga.py
- M  src/nonebot_plugin_parser/parsers/tiktok.py
- M  src/nonebot_plugin_parser/renders/base.py
- M  tests/others/test_urls.md
- M  tests/parsers/test_bilibili.py
- M  tests/parsers/test_ytdlp.py
- M  uv.lock

总计: 14 个文件修改
