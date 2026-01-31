# 合并建议总结

## 📊 概览

**原项目 (fllesser/nonebot-plugin-parser)**
- 最新版本: v2.3.8
- 新功能: 9个提交（包括B站解析增强、依赖更新等）

**Fork项目 (Anyuluo996/nonebot-plugin-parser)**
- 当前版本: 自定义版本
- 特有修复: 11个提交（主要是B站转发动态修复）

## 🎯 合并建议

### 方案推荐：保留所有功能，手动合并

**原因：**
1. 原项目的 B站增强功能很有价值（`is_article()` 检测、统一 opus 处理）
2. 我们的转发动态修复是实际需求的解决方案
3. 双方的改进是互补的，不是互斥的

## 📝 需要合并的具体内容

### ✅ 强烈建议合并（高价值）：

#### 1. **B站解析增强** (upstream #444)
**文件：**
- `src/nonebot_plugin_parser/parsers/bilibili/__init__.py`
- `src/nonebot_plugin_parser/parsers/bilibili/dynamic.py`

**具体改进：**
```python
# 1. 新增 is_article() 检测
if await dynamic.is_article():
    return await self._parse_bilibili_api_opus(dynamic.turn_to_opus())

# 2. DynamicMajor.desc 字段
desc: OpusSummary | None = None

# 3. 改进的 major_info 逻辑
if major := self.module_dynamic.get("major"):
    return major
# 转发类型动态没有 major
return self.module_dynamic
```

#### 2. **Helper 简化** (#448)
**文件：** `src/nonebot_plugin_parser/helper.py`
- 简化了代码逻辑

#### 3. **NGA 和 Tiktok 更新** (#445, #446)
**文件：**
- `src/nonebot_plugin_parser/parsers/nga.py`
- `src/nonebot_plugin_parser/parsers/tiktok.py`

#### 4. **依赖更新**
**文件：**
- `pyproject.toml`
- `uv.lock`
- urllib3: 2.6.2 → 2.6.3
- yt-dlp: 2025.1.29

### ⚠️ 需要冲突解决的文件：

#### 1. **bilibili/__init__.py**
**冲突点：**
- URL handle 规则不同
- 方法名不同（`parse_dynamic_or_opus` vs `parse_dynamic`）
- opus 处理逻辑不同

**建议：**
```
保留我们的逻辑：
  - current_info/content_source 分离
  - 转发动态的完整修复

合并原项目：
  - is_article() 检测
  - 统一的 parse_bilibili_api_opus() 方法名
  - 适当的 URL handle 规则
```

#### 2. **bilibili/dynamic.py**
**冲突点：**
- `DynamicMajor` 字段不同
- `major_info` 逻辑不同
- 我们有 Draw 支持，原项目没有

**建议：**
```
保留我们的：
  - Draw/DrawItem 结构体
  - _major() 容错方法
  - 更多字段的默认值

合并原项目：
  - desc 字段
  - 改进的 major_info 返回逻辑
```

### 🔄 其他文件（直接采用原项目）：

- ✅ `.github/workflows/ci.yml`
- ✅ `.pre-commit-config.yaml`
- ✅ `src/nonebot_plugin_parser/renders/base.py`
- ✅ `tests/parsers/test_bilibili.py`
- ✅ `tests/parsers/test_ytdlp.py`
- ✅ `tests/others/test_urls.md`

## 🚀 推荐的合并步骤

### 步骤 1: 备份
```bash
git branch backup-before-merge
git push origin backup-before-merge
```

### 步骤 2: 创建合并分支
```bash
git checkout -b merge-upstream
```

### 步骤 3: 尝试合并
```bash
git fetch upstream
git merge upstream/master
```

### 步骤 4: 解决冲突

**bilibili/__init__.py:**
```python
# 合并后的关键代码段应该包含：
# 1. 保留我们的转发逻辑
current_info = dynamic_data.item
content_source = current_info
is_forward = orig_info is not None
if is_forward:
    content_source = orig_info

# 2. 合入原项目的 is_article 检测
if await dynamic.is_article():
    return await self._parse_bilibili_api_opus(dynamic.turn_to_opus())
```

**bilibili/dynamic.py:**
```python
# 合并后的结构
class DynamicMajor(Struct):
    type: str | None = None  # 原项目
    desc: OpusSummary | None = None  # 原项目
    draw: Draw | None = None  # 我们的项目
    archive: VideoArchive | None = None
    opus: OpusContent | None = None

    # 保留我们的 _major 容错
    @property
    def _major(self) -> DynamicMajor | None:
        try:
            return convert(major_info, DynamicMajor)
        except Exception:
            return None
```

### 步骤 5: 测试
```bash
# 测试各类动态
- https://www.bilibili.com/opus/1159504791855955984  # 普通图文
- https://m.bilibili.com/opus/1156587796127809560   # 转发动态
- 其他 opus/dynamic 链接
```

### 步骤 6: 合并到主分支
```bash
git checkout master
git merge merge-upstream
git push origin master
```

## ⚠️ 注意事项

1. **不要使用 `git merge upstream/master` 直接在 master 上操作**
   - 创建分支进行合并和测试
   - 确认无误后再合并到 master

2. **B站解析器是核心功能**
   - 务必完整测试各种类型
   - 特别注意转发动态

3. **依赖更新**
   - 检查是否有破坏性变更
   - 特别是 urllib3 和 yt-dlp

4. **测试覆盖**
   - 测试原项目新增的功能
   - 确保我们的修复仍然有效

## 📋 检查清单

合并完成后检查：

- [ ] 普通图文动态正常
- [ ] 转发动态正常（显示转发者+原内容）
- [ ] Opus 类型动态正常
- [ ] Draw 类型动态正常
- [ ] 其他平台（NGA、TikTok）正常
- [ ] 所有测试通过
- [ ] 依赖版本兼容

## 🆘 遇到问题时的回退方案

```bash
# 如果合并失败或有严重问题
git checkout master
git reset --hard backup-before-merge
git push -f origin master
```
