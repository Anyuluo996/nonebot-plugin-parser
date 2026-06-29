# 抖音图文/实况照片解析失败问题报告

**报告日期**: 2026-06-29
**影响范围**: 抖音 `note` 类型图文(含实况照片的图文)解析
**严重程度**: 中 — 用户可见错误提示,功能完全不可用,但仅限特定链接类型
**报告人**: Mavis (运维排查)
**状态**: 🔍 已定位根因,待修复

---

## TL;DR

抖音 PC web 详情接口 `https://www.douyin.com/aweme/v1/web/aweme/detail/` 在没有有效鉴权(Cookie / 风控指纹)的情况下,直接返回 **HTTP 200 + 空 body**。`msgspec.json.Decoder` 解析空字符串时抛 `Input data was truncated`,由于 `note` 类型的 fallback 异常类型定义过窄(`except ParseException`),错误未被捕获,直接 traceback 抛到 matcher,用户看到解析失败。

视频 ID `7656755054552173864` 实际能通过 `parse_video` 兜底拿到,但当前 fallback 路径未生效。

---

## 现象

### 用户侧
用户发送抖音图文/实况照片分享链接后,机器人无响应或返回解析失败。

### 服务端日志

wo4 (10.126.126.2) 上的 `nb2` 容器最近报错(2026-06-29 20:39–20:43):

```text
20:37:33 [INFO] nonebot_plugin_parser | URL 重定向: https://v.douyin.com/2iB6WjcBPmw
            -> https://www.iesdouyin.com/share/note/7656755054552173864/?...
20:37:33 [INFO] nonebot_plugin_parser | 重定向 URL 匹配到: iesdouyin
20:39:38 [ERROR] nonebot | Running Matcher(...) failed.
Traceback (most recent call last):
  ...
  File "/usr/local/lib/python3.13/site-packages/nonebot_plugin_parser/parsers/douyin/__init__.py", line 119, in parse_slides
    aweme_detail = slides.detail_decoder.decode(response.content).aweme_detail
msgspec.DecodeError: Input data was truncated
```

同一视频 ID `7656755054552173864`(鸣潮图文)出现 2 次(强制解析 `par` 前缀 + 普通分享),报错一致。

---

## 根因分析

### 真因(必须修):抖音 PC 详情接口返回空 body

外部直连验证同一接口:

```bash
$ curl -sI 'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=7656755054552173864&aid=6383' \
    -H 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ... Chrome/120 ...'
HTTP/2 200
content-type: application/json
content-length: 0

$ curl -sL '...' -w 'HTTP_CODE: %{http_code}\nSIZE: %{size_download}\n'
HTTP_CODE: 200
SIZE: 0
```

抖音服务端返回 200 但 body 长度为 0。这是典型的风控行为 —— 服务端识别为异常/未授权客户端,吐空响应而不返回 4xx 错误。

`parse_slides` 当前的请求头(见 `parsers/douyin/__init__.py:114-117`):

```python
headers = {**self.headers, "Referer": "https://www.douyin.com/"}
response = await self.request(detail_url, headers=headers, params=params)
```

其中 `self.headers` = `COMMON_HEADER`(来自 `constants.py`):

```python
COMMON_HEADER: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/55.0.2883.87 UBrowser/6.2.4098.3 Safari/537.36"
    )
}
```

问题点:

| 项目 | 现状 | 问题 |
|------|------|------|
| User-Agent | Chrome 55 / UBrowser 6.2 (2016 年) | 异常客户端,直接被风控识别 |
| Cookie | 无 | 无 `ttwid` / `webid` / `msToken`,无法建立会话 |
| X-Requested-With | 无 | AJAX 头缺失 |
| Referer | ✅ 有 | OK |

### 隐藏 bug(必须修):note 类型 fallback 异常类型过窄

`parsers/douyin/__init__.py:36-42`:

```python
if ty == "note":
    try:
        return await self.parse_slides(vid)
    except ParseException as e:
        logger.warning(f"parse_slides failed for note {vid}, fallback to parse_video: {e}")
```

实际抛的是 `msgspec.DecodeError`,继承自 `Exception`,**不是** `ParseException`。因此 fallback 根本没生效,直接 traceback 到 matcher。

`video` 和 `slides` 类型不受影响,只有 `note` 受影响(因为只有 `note` 有 fallback 设计)。

---

## 影响范围

| 链接类型 | 路由 | 影响 |
|---------|------|------|
| `v.douyin.com/xxx` 重定向到 `.../video/...` | `parse_video` | ✅ 正常 |
| `v.douyin.com/xxx` 重定向到 `.../slides/...` | `parse_slides` 直接 | ⚠️ 视 note 之外的 slide 结构稳定性 |
| `v.douyin.com/xxx` 重定向到 `.../note/...` | `parse_slides` → 期望 fallback `parse_video` | ❌ traceback |
| `douyin.com/note/...` 直接 | 同上 | ❌ traceback |
| `douyin.com/video/...` | `parse_video` | ✅ 正常 |

---

## 解决方案

按优先级排列,**A + B 必须同时修复**,C 为增强,D 为临时绕路。

### A. 修复 PC 详情接口鉴权(真因)

**目标**:让 `parse_slides` 的请求通过抖音风控,正常返回 JSON。

方案:

1. **升级 User-Agent**: `constants.py` 的 `COMMON_HEADER` 替换为新版 Chrome(避免 2016 年的 `Chrome/55 UBrowser` 直接被识别为异常)。
   - 推荐: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36`

2. **加 `X-Requested-With: XMLHttpRequest`** 到 `parse_slides` 的 headers。

3. **注入有效 `ttwid` Cookie**:
   - 短期方案:在 `parsers/douyin/__init__.py` 顶部常量或 `.env` 配置里塞一个可用的 `ttwid` 字符串。
   - 中期方案:从 `iesdouyin` / `m.douyin` 分享页面的 HTML 中提取 `ttwid` / `webid`,在 `_parse_short_link` 阶段获取并缓存,后续 PC 详情请求复用。
   - 长期方案:接 `parse_video` 走 m站/iesdouyin 备用,只有需要实况照片时才走 PC 详情。

4. **可选:加 `msToken`**: 通过访问 `https://www.douyin.com/` 首页从响应 HTML 提取。

### B. 修复 fallback 异常类型(隐藏 bug)

**目标**:即使 PC 详情接口偶发异常,也能 fallback 到 `parse_video` 拿到基础信息(对纯图文够用,实况照片会丢失,但比 traceback 强)。

修改 `parsers/douyin/__init__.py:36-42`:

```python
# 改前
if ty == "note":
    try:
        return await self.parse_slides(vid)
    except ParseException as e:
        logger.warning(...)

# 改后
if ty == "note":
    try:
        return await self.parse_slides(vid)
    except (ParseException, msgspec.DecodeError) as e:
        logger.warning(f"parse_slides failed for note {vid}, fallback to parse_video: {e}")
```

或在 decode 前主动校验:

```python
# 在 parse_slides 内, decode 之前
if not response.content:
    raise ParseException(f"detail API returned empty body for {video_id}")
aweme_detail = slides.detail_decoder.decode(response.content).aweme_detail
```

让 B 独立触发 fallback,即使 A 暂时未生效,note 类型也不会再 traceback。

### C. 增强防御(建议)

1. **响应长度日志**:`parse_slides` decode 失败时,把 `len(response.content)` 和前 200 字节摘要打到 warning 日志,便于快速区分"空响应"vs"字段变更"。
2. **退避重试**:对 PC 详情接口失败时,换 `aid` (`6383` ↔ `1128`) 重试一次,部分场景下能绕过风控。
3. **缓存正常响应**:成功解析过的 `aweme_id` 可短时间缓存 JSON,降低对接口的依赖。

### D. 临时绕路(不修代码,运维侧)

让用户分享图文链接时**手动加 `par` 前缀**(代码里有强制解析前缀机制,见 `FEATURE_FORCE_PARSE.md`),会被路由到 `parse_video`,绕过 PC 详情接口。但这只是临时绕开症状,不能解决普通分享场景。

---

## 验证清单

修复后应通过以下检查:

### 单元验证

- [ ] `uv run python -m compileall src`
- [ ] `uv run ruff check src/nonebot_plugin_parser/parsers/douyin/`
- [ ] `uv run basedpyright src/nonebot_plugin_parser/parsers/douyin/`

### 回归测试(新增)

建议在 `tests/parsers/test_douyin.py` 新增以下用例:

- [ ] **空 body 防御**:mock `parse_slides` 的 HTTP 响应为空,断言降级到 `parse_video`,无 traceback
- [ ] **decode 失败兜底**:`note` 类型 URL 在 PC 详情失败时,`parse_video` 路径能拿到 desc / author / cover
- [ ] **实况照片场景**:正常 case 下,note 中的实况照片 mp4 URL 能从 `dynamic_urls` 提取出来
- [ ] **headers 生效**:断言 detail 请求带上 `X-Requested-With` 和新版 UA

### 端到端验证

- [ ] 重启 `nb2` 容器,实际分享 `https://v.douyin.com/2iB6WjcBPmw`(鸣潮图文),确认机器人正常返回结果
- [ ] 实际分享另一个 `note` 类型链接(纯图文),确认正常
- [ ] 实际分享 `video` 类型链接,确认不受影响
- [ ] 在 `.env.test.example` 中加 `PARSER_DOUYIN_TTWID` 占位符(若有此配置)

---

## 相关文件

| 路径 | 说明 |
|------|------|
| `src/nonebot_plugin_parser/parsers/douyin/__init__.py` | 主解析器,bug 集中在第 36-42 / 110-119 行 |
| `src/nonebot_plugin_parser/parsers/douyin/slides.py` | `detail_decoder` 定义,第 96 行 |
| `src/nonebot_plugin_parser/parsers/base.py` | `request()` 通用请求封装,第 127-194 行 |
| `src/nonebot_plugin_parser/constants.py` | `COMMON_HEADER` / `IOS_HEADER` UA 定义 |
| `docs/features/FEATURE_FORCE_PARSE.md` | 强制解析前缀机制(临时绕路 D 用到) |
| `tests/parsers/test_douyin.py`(待新增) | 回归测试 |

---

## 下一步建议

1. **立即**:合方案 B(改 1 行异常类型 + 加 1 行空 body 校验),提交一个低风险 fix PR
2. **本周**:合方案 A 的 UA 升级 + `X-Requested-With`,无侵入
3. **本月**:评估方案 A-3 (ttwid 注入机制),作为独立特性
4. **长期**:把 `parse_slides` 改造成"PC detail 失败时自动 fallback 到 iesdouyin `_ROUTER_DATA`",彻底解决 note 类型单点依赖

---

**报告结束**