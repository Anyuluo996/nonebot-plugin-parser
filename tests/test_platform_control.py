"""
测试单独平台控制功能

这个脚本演示了新增的平台控制功能：
1. 开启/关闭指定平台的解析
2. 查看当前群组的解析状态
3. 支持的平台别名
"""

from pathlib import Path
import json


# 演示平台名称映射功能
def demo_platform_name_mapping():
    """演示平台名称识别（模拟）"""
    print("=" * 60)
    print("演示 1: 平台名称识别")
    print("=" * 60)

    test_cases = [
        ("bilibili", "bilibili"),
        ("b站", "bilibili"),
        ("B站", "bilibili"),
        ("douyin", "douyin"),
        ("抖音", "douyin"),
        ("weibo", "weibo"),
        ("微博", "weibo"),
        ("twitter", "twitter"),
        ("推特", "twitter"),
        ("youtube", "youtube"),
        ("油管", "youtube"),
    ]

    for input_name, expected in test_cases:
        status = "✅"
        print(f"{status} '{input_name}' -> '{expected}'")


# 演示 JSON 数据结构
def demo_json_structure():
    """演示 JSON 存储结构"""
    print("\n" + "=" * 60)
    print("演示 2: JSON 数据结构")
    print("=" * 60)

    # 新格式
    new_format = {
        "test_group_123": ["bilibili", "douyin"],
        "test_group_456": ["weibo", "twitter"]
    }

    print("\n新格式 (disabled_platforms.json):")
    print(json.dumps(new_format, ensure_ascii=False, indent=2))

    print("\n说明:")
    print("  - 群组 test_group_123 禁用了 bilibili 和 douyin")
    print("  - 群组 test_group_456 禁用了 weibo 和 twitter")
    print("  - 未列出的平台默认启用")


# 演示指令用法
def demo_command_usage():
    """演示指令用法"""
    print("\n" + "=" * 60)
    print("演示 3: 指令使用方法")
    print("=" * 60)

    commands = [
        ("开启解析", "开启所有平台的解析功能"),
        ("开启解析 bilibili", "开启 B 站的解析功能"),
        ("开启解析 b站", "同上，支持中文别名"),
        ("关闭解析", "关闭所有平台的解析功能"),
        ("关闭解析 douyin", "关闭抖音的解析功能"),
        ("关闭解析 抖音", "同上，支持中文别名"),
        ("解析状态", "查看当前群组的解析状态"),
    ]

    for cmd, desc in commands:
        print(f"  {cmd:30s} # {desc}")


# 演示状态输出
def demo_status_output():
    """演示状态输出格式"""
    print("\n" + "=" * 60)
    print("演示 4: 状态输出示例")
    print("=" * 60)

    status_output = """当前群组解析状态:
  ✅ 启用 - AcFun (acfun)
  ❌ 禁用 - B站 (bilibili)
  ✅ 启用 - 抖音 (douyin)
  ✅ 启用 - 快手 (kuaishou)
  ✅ 启用 - NGA (nga)
  ✅ 启用 - TikTok (tiktok)
  ❌ 禁用 - Twitter (twitter)
  ✅ 启用 - 微博 (weibo)
  ✅ 启用 - 小红书 (xiaohongshu)
  ✅ 启用 - YouTube (youtube)

总计: 9/11 个平台已启用"""

    print(status_output)


# 演示迁移逻辑
def demo_migration():
    """演示数据迁移逻辑"""
    print("\n" + "=" * 60)
    print("演示 5: 旧版本数据迁移")
    print("=" * 60)

    print("\n旧格式 (disabled_groups.json):")
    old_format = ["test_group_123", "test_group_456"]
    print(json.dumps(old_format, ensure_ascii=False, indent=2))

    print("\n迁移后:")
    print("  - 将禁用群组标记为禁用所有平台")
    print("  - 自动迁移，无需手动操作")
    print("  - 迁移后自动删除旧文件")

    print("\n迁移后格式:")
    new_format = {
        "test_group_123": ["acfun", "bilibili", "douyin", "kuaishou", "nga", "tiktok", "twitter", "weibo", "xiaohongshu", "youtube"],
        "test_group_456": ["acfun", "bilibili", "douyin", "kuaishou", "nga", "tiktok", "twitter", "weibo", "xiaohongshu", "youtube"]
    }
    print(json.dumps(new_format, ensure_ascii=False, indent=2))


def main():
    """主函数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "单独平台控制功能演示" + " " * 15 + "║")
    print("╚" + "=" * 58 + "╝")

    demo_platform_name_mapping()
    demo_json_structure()
    demo_command_usage()
    demo_status_output()
    demo_migration()

    print("\n" + "=" * 60)
    print("功能特点")
    print("=" * 60)
    print("""
✅ 支持单独控制每个平台的解析开关
✅ 支持中英文平台名称别名
✅ 支持查看当前群组的解析状态
✅ 自动迁移旧版本数据
✅ 私聊默认启用所有平台
✅ 数据持久化存储
""")

    print("=" * 60)
    print("支持的平台列表")
    print("=" * 60)

    platforms = [
        ("acfun", "A站"),
        ("bilibili", "B站"),
        ("douyin", "抖音"),
        ("kuaishou", "快手"),
        ("nga", "NGA"),
        ("tiktok", "TikTok"),
        ("twitter", "Twitter"),
        ("weibo", "微博"),
        ("xiaohongshu", "小红书"),
        ("youtube", "YouTube"),
    ]

    for value, display in platforms:
        print(f"  {value:15s} - {display}")

    print("\n")


if __name__ == "__main__":
    main()
