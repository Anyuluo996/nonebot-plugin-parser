# 下载断点续传（缓做，方案存档）

> 状态：**暂不实施**（2026-10-09 评估结论）。方案与风险存档于此，后续要做时按本文件执行。
> 来源 fork：`ankoconmf/nonebot-plugin-parser-suan`（commit `905be17`，仍在活跃维护）。

## 背景

三个 fork 独立命中并修复了 rich Progress 并发递归问题（上游 884f805、cesium、merlin），
顺带暴露出我们下载链路的可靠性短板。suan 的 905be17 是其中最完整的一套下载可靠性重构。

## suan 方案内容（905be17）

- **断点续传**：写入 `.part` 文件，重试带 `Range: bytes=N-`；逐项校验（响应必须 206、
  `Content-Range` 起点必须等于本地字节数、416 = 断点无效弃断点重下、
  **解码失败也弃断点**——字节流已与远端 Range 不对应）；
- **状态码分级**：408/425/429/5xx = 可换线路重试；403/404 默认不可重试，
  B 站单独放宽（其 CDN 节点故障表现为 403）；
- **双后端逐 url 尝试**：httpx 失败换 curl_cffi 再试同一 url（我们目前按域名静态分流，一次定型）；
- **原子重命名**：下载完整才 `.part → final`，半成品永不被当缓存命中
  （可顺带消灭 10371f0 的"0 字节缓存"问题）；
- **同文件并发合并**：`_active_downloads` 字典，同 url 并发下载共享 Task；
- **`Accept-Encoding: identity`**：保证 Content-Length / Content-Range 与实际字节一致；
- **ffmpeg 半成品清理**：合并失败删输出，防坏缓存；
- B 站侧：`order_download_urls` 全线路候选化（干净 CDN 优先、PCDN 垫底）传给下载器轮换。

## 旁证：cesium 的失败路线不要跟

cesium 做的是**并行分片**方案（`bytes=0-0` 探测总长 → 切片 → gather 并发下载），
后在 8a4b2db 连同 curl 实现整体删除（313 行，未留原因）。suan 的**串行续传**方案
至今仍在其 master 上存活。保守设计（串行、只续传不并行、校验从严）是它能活下来的原因。

## 与我们现状的差距

已有：backup_urls 重试轮换、567 特判、指数退避、B 站 mcdn 选线
（`bilibili/streams.py` `_select_preferred_streams`，语义等价 suan 的 order_download_urls）、
手动逐跳重定向、零字节缓存修复（10371f0）。

缺失：续传、双后端逐 url 尝试、状态码分级、原子重命名、同文件并发合并。

## 实施要点（做的时候按这个收缩范围）

1. **不做**双后端逐 url 尝试——保留现有 `_use_curl` 域名静态分流。
2. **不做**同文件并发合并——留给"并发控制（suan 0a246c0）"那次一起。
3. 只做：`.part` + Range 续传 + 原子重命名 + 可重试状态码分级 + ffmpeg 半成品清理。
   改动集中在 `download_file` / `_download_by_curl` 两个函数内部，
   **不动**现有手动重定向与分流骨架。
4. **必须保留**我们的 curl 会话配置：`chrome110` impersonate、IPv4 强制（IPRESOLVE V4）、
   分域代理（`_bypass_proxy`）、CURL_DOWNLOAD_TIMEOUT tuple（curl_cffi 不认 httpx.Timeout，
   见 memory curl_cffi-timeout-type-gotcha）。suan 的裸 AsyncSession 不能照搬。
5. 跨线路续传的正确性隐含"候选 url 内容字节一致"。B 站同清晰度多 CDN 成立；
   其他平台（抖音 backup、twitter）未确认前先 `keep_part=False`（换线路弃断点）。
6. 我们的缓存命中检查在 `download_file` 入口，改完后 0 字节特判（10371f0）可移除
   （原子重命名后半成品不会再出现在最终路径）。

## 收益场景

B 站 mcdn/PCDN 节点中途断流时从 90% 处继续而非重头再来；大文件（60s 幻灯片、
B 站长视频）网络抖动后重试成本骤降；与 video_send_timeout 的"重发自愈"配合，
上次下了一半的文件重发时只拉剩余部分。
