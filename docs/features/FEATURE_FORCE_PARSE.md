# 解析前缀功能说明

## 功能概述

新增了解析前缀功能，允许在平台解析被关闭的情况下，通过添加前缀来强制触发解析。

## 使用方法

### 前缀格式

使用自定义前缀（通过环境变量配置），支持两种格式：

1. **使用 + 号连接**：`前缀+链接`
   - 例如：`jx+https://www.bilibili.com/video/BV1xx411c7mD`

2. **使用空格连接**：`前缀 链接`
   - 例如：`jx https://www.bilibili.com/video/BV1xx411c7mD`

### 配置

在 `.env` 或 `.env.dev` 文件中设置：

```env
PARSER_FORCE_PREFIX=jx
```

**注意**：
- 如果不设置 `PARSER_FORCE_PREFIX`，则无法使用前缀强制解析功能
- 不要使用 `NICKNAME` 作为前缀，因为会被 NoneBot 的其他功能处理
- 建议使用简短且不易与日常对话冲突的前缀，如 `jx`、`parse` 等

## 使用场景

### 场景 1：正常解析（平台未禁用）

```
用户: https://www.bilibili.com/video/BV1xx411c7mD
机器人: [正常解析并返回结果]
```

### 场景 2：禁用后无法解析

```
用户: 关闭解析 bilibili
机器人: 已关闭 bilibili 平台的解析功能

用户: https://www.bilibili.com/video/BV1xx411c7mD
机器人: [无响应，因为B站已关闭]
```

### 场景 3：使用前缀强制解析

```
用户: jx+https://www.bilibili.com/video/BV1xx411c7mD
机器人: [开始解析并返回结果，因为使用了前缀强制触发]
```

## 技术实现

### 修改的文件

1. **src/nonebot_plugin_parser/config.py**
   - 添加 `parser_force_prefix` 配置选项
   - 添加 `parse_prefix` 属性，优先返回 `parser_force_prefix`，否则返回 NICKNAME

2. **src/nonebot_plugin_parser/matchers/rule.py**
   - 定义 `PSR_FORCE_PARSE_KEY` 常量
   - 在 `KeywordRegexRule.__call__` 中实现前缀检测逻辑
   - 检测到前缀时标记为强制解析模式

3. **src/nonebot_plugin_parser/matchers/__init__.py**
   - 在 `parser_handler` 中获取强制解析标记
   - 强制解析时跳过平台禁用检查

### 核心逻辑

```python
# 1. 检测前缀
if text.startswith(f"{parse_prefix}+") or text.startswith(f"{parse_prefix} "):
    force_parse = True
    text = text[len(f"{parse_prefix}+"):].lstrip()
    state[PSR_FORCE_PARSE_KEY] = True

# 2. 跳过禁用检查
if not force_parse and not is_platform_enabled(session, parser.platform.name):
    return  # 跳过解析
```

## 测试

运行测试脚本验证功能：

```bash
# 简化版测试（验证代码逻辑）
python tests/test_force_parse_simple.py

# 功能演示
python tests/demo_force_parse.py
```

## 注意事项

1. **必须配置 PARSER_FORCE_PREFIX**：如果不设置此环境变量，前缀功能将无法使用
2. **不要使用 NICKNAME 作为前缀**：避免与 NoneBot 的内置功能冲突
3. 前缀区分大小写
4. 前缀后必须跟 + 号或空格
5. 使用前缀后，即使平台被禁用也会执行解析
6. 此功能适用于所有支持的解析平台
7. 建议使用简短的前缀，如 `jx`（解析）、`p`（parse）等
