"""
测试命令参数解析 - 使用 pytest
"""

import pytest


class TestCommandArgParsing:
    """测试 NoneBot CommandArg 参数解析"""

    def test_extract_plain_text_simple(self):
        """测试简单的参数提取"""
        # 模拟 extract_plain_text 返回值
        # 实际上 NoneBot 会从消息中提取命令后面的内容

        # 当用户发送 "@机器人 关闭解析 bilibili" 时
        # CommandArg.extract_plain_text() 应该返回 "bilibili"
        text = "bilibili"
        result = text.strip()
        assert result == "bilibili"
        assert bool(result) is True

    def test_extract_plain_text_with_spaces(self):
        """测试带空格的参数"""
        text = " bilibili "
        result = text.strip()
        assert result == "bilibili"

    def test_extract_plain_text_empty(self):
        """测试空参数"""
        text = ""
        result = text.strip()
        assert result == ""
        # 空字符串在 if 判断中为 False
        if result:
            pytest.fail("空字符串应该为 False")

    def test_platform_name_truthiness(self):
        """测试平台名称的真值判断"""
        # 测试各种平台名称
        platform_names = ["bilibili", "b站", "B站", "weibo", "微博", "douyin"]

        for name in platform_names:
            # 非空字符串应该为 True
            assert bool(name.strip()) is True, f"'{name}' 应该为 True"

        # 空字符串应该为 False
        assert bool("".strip()) is False
        assert bool("   ".strip()) is False

    def test_english_platform_name(self):
        """测试英文平台名"""
        name = "bilibili"
        result = name.lower().strip()
        assert result == "bilibili"

    def test_chinese_platform_name(self):
        """测试中文平台名"""
        name = "B站"
        # 注意：中文大小写不敏感只是针对英文部分
        result = name.strip()
        assert result == "B站"

    def test_mixed_case_platform_name(self):
        """测试大小写混合"""
        name = "BILIBILI"
        result = name.lower().strip()
        assert result == "bilibili"


class TestPlatformNameMapping:
    """测试平台名称映射"""

    def test_bilibili_aliases(self):
        """测试 B站 别名"""
        from nonebot_plugin_parser.matchers.filter import get_platform_display_name

        assert get_platform_display_name("bilibili") == "bilibili"
        assert get_platform_display_name("B站") == "bilibili"
        assert get_platform_display_name("b站") == "bilibili"
        assert get_platform_display_name("BILIBILI") == "bilibili"

    def test_douyin_aliases(self):
        """测试抖音别名"""
        from nonebot_plugin_parser.matchers.filter import get_platform_display_name

        assert get_platform_display_name("douyin") == "douyin"
        assert get_platform_display_name("抖音") == "douyin"

    def test_weibo_aliases(self):
        """测试微博别名"""
        from nonebot_plugin_parser.matchers.filter import get_platform_display_name

        assert get_platform_display_name("weibo") == "weibo"
        assert get_platform_display_name("微博") == "weibo"


class TestDisablePlatformLogic:
    """测试禁用平台逻辑"""

    def setup_method(self):
        """每个测试前清空数据"""
        from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT
        _DISABLED_PLATFORMS_DICT.clear()

    def teardown_method(self):
        """每个测试后清空数据"""
        from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT
        _DISABLED_PLATFORMS_DICT.clear()

    def test_disable_bilibili(self):
        """测试禁用 bilibili"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            get_platform_display_name,
            check_platform_available,
            is_platform_enabled,
            _DISABLED_PLATFORMS_DICT,
        )

        # 模拟会话
        class MockSession:
            def __init__(self):
                self.scope = "QQ"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)

        # 模拟命令参数 "bilibili"
        platform_name = "bilibili"
        standard_name = get_platform_display_name(platform_name)

        assert standard_name == "bilibili"
        assert check_platform_available(standard_name) is True

        # 执行禁用逻辑（与 disable_parser 相同）
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)

        # 验证
        assert is_platform_enabled(session, "bilibili") is False
        assert "bilibili" in _DISABLED_PLATFORMS_DICT[group_key]

    def test_disable_with_chinese_alias(self):
        """测试使用中文别名禁用"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            get_platform_display_name,
            is_platform_enabled,
            _DISABLED_PLATFORMS_DICT,
        )

        class MockSession:
            def __init__(self):
                self.scope = "QQ"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)

        # 使用中文别名
        platform_name = "B站"
        standard_name = get_platform_display_name(platform_name)

        assert standard_name == "bilibili"

        # 执行禁用
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)

        # 验证
        assert is_platform_enabled(session, "bilibili") is False
