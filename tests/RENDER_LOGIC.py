"""
B站动态渲染逻辑说明
"""

print("""
╔══════════════════════════════════════════════════════════════════════════╗
║                      B站动态渲染逻辑说明                                  ║
╚══════════════════════════════════════════════════════════════════════════╝

【数据流程】

1. API 获取 → bilibili_api.dynamic.Dynamic.get_info()
   └─> 返回原始 JSON 数据

2. 数据转换 → msgspec.convert(raw_data, DynamicData)
   └─> DynamicData.item (DynamicInfo)
       ├─> name: "略nd"
       ├─> text: "分享图片"
       └─> image_urls: ["http://i0.hdslb.com/...jpg"]

3. 创建 ParseResult → parse_dynamic()
   └─> ParseResult
       ├─> platform: bilibili
       ├─> author: Author(name="略nd", avatar=...)
       ├─> text: "分享图片"
       ├─> contents: [ImageContent(task1)]
       └─> timestamp: 1768806647

【渲染流程】（取决于 render_type 配置）

═══════════════════════════════════════════════════════════════════════════

方案1: CommonRenderer (render_type = "common")
-------------------------------------------------------
生成图片卡片，包含：
- 作者头像 + 昵称 + 时间
- 文字内容
- 图片（嵌入在卡片中）
- 转发内容（如果有）

发送消息：
1. 发送图片卡片（1张图片）
2. 不再单独发送图片

═══════════════════════════════════════════════════════════════════════════

方案2: DefaultRenderer (render_type = "default")
---------------------------------------------------------
生成文本消息 + 单独发送图片

发送消息：
1. 发送文本消息：
   "哔哩哔哩 @略nd"
   "分享图片"
   "链接: ..."

2. 发送图片（render_contents）：
   - 如果图片 <= 4 张：合并为1条消息
   - 如果图片 > 4 张：转为转发消息

═══════════════════════════════════════════════════════════════════════════

方案3: HtmlRenderer (render_type = "htmlrender")
--------------------------------------------------------
使用 nonebot_plugin_htmlrender 渲染 HTML

═══════════════════════════════════════════════════════════════════════════

【当前测试结果】

测试 ID: 1159504791855955984

✅ 数据完整：
- text: "分享图片"
- image_urls: 1 张图片
- timestamp: 1768806647

✅ msgspec 转换成功：
- DynamicInfo.text ✓
- DynamicInfo.image_urls ✓

生成的文件：
1. tests/render_raw_api_data.json - 原始 API 数据
2. tests/render_output/converted_data.json - 转换后的数据

【可能的问题】

1. render_type 配置不正确
   → 检查配置文件中的 parser_render_type

2. 图片下载失败
   → 检查网络连接和 B站 CDN 访问

3. 图片路径问题
   → 检查缓存目录权限

4. 图片是 HTTP 而不是 HTTPS
   → B站图片 URL: http://i0.hdslb.com/bfs/new_dyn/...
   → 某些平台可能不支持 HTTP 图片

【推荐配置】

如果希望图片单独发送（更清晰）：
  parser_render_type = "default"

如果希望所有内容在一张图片中（更简洁）：
  parser_render_type = "common"

【调试步骤】

1. 查看 NoneBot 日志，确认：
   - 解析成功
   - 图片下载成功
   - 渲染器被调用

2. 检查生成的文件：
   - tests/render_output/converted_data.json
   - 确认 text 和 image_urls 不为空

3. 检查缓存目录：
   - 查看下载的图片文件是否存在
   - 检查文件大小是否正常

╚══════════════════════════════════════════════════════════════════════════╝
""")
