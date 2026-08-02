# 用户授权与黑名单 设计与实现记录

> 状态：已实现(第 1 批:授权骨架 + 黑名单 + 前缀强制解析授权)
> 范围：细粒度用户授权 / 全局黑名单 / 前缀强制解析授权 / 堵上 force_parse 绕过「关闭解析」漏洞

---

## 一、问题背景

调研发现插件存在两类权限薄弱点:

1. **前缀强制解析可绕过群管「关闭解析」** —— `parser_handler` 中 `force_parse=True` 时直接跳过 `is_platform_enabled` 检查(`matchers/__init__.py:144`),任何群员用 `par+链接` 即可在群管关闭某平台后继续解析。群管的「关闭解析」形同虚设。
2. **无黑名单机制** —— 无法封禁滥用用户;`bm`/`ym`/点歌等用户级指令完全无权限检查。

需求:授权用户权限 + 细分指令,引入黑名单。

---

## 二、设计决策(与用户对齐)

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 授权粒度 | 授权时指定可用受控项(空格分隔),不写=全部 | 灵活且语法简洁 |
| 作用域 | 两层:全局(跨群)+ 群组(本群),全局优先 | 兼顾跨群授权与群管自治 |
| 强制解析/自动解析 | 同一套授权;前缀用语义键 `强制解析` | 统一心智模型 |
| 黑名单作用域 | 全局 | 「拉黑」直觉=全局封 |
| 前缀授权检查时机 | **仅平台被群管关闭时检查** | 平台开着时人人可用前缀(行为零变化) |
| 持久化 | 同步 `write_text`(JSON) | 数据量小(KB 级),沿用 filter.py 风格,无需异步/锁 |

---

## 三、数据模型

新建 `matchers/auth.py`,持久化到 localstore 数据目录。

### `user_grants.json`

```jsonc
{
  "global": {
    "123456": [],                    // [] = 授权全部受控项
    "789": ["强制解析", "parqq登录"]  // 显式列表
  },
  "groups": {
    "QQClient_群号": {
      "999": ["强制解析"]
    }
  }
}
```

- 群组键沿用 `filter.get_group_key()` 的 `{scope}_{scene_path}` 格式。
- `[]` / `null` / 缺失均视为「全部授权」(`_matches` 函数统一处理)。

### `user_blacklist.json`

```json
["111", "222"]
```

全局黑名单 user_id 列表。

---

## 四、判定优先级

```
is_authorized(user_id, item, session):
  1. SUPERUSER        → True   (永远放行, 防锁死)
  2. in blacklist     → False  (黑名单最高优先级)
  3. global[user_id] 命中 → True
  4. groups[gk][user_id] 命中 → True
  5. else             → False
```

`is_blacklisted` 单独暴露: SUPERUSER 永不被拉黑(`_is_super` 短路返回 False)。

---

## 五、关键改动点

### 1. `parser_handler`(`matchers/__init__.py`)

在 `is_platform_enabled` 检查**之后**、telegram 网关**之前**,插入:

```python
# 黑名单: 全局封禁用户不解析
if user_id and auth.is_blacklisted(user_id):
    return

# 前缀强制解析授权: 仅平台被关闭时才检查
if force_parse and not platform_enabled:
    if not user_id or not auth.is_force_parse_authorized(user_id, session):
        raise TipException("该平台已被关闭，你没有强制解析权限，请联系管理员")
```

**关键语义**:平台开着时 `force_parse` 完全不检查授权 → 现状行为零变化;只有群管关闭某平台后,授权体系才介入,正好实现「关闭 + 前缀授权」组合,且堵上绕过漏洞。

### 2. 黑名单拦截用户级指令

`bm` / `ym` / 点歌 / `par<序号>` 注册时挂 `auth.not_blacklisted()` Rule 或在 handler 内检查。SUPERUSER 指令本身不需要(超管永不被拉黑)。

### 3. 新增 7 个指令

| 指令 | 说明 |
| --- | --- |
| `par授权` / `par全局授权` | 写入群组/全局授权 |
| `par取消授权` | 撤销(全局+本群) |
| `par授权查看` | 查看名单 |
| `par拉黑` / `par解除拉黑` / `par黑名单` | 黑名单管理 |

`@用户` 提取:`_extract_target_user` 优先取消息 at 段(`qq`/`user_id`),取不到回退到 `_normalize_user_id`(支持 `@用户名`/纯数字)。

---

## 六、向后兼容性(零行为变化承诺)

- `user_grants.json` / `user_blacklist.json` 不存在 → 空配置初始化。
- 黑名单默认空 → 所有人维持现状。
- 前缀授权检查**仅在平台关闭时触发** → 平台开着时人人可用前缀。
- Telegram 白名单(`tg_whitelist.json`)保留不动,作 telegram 二次过滤。
- SUPERUSER 在所有判定中直接放行。

**唯一行为变化**(符合预期):群管关闭某平台后,原本「任何人 par+链接 绕过」的漏洞被堵上 → 改为「只有被授权的人能绕过」。

---

## 七、实施分批

### 第 1 批(本次,已完成)

1. ✅ `matchers/auth.py`:数据模型、load/save、判定函数、`not_blacklisted` Rule
2. ✅ `parser_handler`:黑名单 + 前缀授权检查
3. ✅ bm/ym/点歌/par序号 挂 `not_blacklisted()`
4. ✅ 7 个新指令
5. ✅ `tests/test_auth.py`:19 个单元测试
6. ✅ `tests/test_force_parse.py`:更新为反映新语义(未授权被拒 + 授权后放行)
7. ✅ README 指令表 + 权限说明
8. ✅ 本设计记录

### 第 2 批(后续,本次不做)

- 定义 `SUPER_OR_AUTHORIZED(command)` Permission 工厂,改造 `parqq登录`/`dycookie` 等注册点,让被授权用户也能触发(判定基础设施已由 `is_authorized` 提供)。
- 届时讨论 bm/ym/点歌是否纳入「收紧清单」。

---

## 八、不做的事(范围控制)

- ❌ 不做 par收紧/par放开/par权限状态(「关闭解析 + 前缀强制解析授权」已覆盖该需求,避免重复)。
- ❌ 不引入数据库、不做 RBAC 角色、不做 rate limit / cooldown。
- ❌ 不改 `config.py`(全运行时驱动,无需 .env 配置项)。
- ❌ 不迁移/删除 Telegram 白名单。
- ❌ bm/ym/点歌 不纳入「收紧清单」(留第 2 批)。

---

## 九、第 2 批:命令下放 + 前缀一致性(已完成)

### 问题背景

第 1 批上线后,用户反馈两个问题:

1. **管理命令名不跟随前缀**:第 1 批把 `par授权`/`par拉黑` 等硬编码为 `par` 开头,但 `parser_force_prefix` 可配置(如 `jx`),凭据命令(`jxqq登录`)与管理命令(`par授权`)命名风格不一致。
2. **凭据命令无法下放**:`dycookie`/`parqq登录` 等仍仅 SUPERUSER 可用,SUPERUSER 无法授权他人代为维护凭据。

### 设计决策(与用户对齐)

| 决策点 | 选择 |
| --- | --- |
| 管理命令前缀 | `prefix = pconfig.parse_prefix or "par"`,空前缀回退 `par`(避免 `授权`/`拉黑` 等短命令冲突) |
| 凭据命令前缀 | `blogin`/`dyttwid`/`dycookie` 保持硬编码不拼前缀(自带平台标识,不冲突) |
| 授权键 | **语义键(不含前缀)**,与命令前缀解耦 |
| 授权键粒度 | 每命令一个语义键(最细粒度) |

### 语义键清单(`auth.py`)

| 常量 | 语义键 | 对应命令 | 下放 |
| --- | --- | --- | --- |
| `FORCE_PARSE` | `强制解析` | 前缀强制解析 | ✅(第 1 批) |
| `BILI_LOGIN` | `bilibili登录` | `blogin`(仅私聊) | ✅ |
| `NETEASE_LOGIN` | `网易云登录` | `par网易云登录`/`parwyy登录`/登出 | ✅ |
| `QQ_LOGIN` | `qq登录` | `parqq登录`/`parqq登出` | ✅ |
| `DY_TTWID` | `抖音ttwid` | `dyttwid`/`dyttwid查看` | ✅ |
| `DY_COOKIE` | `抖音cookie` | `dycookie`/`dycookie查看` | ✅ |

不下放的命令:管理命令本身(`par授权` 等)、`tg登录`(改变共享 tdl 会话)。

### 关键改动

1. **`auth.py` 新增**:
   - 凭据语义键常量 + `DELEGABLE_ITEMS` 元组
   - `COMMAND_TO_ITEM` 映射 + `register_command_item()` 登记接口
   - `resolve_item()`:把用户输入(语义键/真实命令名/带前缀命令名)归一化为语义键
   - `super_or_authorized(item) -> Permission`:SUPERUSER 短路 + 授权判定(用 `SUPERUSER | Permission(...)`)
   - `private_authorized(item) -> Permission`:私聊限定版本(用于 `blogin`)

2. **`__init__.py` 改动**:
   - 管理命令包进 `_register_admin_commands()`,命令名用 `f"{prefix}xxx"`
   - 凭据命令注册点 `permission=SUPERUSER` 改为 `permission=auth.super_or_authorized(语义键)`
   - 音乐登录命令额外调用 `auth.register_command_item()` 登记映射
   - 新增 `_parse_items()`:授权命令的受控项参数批量归一化

### NoneBot Permission 约束

NoneBot **不允许 Permission 之间用 `&`**(`And operation between Permissions is not allowed`)。
故 `private_authorized` 不能写成 `Permission(_private) & super_or_authorized(...)`,
而要把私聊检查与授权检查合并进同一个 async check 函数,再用 `SUPERUSER | Permission(...)` 组合。

### 授权命令参数解析

用户授权时可输入以下任意形态,`resolve_item` 统一归一化:
- 语义键:`强制解析` / `qq登录` / `网易云登录` / `bilibili登录` / `抖音ttwid` / `抖音cookie`
- 真实命令名(硬编码):`blogin` / `dyttwid` / `dycookie`
- 带前缀命令名(注册后):`parqq登录` / `par网易云登录`

无法识别的输入会让授权命令报错并提示可用受控项清单。

### 向后兼容性

- 旧语义键 `强制解析` 不变,第 1 批的授权数据无需迁移。
- 凭据命令原 SUPERUSER 仍可触发(`super_or_authorized` 内含 SUPERUSER 短路)。
- 管理命令空前缀时回退 `par`,与第 1 批硬编码行为一致。

