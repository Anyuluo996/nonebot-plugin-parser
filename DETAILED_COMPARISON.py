"""
详细对比：B站解析器关键差异
"""

def show_key_differences():
    print("="*70)
    print("B站解析器关键差异对比")
    print("="*70)

    print("\n## 原项目的改进 (upstream #444)")

    print("\n### 1. bilibili/__init__.py 改进:")
    print("-"*70)
    print("""
    ✅ 新增功能:
      - 新增 is_article() 检测，判断动态是否为文章类型
      - 统一 opus 和 dynamic 处理逻辑
      - 新增 @handle("/opus/", r"bilibili\.com/opus/(?P<dynamic_id>\d+)")
      - parse_bilibili_api_opus() 统一解析方法

    关键代码:
      if await dynamic.is_article():
          return await self._parse_bilibili_api_opus(dynamic.turn_to_opus())
    """)

    print("\n### 2. bilibili/dynamic.py 改进:")
    print("-"*70)
    print("""
    ✅ 新增字段:
      - DynamicMajor.type: str | None = None (放宽类型限制)
      - DynamicMajor.desc: OpusSummary | None = None (支持 desc 文本)

    ✅ 修改逻辑:
      - major_info 返回 module_dynamic (转发类型没有 major 时)

    关键代码:
      if major := self.module_dynamic.get("major"):
          return major
      # 转发类型动态没有 major
      return self.module_dynamic
    """)

    print("\n## 我们项目的修复")

    print("\n### 1. bilibili/__init__.py 修复:")
    print("-"*70)
    print("""
    ✅ 核心修复:
      - 区分 current_info (转发者) 和 content_source (原动态)
      - 转发动态的完整逻辑修复
      - 正确显示转发评论 + 原动态内容

    关键代码:
      current_info = dynamic_data.item
      content_source = current_info

      if is_forward:
          content_source = orig_info
          text = f"{forward_comment}\\n\\n---\\n\\n@{orig_info.name}：\\n{orig_text}"
    """)

    print("\n### 2. bilibili/dynamic.py 修复:")
    print("-"*70)
    print("""
    ✅ 新增结构体:
      - Draw 普通图文动态内容
      - DrawItem 图片项

    ✅ 容错处理:
      - DynamicInfo._major() 方法 (try-except)
      - 更多字段的默认值
      - 支持 MAJOR_TYPE_DRAW 类型

    关键代码:
      @property
      def _major(self) -> DynamicMajor | None:
          try:
              return convert(major_info, DynamicMajor)
          except Exception:
              return None  # 容错
    """)

    print("\n## 合并策略建议")

    print("\n### 推荐方案：保留双方优点")
    print("-"*70)
    print("""
    1. bilibili/__init__.py:
       ✅ 保留: 我们的转发动态逻辑
       ✅ 合入: 原项目的 is_article() 检测
       ✅ 合入: 原项目的 opus 处理改进
       ✅ 合入: 原项目的统一 parse_bilibili_api_opus() 方法

    2. bilibili/dynamic.py:
       ✅ 保留: 我们的 Draw 类型支持
       ✅ 保留: 我们的 _major() 容错方法
       ✅ 保留: 我们更多的默认值
       ✅ 合入: 原项目的 DynamicMajor.desc 字段
       ✅ 合入: 原项目的 major_info 返回逻辑改进
    """)

    print("\n## 具体冲突点")

    print("\n### 冲突点 1: DynamicMajor.text 属性")
    print("-"*70)
    print("""
    原项目:
      elif self.type == "MAJOR_TYPE_OPUS" and self.opus:
          return self.opus.summary.text
      elif self.desc:
          return self.desc.text  # 新增

    我们的项目:
      elif self.type == "MAJOR_TYPE_OPUS" and self.opus and self.opus.summary:
          return self.opus.summary.text  # 有额外的 None 检查

    建议: 采用原项目的逻辑，但保留 None 检查
    """)

    print("\n### 冲突点 2: DynamicModule.major_info")
    print("-"*70)
    print("""
    原项目:
      if major := self.module_dynamic.get("major"):
          return major
      # 转发类型动态没有 major
      return self.module_dynamic

    我们的项目:
      if self.module_dynamic:
          return self.module_dynamic.get("major")
      return None

    建议: 采用原项目的逻辑（处理转发类型）
    """)

    print("\n## 合并价值评估")

    print("\n### 原项目的价值:")
    print("-"*70)
    print("""
    ⭐ 高价值:
      - is_article() 检测 - 自动识别文章类型动态
      - 统一的 opus 处理 - 简化代码逻辑
      - desc 字段支持 - 处理某些特殊动态
      - 依赖更新 - urllib3, yt-dlp 等

    ⚠️  需要注意:
      - 可能与我们的修复冲突
      - 需要完整测试
    """)

    print("\n### 我们项目的价值:")
    print("-"*70)
    print("""
    ⭐ 高价值:
      - 转发动态完整修复 - 解决了实际问题
      - Draw 类型支持 - 支持更多动态类型
      - 容错处理 - 更健壮的解析
      - 详细字段默认值 - 兼容性更好

    ✅ 建议保留:
      - 所有我们的修复都应该保留
      - 原项目的改进可以在我们修复基础上应用
    """)

    print("\n" + "="*70)
    print("详细对比完成")
    print("="*70)


if __name__ == "__main__":
    show_key_differences()
