# NGA 解析模块详细对比

## 概述
- **我们的版本**: 基于文本解析 + 单独图片列表
- **上游版本**: v2.4.0 新增图文混排 (graphics) 解析
- **对比日期**: 2026-02-14

---

## 一、功能对比

### 1.1 内容解析方式

| 特性 | 我们的版本 | 上游新版本 | 对比 |
|------|-----------|-----------|------|
| **内容组织** | 文本 + 独立图片列表 | 图文混排 (GraphicsContent) | ✅ 上游更好 |
| **文本清理** | 复杂的 `clean_nga_text()` 函数 | 简单的逐行处理 | ⚠️ 各有优劣 |
| **图片处理** | 统一提取所有图片 | 图片与上下文文本绑定 | ✅ 上游更好 |
| **BBCode 清理** | 完整的规则列表 | 简单去除 `[.*?]` | ⚠️ 我们更完整 |

---

### 1.2 代码差异详细分析

#### 上游的核心改进 (f17b453)

```python
# 上游新版本 - 图文混排解析
text, contents = None, []
content_tag = soup.find(id="postcontent0")
if content_tag and isinstance(content_tag, Tag):
    text = content_tag.get_text("\n", strip=True)
    lines = text.split("\n")
    temp_text = ""
    for line in lines:
        if line.startswith("[img]"):
            # 图片与前面的文本绑定
            img_url = self.base_img_url + line[6:-6]
            contents.append(self.create_graphics_content(img_url, text=temp_text))
            temp_text = ""
        elif "[" in line:
            # 简单去除 BBCode
            if clean_line := re.sub(r"\[[^\]]*?\]", "", line).strip():
                temp_text += clean_line + "\n"
        else:
            temp_text += line + "\n"
    text = temp_text.strip()
```

**上游的优点**:
1. ✅ **图文混排** - 使用 `create_graphics_content()` 让图片和描述文本一起显示
2. ✅ **上下文保留** - 图片前面的文本会作为图片的描述
3. ✅ **逻辑简单** - 逐行处理，易于理解

**上游的缺点**:
1. ❌ BBCode 处理过于简单，可能残留标签
2. ❌ 只处理以 `[img]` 开头的行，其他格式的图片可能丢失

---

#### 我们的版本

```python
# 我们的版本 - 分离的文本和图片
content_tag = soup.find(id="postcontent0")
contents = []
if content_tag and isinstance(content_tag, Tag):
    text = content_tag.get_text("\n", strip=True)
    # 提取所有图片
    img_urls: list[str] = re.findall(r"\[img\](.*?)\[/img\]", text)
    img_urls = [self.base_img_url + url[1:] for url in img_urls]
    contents.extend(self.create_image_contents(img_urls))
    # 复杂的文本清理
    text = self.clean_nga_text(text)
```

**我们的优点**:
1. ✅ **完整的 BBCode 清理** - `clean_nga_text()` 处理多种情况
2. ✅ **图片提取更可靠** - 使用正则提取所有 `[img]...[/img]`
3. ✅ **下载支持** - 配合 download 模块的 curl_cffi 特殊处理

**我们的缺点**:
1. ❌ 图片和文本分离，没有上下文关联
2. ❌ 清理逻辑复杂，可能过度清理

---

## 二、`clean_nga_text()` 函数详解

我们的版本有一个非常完整的 BBCode 清理函数：

| 规则 | 说明 | 上游有对应处理吗？ |
|------|------|-------------------|
| `\[img\][^\[\]]*\[/img\]` | 移除完整图片标签 | ✅ 有，但方式不同 |
| `\[img\][^\[\]]*` | 移除不完整图片标签 | ❌ 没有 |
| `\[url=[^\]]*\]([^\[]*?)\[/url\]` | 处理 URL 标签，保留链接文本 | ❌ 没有 |
| `\[url\]([^\[]*?)\[/url\]` | 处理简单 URL 标签 | ❌ 没有 |
| `\[quote\].*?\[/quote\]` | 移除引用标签 | ❌ 没有 |
| `\[(b|i|u)\](.*?)\[/\1\]` | 处理格式化标签 (b/i/u) | ❌ 没有 |
| `\[(color|size)=[^\]]*\](.*?)\[/\1\]` | 处理颜色/大小标签 | ❌ 没有 |
| `\[[^]]+\]` | 移除其他未配对标签 | ✅ 有 (简单版) |
| `\n{3,}` | 压缩多个换行符 | ❌ 没有 |
| `[ \t]+` | 压缩多个空格 | ❌ 没有 |

---

## 三、下载模块的 NGA 特殊处理

我们的下载模块有针对 NGA 的特殊处理，这是上游没有的：

```python
# 我们的 download/__init__.py 中的 NGA 特殊处理
if "nga.178.com" in url and CURL_CFFI_AVAILABLE:
    logger.info(f"检测到 NGA 图片，使用 curl_cffi 下载: {url}")
    # 使用 curl_cffi 模拟 Chrome 浏览器，绕过反爬
    response = curl_requests.get(
        url,
        headers=headers,
        impersonate="chrome110",  # 模拟 Chrome 110
        timeout=30,
        stream=True,
    )
```

**这个功能很重要**，因为：
- NGA 有反爬虫机制
- 普通 httpx 请求可能被拦截
- curl_cffi 可以模拟浏览器 TLS 指纹

---

## 四、合并建议

### 推荐方案：混合方案 ✅

结合两者的优点：

1. **采用上游的图文混排逻辑** - 使用 `create_graphics_content()`
2. **保留我们的 BBCode 清理** - 在适当的地方使用 `clean_nga_text()`
3. **保留我们的下载特殊处理** - 这是必需的功能
4. **改进图片提取** - 结合两者的提取方式

### 具体改动点：

```python
# 建议的混合实现
if content_tag and isinstance(content_tag, Tag):
    raw_text = content_tag.get_text("\n", strip=True)
    lines = raw_text.split("\n")
    temp_text = ""

    for line in lines:
        # 更灵活的图片检测（不只是行首）
        if img_matches := re.findall(r"\[img\](.*?)\[/img\]", line):
            # 如果这行有图片，先处理图片前的文本
            before_img = line.split("[img]")[0]
            if before_img.strip():
                temp_text += before_img + "\n"

            # 为每张图片创建 graphics content
            for img_url in img_matches:
                full_url = self.base_img_url + img_url[1:]  # 去掉开头的 '.'
                # 使用清理后的临时文本作为图片描述
                clean_desc = self.clean_nga_text(temp_text, max_length=200)
                contents.append(self.create_graphics_content(full_url, text=clean_desc))
                temp_text = ""  # 清空，图片后的文本属于下一段
        else:
            # 非图片行，累积文本
            temp_text += line + "\n"

    # 最后剩余的文本也清理一下
    text = self.clean_nga_text(temp_text)
```

---

## 五、结论

| 项目 | 建议 | 原因 |
|------|------|------|
| **图文混排** | ✅ 采用上游 | 展示效果更好 |
| **BBCode 清理** | ✅ 保留我们的 | 更完整健壮 |
| **下载特殊处理** | ✅ 保留我们的 | NGA 反爬必需 |
| **日志输出** | ✅ 采用上游 | 更清晰的 debug 信息 |

**总体建议：合并上游的 NGA 图文混排功能，但保留我们的清理和下载逻辑。**
