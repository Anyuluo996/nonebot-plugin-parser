# 平台控制命令参数注入修复总结

## 背景

在群聊中发送如下命令时：

- `@机器人 关闭解析 b站`
- `@机器人 开启解析 b站`

日志显示 matcher 已经命中 `nonebot_plugin_parser.matchers.filter`，但 handler 在运行阶段被标记为 `skipped`，导致机器人没有任何回复。

## 问题现象

典型日志特征如下：

1. 事件被 `disable_parser` 或 `enable_parser` 对应 matcher 命中
2. 日志出现 `Running handler Dependent(call=disable_parser)`
3. 随后出现 `Handler Dependent(call=disable_parser) skipped`
4. 最终 matcher 结束，但没有发送任何消息

这说明问题不在命令匹配阶段，而是在 handler 参数依赖注入阶段。

## 根因分析

平台控制命令的 handler 原先写法为：

- `args: CommandArg = CommandArg()`

这会让参数类型标注本身变成错误的依赖声明方式。对于 NoneBot 的命令参数注入，正确写法应为：

- `args: Message = CommandArg()`

也就是说：

- 类型标注应为可注入的消息类型 `Message`
- 默认值才应使用参数提供器 `CommandArg()`

原来的写法会导致依赖系统无法正确解析参数，进而把 handler 跳过。

## 修复内容

### 1. 修正 handler 参数声明

修改文件：`src/nonebot_plugin_parser/matchers/filter.py`

修复点：

- 新增 `from nonebot.adapters import Message`
- 将 `enable_parser` 的参数从 `args: CommandArg = CommandArg()` 改为 `args: Message = CommandArg()`
- 将 `disable_parser` 的参数从 `args: CommandArg = CommandArg()` 改为 `args: Message = CommandArg()`

### 2. 补充回归测试

修改文件：`tests/test_platform_control.py`

新增两类回归测试：

1. **签名级回归**
   - 校验 `enable_parser` / `disable_parser` 的 `args` 注解必须为 `nonebot.adapters.Message`
   - 校验默认值仍然来自 `CommandArg()`

2. **行为级回归**
   - 使用真实 `OneBot V11 Message` 对象直接调用 `disable_parser`
   - 验证能正确识别 `b站` 参数
   - 验证会回复 `b站 解析已关闭`
   - 验证禁用状态被正确写入为 `bilibili`

## 修复后的效果

修复后，下列命令在命中 matcher 后可以正常进入 handler 并处理参数：

- `@机器人 关闭解析 b站`
- `@机器人 开启解析 b站`
- `@机器人 关闭解析 bilibili`
- `@机器人 开启解析 bilibili`

对应行为：

- 可以正确解析中文别名与标准平台名
- 可以正常发送开启/关闭结果消息
- 不再出现“matcher 命中但 handler 被 skipped”的情况

## 验证结果

本次修复完成后已执行以下验证：

### 语法检查

- `uv run python -m compileall src tests/test_platform_control.py`

结果：通过

### 测试

- `uv run pytest tests/test_platform_control.py`

结果：`25 passed`

### Diagnostics

检查文件：

- `src/nonebot_plugin_parser/matchers/filter.py`
- `tests/test_platform_control.py`

结果：无报错

## 影响范围

本次修复影响的平台控制命令包括：

- `开启解析`
- `关闭解析`

不改变以下既有行为：

- `to_me()` 规则要求
- `SUPERUSER | OWNER() | ADMIN()` 权限要求
- 平台开启/关闭状态的持久化逻辑

## 总结

这次问题的本质是 NoneBot handler 参数注入声明错误，而不是命令匹配失败。修复后，平台控制命令已经可以正确识别命令参数并执行开启/关闭逻辑，同时补充了足够直接的回归测试，避免后续再次把 `Message = CommandArg()` 写回错误形式。