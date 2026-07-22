# 网易云扫码登录 8821 风控与登录方案调研

**报告日期**: 2026-07-23
**影响范围**: 网易云音乐 VIP 歌曲解析(需登录态)
**严重程度**: 高 — 直连扫码登录完全不可用
**状态**: ✅ 已确定方案(双模式:自建 API 扫码 + cookie 导入)

---

## TL;DR

网易云扫码登录直连 `music.163.com` 的公开接口(`/api/login/qrcode/*`)被服务端 **8821 风控**彻底封禁。用户扫码后(802→803 之间)服务端拒绝下发 cookie,返回 `code=8821, message="请切换其他登录方式或升级新版本再试"`。

8821 风控锚定的是**设备指纹 + 扫码会话**(易盾 `X-antiCheatToken` + deviceId/chainId),**不是请求的加密方式**。换 IP/UA/代理/自实现 weapi 加密均无法绕过。

当前采用双模式方案:`par网易云登录`(扫码,走自建 NeteaseCloudMusicApi 服务)+ `par网易云登录 <cookie>`(手动导入兜底)。

---

## 现象

### 轮询序列(本地诊断脚本实测)

```
801(等待扫码) ×12  →  802(已扫码待确认) ×1  →  8821 持续...
```

8821 的响应体:
```json
{
  "code": 8821,
  "message": "请切换其他登录方式或升级新版本再试",
  "redirectUrl": "https://qa-yyy.igame.163.com/anquanhuanjingfengxian"
}
```

- `anquanhuanjingfengxian` = "安全环境风险"(网易安全团队反爬系统)
- 接口本身没坏:unikey 正常返回、二维码有效(801→802 正常流转)
- 拦截点卡在"确认登录"环节

### 已验证无效的手段

| 尝试 | 结果 |
|---|---|
| 换出口 IP | 无效(本地家庭宽带也触发) |
| 完整浏览器 UA/Accept/Origin 头 | 无效 |
| 走代理 | 无效 |
| `type=1` 参数修复 | unikey 能拿到了,但仍卡 8821 |

---

## 根因分析

### 8821 的风控机制

网易云有两套独立的风控子系统:

| 风控类型 | 作用域 | 对抗手段 |
|---|---|---|
| **内容接口风控** | song/url、playlist 等 | weapi/eapi 加密(参数加密防爬) |
| **登录链路风控** | 扫码/密码登录 | **易盾 token + 设备指纹**(8821 属于此类) |

8821 属于**登录链路风控**,触发维度:
1. **易盾 `X-antiCheatToken`**:来自 `ac.dun.163yun.com/v3/b`,扫码确认时必须携带
2. **设备指纹**:`deviceId` / `chainId` / `os` / `appver` / `channel` / `buildver`
3. **扫码会话连续性**:网易云 App 端在扫码时检测调用方环境(无原生 SDK 埋点),上报风控

关键点:8821 卡在 802→803,**是网易云 App 端上报的风控**——用户在 App 里扫码后,App 检测到第三方调用方环境异常,上报后服务端拒绝下发 cookie。调用方(我们的 Bot)请求 unikey/poll 接口时用明文还是 weapi,不影响这个判断。

### 为什么自实现 weapi 没用

- 算法本身极小(~30 行 Python,AES-CBC + RSA,依赖 pycryptodome)
- 但 8821 风控**不在加密层**——换 weapi 只是把请求体加密了,设备指纹/易盾 token 仍然缺失
- **全网没有任何公开项目用 weapi 走扫码登录**(ncm-api-enhanced fork 源码确认:QR 登录全部用明文 `/api/`,`crypto=''`)
- 193 stars 的 Python SDK(2061360308/NeteaseCloudMusic_PythonSDK)选择用 QuickJS 跑 webpack 打包的 JS 而非原生重写,侧面说明风控对抗的复杂度

### ncm-api-enhanced 的对抗手段(参考)

活跃维护的 NeteaseCloudMusicApi fork 用以下手段对抗 8821,**且这些是套在明文 `/api/` 之上的,不是靠换 weapi**:

1. `generateDeviceId()` — 伪造设备指纹
2. `generateChainId()` — 会话链路 ID
3. `generateRandomChineseIP()` — 从中国 IP CIDR 池生成
4. 接入易盾 `ac.dun.163yun.com/v3/b` 拿 `X-antiCheatToken`

即便全套做了,8821 仍可能在扫码确认阶段触发(因为是 App 端上报)。

---

## 最终方案

### 双模式登录(已实现)

```
par网易云登录              → 扫码登录(需配置 PARSER_NCM_API 自建服务地址)
par网易云登录 <整条 Cookie> → 手动 cookie 导入(可靠兜底,不需任何配置)
```

**配置**(`.env`):
```bash
PARSER_NCM_API=http://10.126.126.2:4500
```

### 方案选型理由

| 方案 | 可行性 | 依赖 | 维护成本 |
|---|---|---|---|
| ❌ 直连 music.163.com 扫码 | 被 8821 封死 | 无 | — |
| ❌ 自实现 weapi 扫码 | 风控不在加密层,预期无效 | pycryptodome | 高(算法+设备指纹+易盾) |
| ✅ 走自建 NeteaseCloudMusicApi 扫码 | 服务端做完整风控对抗 | 自建 API 服务在线 | 低(调三个 HTTP 接口) |
| ✅ 手动 cookie 导入 | 完全绕开登录链路风控 | 无 | 零 |

### 扫码模式(走自建 API)

自建 NeteaseCloudMusicApi(Node.js)服务在服务端做 weapi 加密 + 设备指纹 + 易盾对抗。插件只调三个 HTTP 接口:

| 步骤 | 接口 | 响应 |
|---|---|---|
| 1. 获取 key | `GET /login/qr/key` | `{"data":{"unikey":"..."},"code":200}` |
| 2. 生成二维码 | `GET /login/qr/create?key=X&qrimg=true` | `{"data":{"qrimg":"data:image/png;base64,..."}}` |
| 3. 轮询状态 | `GET /login/qr/check?key=X` | `{"code":801/802/803,"cookie":"..."}` |

- `/qr/create` 直接返回 base64 PNG,不需要 `qrcode` 库
- 803 时 cookie 在响应体 `cookie` 字段,校验含 `MUSIC_U` 后保存

### cookie 导入模式(兜底)

与抖音 `dycookie` 同模式。浏览器登录 music.163.com → F12 → Network → Cookie 整行复制,必须含 `MUSIC_U`。

---

## 代码位置

| 文件 | 作用 |
|---|---|
| `matchers/__init__.py` `_netease_login` | 双模式分流(args 为空→扫码,有值→cookie) |
| `matchers/__init__.py` `_netease_qr_login` | 走自建 API 的扫码登录实现 |
| `matchers/__init__.py` `_netease_logout` | 清除登录态 |
| `parsers/netease/credential.py` | cookie 持久化(save/load/clear) |
| `parsers/netease/api.py` `get_song_url` | 带 cookie 走 `enhance/player/url` 解析 VIP |
| `config.py` `parser_ncm_api` | 自建 API 服务地址配置 |

---

## 关键证据来源

- **8821 响应**:本地诊断脚本实测(`netease_login_debug.py`,已删除)
- **ncm-api-enhanced QR 登录源码**:jsdelivr CDN 上的 `module/login_qr_key.js`、`module/login_qr_check.js`、`util/request.js`、`util/option.js`(QR 全用明文 `/api/`,`crypto=''`)
- **8821 对抗手段**:`util/index.js` 的 `generateDeviceId`/`generateChainId`/`generateRandomChineseIP` + `module/register_checktoken_v3.js`(易盾 token)
- **Python weapi 现状**:pyncm(PyPI 下架)、2061360308/NeteaseCloudMusic_PythonSDK(用 QuickJS 跑 JS)
- **Binaryify/NeteaseCloudMusicApi**:原仓库已清空只剩 README,标注"不再维护"
