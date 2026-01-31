# B站动态转发类型解析问题修复

## 问题描述
用户反馈：转发动态只显示用户头像和ID，没有显示动态的文字和图片内容。

测试链接：
- 普通图文动态: https://m.bilibili.com/opus/1159504791855955984
- 转发动态: https://m.bilibili.com/opus/1156587796127809560

## 问题分析

### 数据结构
B站 API 返回的转发动态数据结构：

```json
{
  "item": {
    "type": "DYNAMIC_TYPE_FORWARD",
    "modules": {
      "module_author": {
        "name": "星空future",  // 转发者
        ...
      },
      "module_dynamic": {
        "major": null,        // ← 转发类型，item 的 major 为 null
        "desc": {
          "text": "好耶..."   // ← 转发评论
        }
      }
    },
    "orig": {                 // ← 原动态数据在这里
      "type": "DYNAMIC_TYPE_DRAW",
      "modules": {
        "module_author": {
          "name": "糊涂的小炎陵",  // 原作者
          ...
        },
        "module_dynamic": {
          "major": {
            "opus": {
              "summary": {
                "text": "春随淑气融残雪..."  // ← 原动态文本
              },
              "pics": [                   // ← 原动态图片
                {
                  "url": "http://i0.hdslb.com/..."
                },
                {
                  "url": "http://i0.hdslb.com/..."
                }
              ]
            }
          }
        }
      }
    }
  }
}
```

### 问题根源
1. **msgspec 转换失败**: 当使用 `convert(raw_data, DynamicData)` 时，msgspec 无法正确转换嵌套的 `orig` 字段为 `DynamicInfo` 类型
2. **orig 为 None**: 转换后 `dynamic_data.orig` 为 None，导致代码没有使用原动态的内容
3. **item 内容为空**: 转发类型的 `item.major` 为 null，导致无法获取图片和文本

## 修复方案

### 修改文件: `src/nonebot_plugin_parser/parsers/bilibili/__init__.py`

#### 修改前（第 180-200 行）:
```python
async def parse_dynamic(self, dynamic_id: int):
    from bilibili_api.dynamic import Dynamic
    from .dynamic import DynamicData

    dynamic = Dynamic(dynamic_id, await self.credential)
    dynamic_data = convert(await dynamic.get_info(), DynamicData)

    # 获取主要动态信息
    dynamic_info = dynamic_data.item

    # 如果是转发类型，尝试获取原动态的内容
    if dynamic_data.orig:  # ← orig 为 None，条件不满足
        dynamic_info = dynamic_data.orig
```

#### 修改后:
```python
async def parse_dynamic(self, dynamic_id: int):
    from bilibili_api.dynamic import Dynamic
    from .dynamic import DynamicData, DynamicInfo

    dynamic = Dynamic(dynamic_id, await self.credential)
    raw_data = await dynamic.get_info()

    # msgspec 转换主数据
    dynamic_data = convert(raw_data, DynamicData)

    # 手动处理 orig 字段（msgspec 可能无法正确转换嵌套的 orig）
    orig_info: DynamicInfo | None = None
    if raw_data.get('item', {}).get('orig'):
        try:
            orig_info = convert(raw_data['item']['orig'], DynamicInfo)
        except Exception as e:
            logger.warning(f"转换 orig 数据失败: {e}")

    # 获取主要动态信息
    dynamic_info = dynamic_data.item

    # 如果是转发类型，尝试获取原动态的内容
    if orig_info:  # ← 使用手动转换的 orig_info
        dynamic_info = orig_info
```

### 修复说明
1. **先获取原始数据**: `raw_data = await dynamic.get_info()`
2. **手动转换 orig**: 单独转换 `raw_data['item']['orig']` 为 `DynamicInfo`
3. **使用手动转换的结果**: `if orig_info:` 替代 `if dynamic_data.orig:`
4. **添加异常处理**: 如果转换失败，记录警告但不影响主流程

## 测试结果

### 手动解析测试（`tests/test_manual_parse.py`）
```
测试1: 转发动态
✅ 检测到转发类型动态
   转发者: 星空future
   原作者: 糊涂的小炎陵
   原动态文本: 春随淑气融残雪...
   原动态图片数: 2
   转发评论: 好耶[咩栗呜米收藏集_大头]...

解析结果:
  作者: 糊涂的小炎陵
  文本: 春随淑气融残雪...
  图片数: 2
  转发评论: 好耶...
  转发者: 星空future
```

## 相关文件
- `src/nonebot_plugin_parser/parsers/bilibili/__init__.py` - 修复了 `parse_dynamic` 方法
- `src/nonebot_plugin_parser/parsers/bilibili/dynamic.py` - 数据结构定义
- `tests/test_manual_parse.py` - 手动解析测试脚本
- `tests/pipeline_output/test2_raw_api.json` - 转发动态原始数据
- `tests/pipeline_output/test2_manual_parse.json` - 手动解析结果

## 其他说明
- `DynamicData.orig` 字段保留在结构体中，但不再依赖 msgspec 自动转换
- 转发评论仍会添加到文本中，格式：`转发评论\n\n---\n\n原动态文本`
- 所有动态类型（OPUS、DRAW、ARCHIVE 等）的 orig 都能正确处理
