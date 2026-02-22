# Twitter 混合 API 测试指南

## 功能概述

本次更新实现了**混合 Twitter API 方案**，结合了 vx Twitter API 和 xdown.app API 的优势。

## 新增功能

### 1. 完整用户信息
- ✅ **用户头像**: 显示用户的真实头像
- ✅ **用户昵称**: 显示用户的显示名称
- ✅ **用户ID**: 显示 @username 格式的 ID
- ✅ **时间戳**: 显示推文发布时间
- ✅ **点赞数**: 显示推文的点赞数量

### 2. 转发支持
- ✅ **递归处理**: 支持多层转发
- ✅ **完整信息**: 转发的推文也包含完整信息

### 3. 智能降级
- ✅ **优先 vx API**: 获取最完整的信息
- ✅ **自动降级**: vx API 失败时使用 xdown.app
- ✅ **保留功能**: 所有现有功能完全保留

## 测试步骤

### 测试 1: 基本 Twitter 链接解析

```
# 发送以下链接测试
https://x.com/Twitter/status/20
```

**预期结果**:
- 显示用户头像 (@Twitter)
- 显示用户昵称 (Twitter)
- 显示推文内容
- 显示时间戳
- 如果有媒体，正确显示视频/图片/GIF

### 测试 2: 视频 Tweet

```
# 找一个包含视频的 Tweet 链接
https://x.com/[用户名]/status/[推文ID]
```

**预期结果**:
- 视频正确下载和显示
- 显示完整用户信息
- 如果是 GIF，自动转换为 GIF 格式

### 测试 3: GIF Tweet

```
# 找一个包含 GIF 的 Tweet 链接
# 通常 URL 中包含 tweet_video 的就是 GIF
```

**预期结果**:
- 自动检测为 GIF
- 下载并转换为 GIF 格式
- 日志显示 "检测到 GIF 内容，将转换为 GIF"

### 测试 4: 多图 Tweet

```
# 找一个包含多张图片的 Tweet
```

**预期结果**:
- 所有图片都正确显示
- 保持图片顺序

### 测试 5: 转发 Tweet

```
# 找一个转发其他推文的 Tweet
```

**预期结果**:
- 显示原推文信息
- 显示被转发的推文信息
- 两层都有完整的用户信息

### 测试 6: 降级机制

如果 vx Twitter API 在你的环境中不可用：

**预期行为**:
- 日志显示 "vx Twitter API 解析失败，降级到 xdown.app API"
- 仍然能够解析推文（使用 xdown.app）
- 可能缺少部分用户信息（头像、昵称等）

## 日志监控

### 开启调试日志

```python
# 在 NoneBot2 配置中设置
logger.level("DEBUG")
```

### 关键日志信息

**成功使用 vx API**:
```
[DEBUG] 尝试使用 vx Twitter API 解析: https://x.com/...
[DEBUG] 检测到 GIF 内容，将转换为 GIF: ...
```

**降级到 xdown.app**:
```
[WARNING] vx Twitter API 解析失败，降级到 xdown.app API: ...
```

**GIF 检测**:
```
[INFO] 检测到 tweet_video_thumb 缩略图，判断为 GIF
[INFO] 检测到 '下载 gif' 链接，判断为 GIF
```

## 代理配置

### 自动代理支持

httpx 会自动使用以下环境变量：

```bash
# HTTP 代理
export HTTP_PROXY="http://proxy.example.com:8080"
export HTTPS_PROXY="http://proxy.example.com:8080"

# SOCKS 代理
export ALL_PROXY="socks5://127.0.0.1:1080"

# 不使用代理的地址
export NO_PROXY="localhost,127.0.0.1,.local"
```

### 验证代理设置

```python
import os
print(os.environ.get('HTTP_PROXY'))
print(os.environ.get('HTTPS_PROXY'))
```

## 故障排查

### 问题 1: vx API 超时

**症状**: 日志显示 "vx Twitter API 解析失败"

**解决方案**:
- 检查网络连接
- 配置代理（见上）
- 系统会自动降级到 xdown.app

### 问题 2: GIF 未转换

**症状**: GIF 显示为视频

**解决方案**:
- 检查日志中是否有 GIF 检测信息
- 确认 URL 包含 `tweet_video`
- 系统会自动检测并转换

### 问题 3: 用户信息不显示

**症状**: 没有头像或昵称

**可能原因**:
- vx API 不可用，降级到 xdown.app
- xdown.app 不提供用户信息

**验证**:
- 查看日志确认使用哪个 API
- 如果是降级，这是正常行为

## 性能对比

| API | 用户信息 | GIF 转换 | 国内可用 | 响应时间 |
|-----|---------|---------|---------|---------|
| vx Twitter API | ✅ 完整 | ✅ 支持 | ⚠️ 需代理 | ~1-2s |
| xdown.app API | ❌ 有限 | ✅ 支持 | ✅ 直接 | ~2-3s |
| **混合方案** | ✅ 完整 | ✅ 支持 | ✅ 自动 | ~1-3s |

## 技术细节

### API 选择逻辑

```python
try:
    # 优先: vx Twitter API（完整信息）
    result = await parse_by_vxapi(url)
except Exception:
    # 降级: xdown.app API（保留功能）
    result = await parse_by_xdown(url)
```

### GIF 检测逻辑

```python
# 检测 1: URL 包含 tweet_video
if "tweet_video" in media.url:
    is_gif = True

# 检测 2: 媒体类型为 gif
if media.type == "gif":
    is_gif = True

# 如果是 GIF，启用转换
if is_gif:
    contents.extend(create_dynamic_contents([url], convert_to_gif=True))
```

### 数据结构

```python
class VxTwitterResponse:
    user_name: str              # 昵称
    user_screen_name: str       # @username
    user_profile_image_url: str # 头像
    date_epoch: int             # 时间戳
    likes: int                  # 点赞数
    text: str                   # 推文内容
    media_extended: list        # 媒体信息
    qrt: VxTwitterResponse      # 转发信息
```

## 总结

✅ **新功能**: 完整用户信息、转发支持、时间戳
✅ **保留功能**: GIF 转换、代理支持、国内可用
✅ **自动降级**: vx API 失败时使用 xdown.app
✅ **智能检测**: 自动识别 GIF 类型并转换

享受增强的 Twitter 解析体验！ 🎉
