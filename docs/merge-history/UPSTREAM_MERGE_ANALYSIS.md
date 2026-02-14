# 主仓库合并分析报告

**生成时间**: 2026-02-09
**主仓库版本**: v2.4.0
**当前版本**: 9.9.9.dev
**分叉点**: v2.3.7 (commit `15653d7`)

---

## 📦 已合并的非冲突更新

### ✅ 依赖更新
以下依赖已更新到主仓库最新版本：

| 依赖 | 当前版本 | 主仓库版本 | 状态 |
|------|---------|-----------|------|
| ruff | ≥0.14.14 | ≥0.15.0 | ✅ 已合并 |
| uv_build | ≥0.9.0,<0.10.0 | ≥0.10.0,<0.11.0 | ✅ 已合并 |
| yt-dlp | ≥2026.1.29 | ≥2026.2.4 | ✅ 已合并 |
| types-yt-dlp | ≥2026.1.29.20260131 | ≥2026.2.4.20260206 | ✅ 已合并 |

### ✅ 构建系统配置
- 更新 `uv_build` 依赖到 0.10.x
- 更新 ruff 到 0.15.x

### ✅ Pre-commit 配置
- 主仓库移除了自动更新配置以避免 CI 干扰
- 当前版本已经包含此更改

---

## ⚠️ 未合并的冲突更新分析

### 1. 🔴 核心冲突：进度条库替换 (tqdm → rich)

**涉及文件**:
- `src/nonebot_plugin_parser/download/__init__.py`
- `src/nonebot_plugin_parser/download/ytdlp.py`
- `src/nonebot_plugin_parser/parsers/base.py`
- `src/nonebot_plugin_parser/renders/common.py`
- `pyproject.toml` (依赖)

#### 主仓库的实现

**依赖变化**:
```diff
- "tqdm>=4.67.1,<5.0.0"
- "apilmoji[tqdm]>=0.2.4,<1.0.0"
+ "rich>=13.0.0"
+ "apilmoji[rich]>=0.3.0,<1.0.0"
```

**代码实现** (download/__init__.py):
```python
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    DownloadColumn,
)

@contextmanager
def rich_progress(self, desc: str, total: int | None = None):
    with Progress(
        TextColumn("[bold blue]{task.description}", justify="right"),
        BarColumn(bar_width=None),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(),
    ) as progress:
        task_id = progress.add_task(description=desc, total=total)
        yield partial(progress.update, task_id)
```

**优势**:
- ✅ Rich 进度条更美观，支持更多样式
- ✅ Rich 与终端完美集成，不会破坏输出
- ✅ 内置下载速度、剩余时间显示
- ✅ 统一进度条风格（如果项目其他地方也用 rich）

**劣势**:
- ❌ 代码复杂度略有增加（需要 context manager）
- ❌ 需要额外的依赖（虽然 rich 很流行）

#### 当前项目的实现

**依赖**:
```toml
"tqdm>=4.67.1,<5.0.0",
"curl_cffi>=0.13.0,<1.0.0,!=0.14.0",
```

**代码实现** (download/__init__.py):
```python
from tqdm.asyncio import tqdm

# 使用 tqdm 显示进度
with tqdm(total=total_size, unit="B", unit_scale=True, desc=file_name) as pbar:
    async for chunk in response.aiter_bytes(chunk_size):
        await file.write(chunk)
        pbar.update(len(chunk))
```

**优势**:
- ✅ 实现简单直接
- ✅ tqdm 经典可靠，社区广泛使用
- ✅ 结合 curl_cffi 支持 NGA 反爬绕过
- ✅ 代码量少，易于维护

**劣势**:
- ❌ 进度条样式较简单
- ❌ 在某些终端可能有显示问题
- ❌ Rich 是更现代的选择

#### 🎯 建议

**保持当前实现 (tqdm + curl_cffi)**，原因：

1. **NGA 特殊下载支持**: 当前版本使用 curl_cffi 绕过 NGA 的反爬，这是主仓库没有的
   ```python
   if "nga.178.com" in url and CURL_CFFI_AVAILABLE:
       logger.info(f"检测到 NGA 图片，使用 curl_cffi 下载: {url}")
       # ... 使用 curl_cffi 下载
   ```

2. **稳定优先**: tqdm 已经在当前项目中稳定运行，没有必要为了美观而冒险

3. **功能完备**: 当前实现的进度条功能已经足够

4. **避免不必要的风险**: 主仓库的 rich 重构涉及多个文件，合并可能引入隐藏问题

---

### 2. 🟡 通用渲染模块简化 (renders/common.py)

**主仓库改动**: PR #457 - 简化 common render 实现

#### 主要变更

主仓库简化了以下内容：
1. 字体宽度计算逻辑优化
2. 文本渲染缓存机制改进
3. 移除一些冗余代码

#### 当前项目的额外功能

你的版本在主仓库基础上增加了：

**异常抑制装饰器**:
```python
def suppress_exception(
    func: Callable[P, T],
) -> Callable[P, T | None]:
    """装饰器：捕获所有异常并返回 None"""
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.debug(f"函数 {func.__name__} 执行失败: {e}")
            return None
    return wrapper
```

**字符宽度计算增强**:
```python
@lru_cache(maxsize=500)
def get_char_width(self, char: str) -> int:
    """获取字符宽度，使用缓存优化"""
    bbox = self.font.getbbox(char)
    return int(bbox[2] - bbox[0])

def get_text_width(self, text: str) -> int:
    """计算文本宽度，使用预计算的字符宽度优化性能"""
    if not text:
        return 0
    return sum(self.get_char_width_fast(char) for char in text)
```

#### 🎯 优劣对比

| 维度 | 主仓库版本 | 当前版本 |
|------|-----------|---------|
| 代码简洁性 | ✅ 更简洁 | ❌ 稍复杂 |
| 性能优化 | ✅ 有优化 | ✅ 有更多优化 |
| 错误处理 | ✅ 基础处理 | ✅ 异常抑制装饰器 |
| 功能完整度 | ⚠️ 基础功能 | ✅ 增强功能 |

#### 🎯 建议

**保持当前版本**，原因：

1. **性能优化更多**: 你的版本有更多的 lru_cache 优化
2. **异常处理更完善**: 异常抑制装饰器提供了更好的容错性
3. **已经过验证**: 当前版本已经在生产环境中稳定运行
4. **合并风险高**: 渲染模块是核心功能，合并可能影响所有平台的卡片渲染

---

### 3. 🟡 下载模块重构与增强

**主仓库改动**: PR #461, #462

#### 主仓库新增功能

1. **并发下载保护**:
```python
# 防止同一个 URL 被并发下载
_download_locks: dict[str, asyncio.Lock] = {}

async def download_file(self, url: str, ...):
    lock = _download_locks.setdefault(url, asyncio.Lock())
    async with lock:
        # ... 下载逻辑
```

2. **总大小显示**:
```python
content_length = response.headers.get("Content-Length")
content_length = int(content_length) if content_length else 0

# 进度条显示总大小
with self.rich_progress(file_name, content_length) as update_progress:
    # ...
```

3. **可配置的 chunk size**:
```python
async def download_file(
    self,
    url: str,
    *,
    chunk_size: int = 64 * 1024,  # 可配置
    ...
):
```

4. **确保缓存目录存在**:
```python
def __init__(self):
    self.cache_dir: Path = pconfig.cache_dir
    self.cache_dir.mkdir(parents=True, exist_ok=True)  # 新增
```

#### 当前项目的额外功能

1. **NGA curl_cffi 支持**:
```python
if "nga.178.com" in url and CURL_CFFI_AVAILABLE:
    # 使用 curl_cffi 绕过反爬
```

2. **零字节文件检测**:
```python
if file_path.exists():
    if file_path.stat().st_size > 0:
        return file_path
    else:
        await safe_unlink(file_path)  # 删除空文件
```

3. **详细异常类型**:
```python
class SizeLimitException(DownloadLimitException):
    """下载大小超过限制异常"""

class DurationLimitException(DownloadLimitException):
    """下载时长超过限制异常"""

class ZeroSizeException(DownloadException):
    """下载大小为 0 异常"""
```

#### 🎯 优劣对比

| 功能 | 主仓库 | 当前项目 |
|------|-------|---------|
| 并发下载保护 | ✅ 有 | ❌ 无 |
| 总大小显示 | ✅ 有 | ⚠️ 有（但格式不同） |
| 可配置 chunk size | ✅ 有 | ⚠️ 固定 |
| 缓存目录检查 | ✅ 有 | ✅ 有 |
| NGA 反爬绕过 | ❌ 无 | ✅ 有 |
| 零字节文件处理 | ⚠️ 隐式 | ✅ 显式 |
| 详细异常类型 | ❌ 无 | ✅ 有 |

#### 🎯 建议

**选择性合并**，建议：

1. ✅ **合并**: 并发下载保护（降低重复请求）
2. ✅ **合并**: 缓存目录显式创建（更健壮）
3. ✅ **合并**: 可配置 chunk size（灵活性）
4. ❌ **不合并**: Rich 进度条（保持 tqdm）
5. ✅ **保留**: NGA curl_cffi 支持
6. ✅ **保留**: 详细异常类型

**实施建议**:
```python
# 在当前基础上添加
_download_locks: dict[str, asyncio.Lock] = {}

async def download_file(self, url: str, *, chunk_size: int = 64 * 1024, ...):
    # 添加并发保护
    lock = _download_locks.setdefault(url, asyncio.Lock())
    async with lock:
        # 保持现有的 tqdm + curl_cffi 逻辑
        # ...
```

---

### 4. 🟢 高度估算增强 (PR #463)

**主仓库改动**: 优化图片高度预估逻辑

#### 变更内容

主仓库对 `renders/common.py` 中的高度预估逻辑进行了优化，使得：
- 卡片渲染更准确
- 减少不必要的重绘
- 性能提升

#### 🎯 建议

**谨慎合并**：
- 高度预估逻辑比较微妙，合并可能影响所有平台的卡片渲染
- 建议先在测试环境验证，确认不影响现有功能后再合并
- 或者保持当前版本，因为现有版本可能已经适配了你的特殊需求

---

### 5. 🟢 其他小改动

#### 文件重命名
- `HYSongYunLangHeiW-1.ttf` → `HYSongYunLangHeiW.ttf`
- **建议**: 合并（只是文件名规范化）

#### README.md
- 移除 Star History 图表
- **建议**: 不合并（你喜欢保留图表）

#### 插件描述更新
- 主仓库更新了插件描述文本
- **建议**: 可选合并（描述性内容，影响不大）

#### .env.test
- 测试环境配置更新
- **建议**: 可选合并（不影响生产）

---

## 🎯 最终合并建议

### ✅ 立即合并（已完成）
- [x] 依赖版本更新
- [x] uv_build 版本更新
- [x] pre-commit 配置

### 🤔 可选合并
- [ ] 文件重命名（字体文件）
- [ ] .env.test 配置
- [ ] 插件描述更新

### ❌ 不建议合并
1. **Rich 进度条替换 tqdm**
   - 原因：当前版本稳定，且有 NGA curl_cffi 特殊支持

2. **通用渲染模块简化**
   - 原因：当前版本有更多优化和异常处理

### 🛠️ 需要手动合并（中等优先级）
1. **下载模块并发保护**
   - 添加 `_download_locks` 字典
   - 在下载前获取锁
   - 保持现有的 tqdm 和 curl_cffi 逻辑

2. **缓存目录显式创建**
   - 在 `__init__` 中添加 `self.cache_dir.mkdir(parents=True, exist_ok=True)`

3. **可配置 chunk size**
   - 为 `download_file` 等方法添加 `chunk_size` 参数

---

## 📋 手动合并实施指南

### 1. 添加并发下载保护

在 `src/nonebot_plugin_parser/download/__init__.py` 的 `StreamDownloader` 类中添加：

```python
class StreamDownloader:
    # 类级别的锁字典
    _download_locks: dict[str, asyncio.Lock] = {}

    async def download_file(self, url: str, ...):
        # 添加并发保护
        lock = self._download_locks.setdefault(url, asyncio.Lock())
        async with lock:
            # 现有的下载逻辑保持不变
            # ...
```

### 2. 确保缓存目录存在

```python
def __init__(self):
    self.headers: dict[str, str] = COMMON_HEADER.copy()
    self.cache_dir: Path = pconfig.cache_dir
    # 添加这行
    self.cache_dir.mkdir(parents=True, exist_ok=True)
    self.client: AsyncClient = AsyncClient(timeout=DOWNLOAD_TIMEOUT, verify=False)
```

### 3. 添加可配置 chunk size

```python
async def download_file(
    self,
    url: str,
    *,
    file_name: str | None = None,
    ext_headers: dict[str, str] | None = None,
    chunk_size: int = 64 * 1024,  # 添加这个参数
) -> Path:
    # 在使用 chunk_size 的地方传递参数
    # ...
```

---

## 📊 总结

### 保持当前版本的核心原因

1. **功能更完整**:
   - NGA curl_cffi 反爬绕过
   - 推特 GIF 转换
   - 强制解析功能
   - 细粒度平台控制
   - B站 Chrome 截图渲染

2. **稳定性优先**:
   - 当前版本已经在生产环境验证
   - 避免引入未知风险

3. **性能优化不差**:
   - 虽然没有 Rich，但 tqdm 足够用
   - 渲染模块有更多 lru_cache 优化

4. **维护成本**:
   - 合并冲突解决复杂
   - 需要大量测试

### 建议的后续策略

1. **短期**:
   - 保持当前版本稳定
   - 只合并依赖更新和安全补丁

2. **中期**:
   - 观察主仓库 v2.4.x 的稳定性
   - 收集用户反馈

3. **长期**:
   - 考虑将你的自定义功能（如 GIF 转换、强制解析）PR 到主仓库
   - 或者维护一个独立的 fork

---

## 🔄 版本对照表

| 组件 | 主仓库 v2.4.0 | 当前项目 | 说明 |
|------|-------------|---------|------|
| Python | ≥3.10 | ≥3.10 | ✅ 一致 |
| nonebot2 | ≥2.4.3 | ≥2.4.3 | ✅ 一致 |
| rich | ≥13.0.0 | ❌ 未使用 | 当前用 tqdm |
| tqdm | ❌ 已移除 | ≥4.67.1 | 当前使用 |
| curl_cffi | ≥0.13.0 | ≥0.13.0 | ✅ 一致 |
| pillow | ≥11.0.0 | ≥11.0.0 | ✅ 一致 |
| yt-dlp | ≥2026.2.4 | ≥2026.2.4 | ✅ 已更新 |
| bilibili-api | ≥17.4.1 | ≥17.4.1 | ✅ 一致 |
| ruff | ≥0.15.0 | ≥0.15.0 | ✅ 已更新 |

---

生成时间: 2026-02-09
报告版本: 1.0
