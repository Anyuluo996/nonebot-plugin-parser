"""
测试解析前缀功能 - 简化版

只测试核心逻辑，不依赖完整的 NoneBot 初始化
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))


def test_parse_prefix_property():
    """测试 parse_prefix 属性在 Config 类中"""
    print("\n" + "=" * 60)
    print("测试 1: 检查 parse_prefix 属性")
    print("=" * 60)

    # 直接读取文件检查
    config_file = project_root / "src" / "nonebot_plugin_parser" / "config.py"
    content = config_file.read_text(encoding="utf-8")

    # 检查是否添加了 parser_force_prefix 配置
    if "parser_force_prefix" in content:
        print("✓ Config 类中已添加 parser_force_prefix 配置")

        # 检查是否添加了 parse_prefix 属性
        if "def parse_prefix" in content:
            print("✓ Config 类中已添加 parse_prefix 属性")
            return True
        else:
            print("✗ Config 类中未找到 parse_prefix 属性")
            return False
    else:
        print("✗ Config 类中未找到 parser_force_prefix 配置")
        return False


def test_force_parse_key_constant():
    """测试强制解析状态键常量"""
    print("\n" + "=" * 60)
    print("测试 2: 检查 PSR_FORCE_PARSE_KEY 常量")
    print("=" * 60)

    rule_file = project_root / "src" / "nonebot_plugin_parser" / "matchers" / "rule.py"
    content = rule_file.read_text(encoding="utf-8")

    if 'PSR_FORCE_PARSE_KEY: Literal["psr-force-parse"] = "psr-force-parse"' in content:
        print('✓ PSR_FORCE_PARSE_KEY 常量已定义')
        return True
    else:
        print('✗ PSR_FORCE_PARSE_KEY 常量未定义或格式不正确')
        return False


def test_prefix_import():
    """测试 rule.py 是否导入 pconfig"""
    print("\n" + "=" * 60)
    print("测试 3: 检查 rule.py 导入 pconfig")
    print("=" * 60)

    rule_file = project_root / "src" / "nonebot_plugin_parser" / "matchers" / "rule.py"
    content = rule_file.read_text(encoding="utf-8")

    if "from ..config import gconfig, pconfig" in content:
        print("✓ rule.py 已导入 pconfig")
        return True
    else:
        print("✗ rule.py 未导入 pconfig")
        return False


def test_prefix_logic_in_rule():
    """测试 KeywordRegexRule 中的前缀处理逻辑"""
    print("\n" + "=" * 60)
    print("测试 4: 检查前缀处理逻辑")
    print("=" * 60)

    rule_file = project_root / "src" / "nonebot_plugin_parser" / "matchers" / "rule.py"
    content = rule_file.read_text(encoding="utf-8")

    checks = [
        ("检查 parse_prefix 变量", 'parse_prefix = pconfig.parse_prefix'),
        ("检查前缀匹配逻辑", 'if text.startswith(f"{parse_prefix}+") or text.startswith(f"{parse_prefix} ")'),
        ("检查强制解析标记", "state[PSR_FORCE_PARSE_KEY] = True"),
        ("检查前缀去除逻辑", 'text = text[len(f"{parse_prefix}+"):].lstrip()'),
    ]

    all_passed = True
    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ {check_name}")
        else:
            print(f"  ✗ {check_name}")
            all_passed = False

    return all_passed


def test_force_parse_import():
    """测试 __init__.py 是否导入 PSR_FORCE_PARSE_KEY"""
    print("\n" + "=" * 60)
    print("测试 5: 检查 __init__.py 导入")
    print("=" * 60)

    init_file = project_root / "src" / "nonebot_plugin_parser" / "matchers" / "__init__.py"
    content = init_file.read_text(encoding="utf-8")

    if "PSR_FORCE_PARSE_KEY" in content and "from .rule import" in content:
        print("✓ __init__.py 已导入 PSR_FORCE_PARSE_KEY")
        return True
    else:
        print("✗ __init__.py 未导入 PSR_FORCE_PARSE_KEY")
        return False


def test_force_parse_check():
    """测试 parser_handler 中的强制解析检查"""
    print("\n" + "=" * 60)
    print("测试 6: 检查强制解析逻辑")
    print("=" * 60)

    init_file = project_root / "src" / "nonebot_plugin_parser" / "matchers" / "__init__.py"
    content = init_file.read_text(encoding="utf-8")

    checks = [
        ("检查获取 force_parse 变量", "force_parse = state.get(PSR_FORCE_PARSE_KEY, False)"),
        ("检查条件判断", "if not force_parse and not platform_enabled"),
    ]

    all_passed = True
    for check_name, check_string in checks:
        if check_string in content:
            print(f"  ✓ {check_name}")
        else:
            print(f"  ✗ {check_name}")
            all_passed = False

    return all_passed


def test_prefix_logic_simulation():
    """模拟测试前缀解析逻辑"""
    print("\n" + "=" * 60)
    print("测试 7: 模拟前缀解析逻辑")
    print("=" * 60)

    # 模拟不同的 nickname
    test_nicknames = ["bot", "机器人", "解析助手"]

    test_cases = [
        # (输入文本, 昵称, 预期强制解析, 预期清理后文本前缀)
        ("https://bilibili.com/video/BV123", "bot", False, "https://bilibili.com/video/BV123"),
        ("bot+https://bilibili.com/video/BV123", "bot", True, "https://bilibili.com/video/BV123"),
        ("bot https://bilibili.com/video/BV123", "bot", True, "https://bilibili.com/video/BV123"),
        ("机器人+https://bilibili.com/video/BV123", "机器人", True, "https://bilibili.com/video/BV123"),
        ("机器人 https://bilibili.com/video/BV123", "机器人", True, "https://bilibili.com/video/BV123"),
    ]

    all_passed = True
    for original, prefix, should_force, expected_prefix in test_cases:
        # 模拟前缀去除逻辑
        text = original
        force_parse = False

        if text.startswith(f"{prefix}+") or text.startswith(f"{prefix} "):
            force_parse = True
            if text.startswith(f"{prefix}+"):
                text = text[len(f"{prefix}+"):].lstrip()
            else:
                text = text[len(f"{prefix} "):].lstrip()

        if force_parse != should_force:
            print(f"  ✗ 强制解析标志错误: '{original}' -> 期望 {should_force}, 得到 {force_parse}")
            all_passed = False
        elif not text.startswith(expected_prefix):
            print(f"  ✗ 文本清理错误: '{original}' -> 期望以 '{expected_prefix}' 开头, 得到 '{text}'")
            all_passed = False
        else:
            print(f"  ✓ '{original}' -> 强制={force_parse}, 文本='{text}'")

    return all_passed


def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("开始测试解析前缀功能")
    print("=" * 60)

    tests = [
        ("parse_prefix 属性", test_parse_prefix_property),
        ("PSR_FORCE_PARSE_KEY 常量", test_force_parse_key_constant),
        ("pconfig 导入", test_prefix_import),
        ("前缀处理逻辑", test_prefix_logic_in_rule),
        ("PSR_FORCE_PARSE_KEY 导入", test_force_parse_import),
        ("强制解析检查", test_force_parse_check),
        ("前缀解析模拟", test_prefix_logic_simulation),
    ]

    failed_tests = []

    for name, test_func in tests:
        try:
            result = test_func()
            if not result:
                failed_tests.append(name)
        except Exception as e:
            print(f"✗ {name} 测试异常: {e}")
            failed_tests.append(name)

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    if failed_tests:
        print(f"❌ 共有 {len(failed_tests)} 个测试失败:")
        for name in failed_tests:
            print(f"  - {name}")
        return False
    else:
        print(f"✅ 所有 {len(tests)} 个测试通过!")
        return True


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
