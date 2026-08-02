<div align="center">
<a href="https://v2.nonebot.dev/store">
    <img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-template/refs/heads/resource/.docs/NoneBotPlugin.svg" width="310" alt="logo">
</a>

## ✨ [Nonebot2](https://github.com/nonebot/nonebot2) 链接分享自动解析插件 ✨

[![LICENSE](https://img.shields.io/github/license/fllesser/nonebot-plugin-parser.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-parser.svg)](https://pypi.python.org/pypi/nonebot-plugin-parser)
[![python](https://img.shields.io/badge/python-3.10|3.11|3.12|3.13|3.14-blue.svg)](https://python.org)
[![uv](https://img.shields.io/badge/package%20manager-uv-black?style=flat-square&logo=uv)](https://github.com/astral-sh/uv)
[![ruff](https://img.shields.io/badge/code%20style-ruff-black?style=flat-square&logo=ruff)](https://github.com/astral-sh/ruff)
<br/>
[![pre-commit](https://results.pre-commit.ci/badge/github/fllesser/nonebot-plugin-parser/master.svg)](https://results.pre-commit.ci/latest/github/fllesser/nonebot-plugin-parser/master)
[![codecov](https://codecov.io/gh/fllesser/nonebot-plugin-parser/graph/badge.svg?token=VCS8IHSO7U)](https://codecov.io/gh/fllesser/nonebot-plugin-parser)
[![qqgroup](https://img.shields.io/badge/QQ%E7%BE%A4-820082006-orange?style=flat-square)](https://qm.qq.com/q/y4T4CjHimc)

</div>

> [!IMPORTANT] 
> **收藏项目**，你将从 GitHub 上无延迟地接收所有发布通知～ ⭐️

<img width="100%" src="https://starify.komoridevs.icu/api/starify?owner=fllesser&repo=nonebot-plugin-parser" alt="starify" />

## 📖 介绍

| 平台    | 触发的消息形态                    | 视频 | 图集 | 音频 | 状态 |
| ------- | --------------------------------- | ---- | ---- | ---- | ---- |
| B 站    | av 号/BV 号/链接/短链/卡片/小程序 | ✅​  | ✅​  | ✅​  | ✅   |
| 抖音    | 链接(分享链接，兼容电脑端链接)    | ✅​  | ✅​  | ❌️  | ✅   |
| 微博    | 链接(博文，视频，show, 文章)      | ✅​  | ✅​  | ❌️  | ✅   |
| 小红书  | 链接(含短链)/卡片                 | ✅​  | ✅​  | ❌️  | ✅   |
| 快手    | 链接(包含标准链接和短链)          | ✅​  | ✅​  | ❌️  | ✅   |
| acfun   | 链接                              | ✅​  | ❌️  | ❌️  | ✅   |
| youtube | 链接(含短链)                      | ✅​  | ❌️  | ✅​  | ✅   |
| tiktok  | 链接                              | ✅​  | ❌️  | ❌️  | ✅   |
| twitter | 链接                              | ✅​  | ✅​  | ❌️  | ✅   |
| Pixiv   | 链接(含 artworks)                 | ❌️  | ✅​  | ❌️  | ✅   |
| Telegram| 链接(t.me，需权限)               | ✅​  | ✅​  | ✅​  | ✅   |
| NGA     | 链接(帖子，主楼+前4楼回复)        | ❌️  | ✅​  | ❌️  | ✅   |
| 知乎    | 链接(专栏文章/回答/问题)          | ✅​  | ✅​  | ❌️  | ✅   |
| 网易云  | 链接(歌曲)                        | ❌️  | ❌️  | ✅​  | ✅   |
| QQ音乐  | 链接(歌曲)                        | ❌️  | ❌️  | ✅​  | ✅   |
| 酷狗    | 链接(歌曲分享)                    | ❌️  | ❌️  | ✅​  | ✅   |
| 汽水音乐| 链接(歌曲分享)                    | ❌️  | ❌️  | ✅​  | 🧪   |
| 虎扑    | 链接(BBS 帖子)                    | ❌️  | ✅​  | ❌️  | ✅   |
| 酷安    | 链接(动态)                        | ❌️  | ✅​  | ❌️  | ✅   |
| LOFTER  | 链接(图文/音乐帖，含短链)         | ❌️  | ✅​  | ❌️  | ✅   |
| 堆糖    | 链接(图集/blog)                   | ❌️  | ✅​  | ❌️  | ✅   |
| BUFF    | 链接(资讯/玩家秀)                 | ❌️  | ✅​  | ❌️  | ✅   |
| 小黑盒  | 链接(社区帖子)                    | ✅​  | ✅​  | ❌️  | 🧪   |
| ILLU    | 链接(文章/图集)                   | ❌️  | ✅​  | ❌️  | ✅   |
| 贴吧    | 链接(帖子主楼+回复)               | ✅​  | ✅​  | ❌️  | ✅   |

> [!Note]
> 状态列说明：✅ 表示已实测稳定；🧪 表示**实验性**，受上游接口/风控影响，可能不稳定：
> - **音乐类**：**网易云**已实测稳定，直连官方公开接口，**开箱即用无需配置**；**QQ 音乐**已基于 `qqmusic-api-python` 自建直连，免费歌曲**开箱即用**，VIP/付费歌曲用「qqmusic登录」扫码授权（凭证持久化）；**酷狗**直连官方接口（MD5 签名 + curl_cffi 绕过 SSA 反爬），**开箱即用无需配置**；**汽水**未实测成功；
> - **小黑盒**：签名算法已实现，但存在 **IP 级风控**（`show_captcha`），需配合代理或更换出口 IP。

支持的链接，可参考 [测试链接](https://github.com/fllesser/nonebot-plugin-parser/blob/master/tests/others/test_urls.md)

## 🎨 效果图

插件默认启用 PIL 实现的通用媒体卡片渲染，效果图如下

<div align="center">

<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/9_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/4_pic.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_video.png" width="160" />
<img src="https://raw.githubusercontent.com/fllesser/nonebot-plugin-parser/refs/heads/resources/resources/renderdamine/repost_2_pic.png" width="160" />

</div>

## 💿 安装

> [!Warning] 
> **如果你已经在使用 nonebot-plugin-resolver[2]，请在安装此插件前卸载**

<details>
<summary>使用 nb-cli 安装/更新</summary>
在 nonebot2 项目的根目录下打开命令行, 输入以下指令即可安装

    nb plugin install nonebot-plugin-parser --upgrade

使用 pypi 源更新

    nb plugin install nonebot-plugin-parser --upgrade -i https://pypi.org/simple

安装仓库 dev 分支

    uv pip install git+https://github.com/fllesser/nonebot-plugin-parser.git@dev

</details>

<details>
<summary>使用包管理器安装</summary>
在 nonebot2 项目的插件目录下, 打开命令行, 根据你使用的包管理器, 输入相应的安装命令
<details>
<summary>uv</summary>
使用 uv 安装

    uv add nonebot-plugin-parser

安装仓库 dev 分支

    uv add git+https://github.com/fllesser/nonebot-plugin-parser.git@master

</details>

<details>
<summary>pip</summary>

    pip install --upgrade nonebot-plugin-parser

</details>
<details>
<summary>pdm</summary>

    pdm add nonebot-plugin-parser

</details>
<details>
<summary>poetry</summary>

    poetry add nonebot-plugin-parser

</details>

打开 nonebot2 项目根目录下的 `pyproject.toml` 文件, 在 `[tool.nonebot]` 部分追加写入

    plugins = ["nonebot_plugin_parser"]

</details>

<details>
<summary>使用 nbr 安装(使用 uv 管理依赖可用)</summary>

[nbr](https://github.com/fllesser/nbr) 是一个基于 uv 的 nb-cli，可以方便地管理 nonebot2

    nbr plugin install nonebot-plugin-parser

使用 **pypi** 源安装

    nbr plugin install nonebot-plugin-parser -i "https://pypi.org/simple"

使用**清华源**安装

    nbr plugin install nonebot-plugin-parser -i "https://pypi.tuna.tsinghua.edu.cn/simple"

</details>

<details>
<summary>安装可选依赖</summary>

`ytdlp`, 用于解析 `youtube` 和 `tiktok` 视频

    uv add "nonebot-plugin-parser[ytdlp]"

[emosvg](https://github.com/fllesser/emosvg) 用于渲染 `emoji` 表情, 基于 `cairo` 和 `svg` 实现，`win/mac` 用户，请确保自己会配置 `cairo`, 插件默认使用的依赖于网络的 `apilmoji`，已缓存的 `emoji` 渲染速度略快于 `emosvg`

    uv add "nonebot-plugin-parser[emosvg]"

`htmlkit`, 无 js 渲染 `html`, 插件目前还没有供 `htmlkit` 使用的模版, 因此可忽略此依赖

    uv add "nonebot-plugin-parser[htmlkit]"

`htmlrender`, 使用 `playwright` 渲染 `html`, 插件自 `v2.5.0` 起已正式支持

    uv add "nonebot-plugin-parser[htmlrender]"

现版本推荐组合

    uv add "nonebot-plugin-parser[ytdlp,emosvg]"

`all` 顾名思义，安装所有可选依赖

    uv add "nonebot-plugin-parser[all]"

</details>

<details>
<summary>安装必要组件</summary>

部分解析依赖 `ffmpeg`

`ubuntu/debian`

    sudo apt-get install ffmpeg

其他 `Linux` 参考(原项目推荐): https://gitee.com/baihu433/ffmpeg

`Windows` 参考(原项目推荐): https://www.jianshu.com/p/5015a477de3c

`yt-dlp` 自 `2025.11.12` 起要求用户安装外部 `JavaScript Runtime`，参考 https://github.com/yt-dlp/yt-dlp/releases/tag/2025.11.12, 推荐安装 [Deno](https://deno.com)

`macOS / Linux`

    curl -fsSL https://deno.land/install.sh | sh

`windows`

    irm https://deno.land/install.ps1 | iex

</details>

<details>
<summary>Telegram 解析前置（可选）</summary>

Telegram 解析依赖 [tdl](https://github.com/iyear/tdl)（Telegram Downloader CLI）。
tdl 不在 PyPI，需手动安装二进制；登录后才能解析 t.me 链接。

`macOS / Linux`

    # 安装
    curl -fsSL https://docs.iyear.me/tdl/install.sh | bash

`windows` (PowerShell)

    # 安装
    irm https://docs.iyear.me/tdl/install.ps1 | iex

> 注意：tdl 不读取 `http_proxy`/`https_proxy` 环境变量，国内服务器请配置 `parser_tdl_proxy`。
> 登录会话默认存储于 `~/.tdl`，如需切换账号可用 `tdl login -n <namespace>` 并配置 `parser_tdl_ns`。

</details>

<details>
<summary>Telegram 登录方式（二选一）</summary>

tdl 的登录涉及二维码终端渲染和两步验证(2FA)密码交互，均依赖 TTY 终端环境。

**方式一：bot 全自动登录 `tg登录`（推荐）**

SUPERUSER 在 QQ 发送 `tg登录`，bot 会自动启动 tdl、把二维码渲染成 PNG 发回，
用户扫码后 bot 自动处理 2FA（账号开启两步验证时，再直接发送密码即可）。

> [!CAUTION]
> **平台限制**：`tg登录` 依赖 Unix 伪终端（pty），**仅支持 Linux / macOS / Docker / WSL**。
> Windows 原生环境不可用（`os.fork` / `pty` / `termios` 等 API 不存在）。
>
> Windows 用户请：
> - 在 **Docker 容器**内部署 bot 并登录（推荐），或
> - 在 **WSL** 内部署 bot 并登录，或
> - 改用下方「方式二」在终端手动登录后，把会话目录共享给 bot

**方式二：终端手动登录 `tdl login`**

在 tdl 安装好、且 bot 能访问到同一个 `~/.tdl` 会话目录的终端里执行：

    tdl login            # 扫码登录
    tdl login --type code  # 或手机验证码登录

登录成功后会话写入 `~/.tdl/data/<namespace>`，bot 即可解析 t.me 链接。
注意：bot 与登录终端必须共享同一用户主目录（`HOME`），否则 bot 读不到会话。

</details>

## ⚙️ 配置

<details>
<summary>配置项</summary>

```bash
# [可选] nonebot2 内置配置，若服务器上传带宽太低，建议调高，防止超时
API_TIMEOUT=30.0

# [可选] B 站 cookie, 必须含有 SESSDATA 项，可附加 B 站 AI 总结功能
# 如果需要长期使用此凭据则不应该在浏览器登录账户导致 cookie 被刷新，建议注册个小号获取
# 各项获取方式 https://nemo2011.github.io/bilibili-api/#/get-credential
# ac_time_value 相对特殊，仅用于刷新 Cookies
# B站网页打开开发者工具，进入控制台，输入 window.localStorage.ac_time_value 即可获取其值。
parser_bili_ck="SESSDATA=xxxxxxxxxx;ac_time_value=131231241231241"

# [可选] 允许的 B 站视频编码，越靠前的编码优先级越高
# 可选 "avc"(H.264，体积较大), "hev"(HEVC), "av01"(AV1)
# 后两项在不同设备可能有兼容性问题，如需完全避免，可只填一项，如 '["avc"]'
parser_bili_video_codes='["avc", "av01", "hev"]'

# [可选] B 站视频清晰度
# 360p(16), 480p(32), 720p(64), 1080p(80), 1080p+(112), 1080p_60(116), 4k(120)
parser_bili_video_quality=80

# [可选] 小红书 Cookie, 部分链接解析有水印，可填
parser_xhs_ck=""

# [可选] Youtube Cookie, Youtube 视频因人机检测下载失败，需填
parser_ytb_ck=""

# [可选] PixivNow API 地址，支持自建或第三方 PixivNow 部署
# 参考 https://github.com/journey-ad/Pixiv-Illustration-Resolver
# 国内访问 Pixiv 需要代理，API 请求和图片下载均会使用 parser_proxy 配置的代理
parser_pixiv=""

# [可选] 是否允许解析 R18 / R-18G 内容
# 默认为 false（不解析），设为 true 允许解析
parser_pixivR18=false

# [可选] 代理, 仅作用于 youtube, tiktok, Pixiv 解析
# 推特解析会自动读取环境变量中的 http_proxy / https_proxy(代理软件通常会自动设置)
parser_proxy=None

# [可选] 音频解析，是否需要上传群文件
parser_need_upload=False

# [可选] 视频，图片，音频是否使用 base64 发送
# 注意：编解码和传输 base64 会占用更多的内存,性能和带宽, 甚至可能会使 websocket 连接崩溃
# 因此该配置项仅推荐 nonebot 和 协议端不在同一机器的用户配置
parser_use_base64=False

# [可选] 视频最大解析时长，单位：秒
parser_duration_maximum=480

# [可选] 音视频下载最大文件大小，单位 MB，超过该配置将阻断下载
parser_max_size=90

# [可选] 全局禁止的解析
# 示例 parser_disabled_platforms=["bilibili", "douyin"] 表示禁止了哔哩哔哩和抖音
# 可选值: ["bilibili", "douyin", "kuaishou", "twitter", "youtube", "acfun", "tiktok", "weibo", "xiaohongshu", "pixiv", "telegram"]
parser_disabled_platforms='["twitter"]'

# [可选] 渲染器类型
# 可选 "default"(无图片渲染), "common"(PIL 通用图片渲染), "htmlrender"(htmlrender), "htmlkit"(htmlkit, 暂不可用)
parser_render_type="common"

# [可选] 是否在解析结果中附加原始URL
parser_append_url=False

# [可选] Telegram 解析所用 tdl 二进制路径，默认走 PATH 查找
# 部署前需安装 tdl 并执行 `tdl login` 登录 Telegram 账号
parser_tdl_path="tdl"

# [可选] tdl session namespace，对应 `tdl login -n <ns>` 的命名空间
parser_tdl_ns="default"

# [可选] tdl 专用代理（tdl 不读取 http_proxy 环境变量，必须显式传入）
# 留空时沿用 parser_proxy
parser_tdl_proxy=None

# [可选] 强制解析前缀
# 仅在显式配置后生效；未配置时不会回退到机器人昵称
# 示例: parser_force_prefix="bot" 后，可使用 "bot+链接" 或 "bot 链接" 强制触发解析
parser_force_prefix=""

# [可选] 自定义渲染字体
# 配置字体文件名，并将字体文件放置于 localstore 生成的插件 config 目录下
# 例如: ./config/nonebot_plugin_parser/
parser_custom_font="LXGWZhenKaiGB-Regular.ttf"

# [可选] 是否需要转发媒体内容(超过 4 项时始终使用合并转发)
parser_need_forward_contents=True

# [可选] emoji 渲染 CDN
# 例如 ELK_SH_CDN = "https://emojicdn.elk.sh", MQRIO_DEV_CDN = "https://emoji-cdn.mqrio.dev"
parser_emoji_cdn="https://emojicdn.elk.sh"

# [可选] emoji 渲染样式 "apple", "google", "twitter", "facebook"(默认)
parser_emoji_style="facebook"

# [可选] 短链重定向到暂不支持解析的页面（如 B站会员购商城）时，是否启用浏览器截图兜底
# 需安装 [htmlrender] extras: uv add "nonebot-plugin-parser[htmlrender]" 并执行 playwright install chromium
parser_screenshot=True

# [可选] 截图是否整页（默认仅首屏）
parser_screenshot_full_page=False
```

</details>

<details>
<summary>推荐的字体</summary>

- [LXGW ZhenKai / 霞鹜臻楷](https://github.com/lxgw/LxgwZhenKai) 效果图使用字体
- [LXGW Neo XiHei / 霞鹜新晰黑](https://github.com/lxgw/LxgwNeoXiHei)
- [LXGW Neo ZhiSong / 霞鹜新致宋 / 霞鶩新緻宋](https://github.com/lxgw/LxgwNeoZhiSong)
</details>

## 🎉 使用

|   指令   |         权限          | 需要@ | 范围 |       说明        |
| :------: | :-------------------: | :---: | :--: | :---------------: |
| 开启解析 | SUPERUSER/OWNER/ADMIN |  是   | 群聊 |     开启解析      |
| 关闭解析 | SUPERUSER/OWNER/ADMIN |  是   | 群聊 |     关闭解析      |
|    bm    |           -           |  否   | 群聊 |   下载 B 站音频   |
|    ym    |           -           |  否   | 群聊 | 下载 youtube 音频 |
|  tg授权  |       SUPERUSER       |  否   | 任意 | 授权用户使用 TG 解析 |
| tg取消授权|      SUPERUSER       |  否   | 任意 | 取消 TG 解析授权  |
|  tg白名单|       SUPERUSER       |  否   | 任意 | 查看 TG 授权列表  |
|  tg登录  |       SUPERUSER       |  否   | 任意 | TG 扫码登录(生成二维码) |
|  blogin  |       SUPERUSER       |  否   | 私聊 | 扫码获取 B 站凭证 |
| qqmusic登录 |       SUPERUSER       |  否   | 任意 | QQ 音乐扫码登录(获取 VIP 凭证) |
| qqmusic登出 |       SUPERUSER       |  否   | 任意 | 清除 QQ 音乐登录态 |

### 👤 用户授权与黑名单

> [!Note]
> 细粒度授权:按「用户 + 受控项」授权,分**全局**(跨群生效)与**群组**(本群独立)两层,全局优先。
> 受控项目前为 `强制解析`(前缀强制解析授权);后续会扩展到更多高权限指令。
> 黑名单为**全局**封禁,命中后不解析、不响应功能指令(SUPERUSER 不可被拉黑,防锁死)。

| 命令 | 权限 | 范围 | 说明 |
| --- | --- | --- | --- |
| `par授权 @用户 [受控项...]` | SUPERUSER | 任意 | 本群授权(私聊触发=全局);不写受控项=授权全部 |
| `par全局授权 @用户 [受控项...]` | SUPERUSER | 任意 | 全局授权(跨群生效);不写受控项=授权全部 |
| `par取消授权 @用户 [受控项...]` | SUPERUSER | 任意 | 撤销授权(全局+本群);不写受控项=撤销全部 |
| `par授权查看` | SUPERUSER | 任意 | 查看全局 + 本群授权名单 |
| `par拉黑 @用户` | SUPERUSER | 任意 | 全局拉黑(不解析/不响应功能指令) |
| `par解除拉黑 @用户` | SUPERUSER | 任意 | 解除全局拉黑 |
| `par黑名单` | SUPERUSER | 任意 | 查看全局黑名单 |

**典型用法 ——「关闭解析 + 前缀强制解析授权」组合:**

群管 `@bot 关闭解析 抖音` 后,默认无人能解析抖音链接。此时 SUPERUSER 执行:

```
par授权 @张三 强制解析
```

张三便可在该群用 `par+抖音链接` 强制解析;其他未授权用户发抖音链接或前缀强制解析都被拦截。

> 受控项 `强制解析` 是前缀强制解析(`parser_force_prefix` + 链接)的授权键。平台**未被关闭**时,人人可用前缀(行为不变);只有平台被群管关闭后,授权体系才介入。

> 持久化数据存于 nonebot-plugin-localstore 数据目录:`user_grants.json` / `user_blacklist.json`。

### 🎵 点歌

> [!Note]
> 基于「网易云 / QQ 音乐 / 酷狗」三大服务点歌，前缀复用 `parser_force_prefix`（未配置默认 `par`）。

|         命令         | 权限 | 范围 |        说明        |
| -------------------- | --- | --- | ------------------ |
| `par点歌 <歌名>`     |  -  | 任意 | 三服务并发搜索，合并取前 10 首候选 |
| `par网易云 <歌名>` / `parwyy <歌名>` |  -  | 任意 | 仅网易云搜索 |
| `parqq <歌名>`       |  -  | 任意 | 仅 QQ 音乐搜索 |
| `par酷狗 <歌名>` / `parkg <歌名>` |  -  | 任意 | 仅酷狗搜索 |
| `par<序号>`（如 `par1`） |  -  | 任意 | 选择搜索结果中的第 N 首 |

- 三服务并发时，**单服务失败会被静默**，由其他服务补齐名额；**全部失败或指定服务失败**才会提示「搜索失败，请稍后重试」。
- 候选列表渲染为图片发送，按 `par<序号>` 选择；选定后复用对应平台的链接解析流程（音乐卡片 + 音频）。
- 选择窗口期为 **5 分钟**，且按用户 + 场景隔离（不同用户/群聊互不影响）。

> Telegram 解析需消耗本机 tdl 会话，仅 SUPERUSER 或被 SUPERUSER 授权（`tg授权 <用户ID/@用户名>`）的用户可触发。
> 首次使用需登录：SUPERUSER 执行 `tg登录`，bot 会把二维码渲染成图片发回，用 Telegram App 扫码即可（会话写入 `~/.tdl`）。
> ⚠ `tg登录`（bot 全自动登录）仅支持 **Linux / macOS / Docker / WSL**，Windows 原生环境不可用。Windows 用户请使用容器/WSL 部署，或在终端执行 `tdl login` 手动登录（详见上方「Telegram 登录方式」）。

> QQ 音乐解析免费歌曲**开箱即用**；VIP/付费歌曲需 SUPERUSER 执行 `qqmusic登录`，bot 发回二维码后用手机 QQ 扫码授权，登录态会持久化保存（存于 nonebot-plugin-localstore 数据目录）。需重新登录时执行 `qqmusic登出` 后再次 `qqmusic登录`。

## 🧩 扩展

> [!IMPORTANT]
> 插件自 `v2.2.0` 版本开始支持自定义解析器，通过继承 `BaseParser` 类并实现 `platform`, `handle` 即可

<details>
<summary>完整示例</summary>

```python
from re import Match
from typing import ClassVar

from httpx import AsyncClient
from nonebot import require

require("nonebot_plugin_parser")
from nonebot_plugin_parser.parsers import BaseParser, Platform, handle

class ExampleParser(BaseParser):
    """示例视频网站解析器"""

    platform: ClassVar[Platform] = Platform(name="example", display_name="示例网站")

    @handle("ex.short", r"ex\.short/\w+)")
    async def _parse_short_link(self, searched: Match[str]):
        """解析短链"""
        url = f"https://{searched.group(0)}"
        # 重定向再解析，请确保重定向链接的 handle 存在
        # 比如 url 重定向到 example.com/... 就会调用 _parse 解析
        return await self.parse_with_redirect(url)

    @handle("example.com", r"example\.com/video/(?P<video_id>\w+)")
    @handle("exam.ple", r"exam\.ple/(?P<video_id>\w+)")
    async def _parse(self, searched: Match[str]):
        # 1. 提取视频 ID
        video_id = searched.group("video_id")

        # 2. 请求 API 获取视频信息
        async with AsyncClient(headers=self.headers, timeout=self.timeout) as client:
            resp = await client.get(f"https://api.example.com/video/{video_id}")
            resp.raise_for_status()
            data = resp.json()

        # 3. 提取数据
        title = data["title"]
        author_name = data["author"]["name"]
        avatar_url = data["author"]["avatar"]
        video_url = data["video_url"]
        cover_url = data["cover_url"]
        duration = data["duration"]
        timestamp = data["publish_time"]
        description = data.get("description", "")

        # 4. 视频内容
        author = self.create_author(author_name, avatar_url)
        video = self.create_video_content(video_url, cover_url, duration)

        # 5. 图集内容
        image_urls = data.get("images")
        images = self.create_image_contents(image_urls)

        # 6. 返回解析结果
        return self.result(
            title=title,
            text=description,
            author=author,
            contents=[video, *images],
            timestamp=timestamp,
            url=f"https://example.com/video/{video_id}",
        )

```

</details>
<details>
<summary>辅助函数</summary>

> 构建作者信息

```python
author = self.create_author(
    name="作者名",
    avatar_url="https://example.com/avatar.jpg",   # 可选，会自动下载
    description="个性签名"                          # 可选
)
```

> 构建视频内容

```python
# 方式1：传入 URL，自动下载
video = self.create_video_content(
    url_or_task="https://example.com/video.mp4",
    cover_url="https://example.com/cover.jpg",  # 可选
    duration=120.5                               # 可选，单位：秒
)

# 方式2：传入已创建的下载任务
from nonebot_plugin_parser.download import DOWNLOADER
video_task = DOWNLOADER.download_video(url, ext_headers=self.headers)
video = self.create_video_content(
    url_or_task=video_task,
    cover_url=cover_url,
    duration=duration
)
```

> 构建图集内容

```python
# 并发下载图集内容
images = self.create_image_contents([
    "https://example.com/img1.jpg",
    "https://example.com/img2.jpg",
])
```

> 创建动图内容（GIF)，平台一般只提供视频（后续插件会做自动转为 gif 的处理)

```python
dynamics = self.create_dynamic_contents([
    "https://example.com/dynamic1.mp4",
    "https://example.com/dynamic2.mp4",
])
```

> 重定向 url

```python
real_url = await self.get_redirect_url(
    url="https://short.url/abc",
    headers=self.headers  # 可选
)
```

</details>

## 🌟 星星

[![Star History Chart](https://api.star-history.com/svg?repos=fllesser/nonebot-plugin-parser&type=date&legend=top-left)](https://www.star-history.com/#fllesser/nonebot-plugin-parser&type=date&legend=top-left)

## 🎉 致谢

[nonebot-plugin-resolver](https://github.com/zhiyu1998/nonebot-plugin-resolver)
[parse-video-py](https://github.com/wujunwei928/parse-video-py)
[nonebot-plugin-parser-lite](https://github.com/sokoko-org/nonebot-plugin-parser-lite) — 本项目的知乎、网易云、酷狗、酷我、汽水音乐、虎扑、酷安、LOFTER、堆糖、BUFF 平台解析器移植自此项目，特此致谢。
