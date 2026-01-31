# B站动态解析测试报告

## 测试时间
2026-01-31

## 测试链接
- https://m.bilibili.com/opus/1159504791855955984
- https://www.bilibili.com/opus/1156587796127809560

## 测试结果

### ✅ 数据获取
- bilibili_api.dynamic.Dynamic.get_info() - **成功**
- bilibili_api.opus.Opus.get_info() - **对某些ID失败**

### ✅ 数据转换
- msgspec.convert() → DynamicData - **成功**
- DynamicInfo.text - **有内容**："分享图片"
- DynamicInfo.image_urls - **有图片**：1张
- DynamicInfo.timestamp - **有时间戳**：1768806647

### ✅ 动态类型支持
- `DYNAMIC_TYPE_DRAW` - **支持**（画图动态）
- `DYNAMIC_TYPE_FORWARD` - **支持**（转发动态）
- `DYNAMIC_TYPE_OPUS` - **部分支持**（需降级到Dynamic）

## 解析数据详情

### 1159504791855955984 (非转发)
```
类型: DYNAMIC_TYPE_DRAW
作者: 略nd
文本: 分享图片
图片: 1张 (2000x1500)
转发: 否
```

### 1156587796127809560 (转发)
```
类型: DYNAMIC_TYPE_FORWARD
转发评论: 好耶
原动态: 2张图片
原动态文本: 春随淑气融残雪...
```

## 渲染逻辑

### CommonRenderer (render_type="common")
- 生成图片卡片，包含所有内容
- 图片嵌入在卡片中
- 发送1张图片

### DefaultRenderer (render_type="default")
- 先发送文本消息
- 再单独发送图片
- 图片合并为1条或转发消息

## 生成的测试文件

1. **tests/render_raw_api_data.json**
   - 原始 API 返回数据
   - 完整的 JSON 结构

2. **tests/render_output/converted_data.json**
   - msgspec 转换后的数据
   - 提取的关键字段

3. **tests/test_*.py**
   - 各种测试脚本
   - 可单独运行验证

## 可能的问题

### 1. 图片未显示
- **原因**：CommonRenderer 将图片嵌入卡片
- **解决**：使用 DefaultRenderer 单独发送图片

### 2. 文本未显示
- **原因**：text 字段为空
- **检查**：查看 converted_data.json 中的 text 字段

### 3. HTTP 图片问题
- **现象**：B站图片使用 HTTP 而非 HTTPS
- **影响**：某些平台可能不支持 HTTP 图片
- **解决**：可能在下载时自动转换或平台适配

## 推荐配置

```toml
# 方案1: 图片单独发送（推荐）
parser_render_type = "default"

# 方案2: 图片嵌入卡片
parser_render_type = "common"
```

## 下一步

1. **检查配置**
   - 确认 `parser_render_type` 设置
   - 重启 NoneBot 使配置生效

2. **查看日志**
   - 启用 DEBUG 模式
   - 查看解析和下载日志

3. **测试验证**
   - 使用提供的测试链接
   - 观察实际发送的消息

## 文件清单

测试脚本：
- test_render_full.py - 完整测试
- test_render_simple.py - 简化测试
- RENDER_LOGIC.py - 渲染逻辑说明

数据文件：
- tests/render_raw_api_data.json
- tests/render_output/converted_data.json
