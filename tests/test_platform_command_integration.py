"""
测试平台控制命令 - 完整测试
"""

import pytest


class TestCommandHandlerIntegration:
    """测试命令处理器集成"""

    def setup_method(self):
        """每个测试前清空数据"""
        from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT
        _DISABLED_PLATFORMS_DICT.clear()

    def teardown_method(self):
        """每个测试后清空数据"""
        from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT
        _DISABLED_PLATFORMS_DICT.clear()

    def test_disable_single_platform_english(self):
        """测试禁用单个平台 - 英文"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            save_disabled_platforms,
            check_platform_available,
            get_platform_display_name,
        )

        # 模拟 session
        class MockSession:
            def __init__(self):
                self.scope = "QQClient"
                self.scene_path = "881042075"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)
        platform_name = "bilibili"

        # 模拟命令处理逻辑
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "bilibili"
        assert check_platform_available(standard_name) is True

        # 禁用
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)
        save_disabled_platforms()

        # 验证
        assert is_platform_enabled(session, "bilibili") is False
        assert "bilibili" in _DISABLED_PLATFORMS_DICT[group_key]

    def test_disable_single_platform_chinese(self):
        """测试禁用单个平台 - 中文"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            save_disabled_platforms,
            get_platform_display_name,
        )

        class MockSession:
            def __init__(self):
                self.scope = "QQClient"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)
        platform_name = "B站"

        # 模拟命令处理
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "bilibili"

        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)
        save_disabled_platforms()

        # 验证
        assert is_platform_enabled(session, "bilibili") is False

    def test_enable_single_platform(self):
        """测试开启单个平台"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            save_disabled_platforms,
        )

        class MockSession:
            def __init__(self):
                self.scope = "QQClient"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)

        # 先禁用
        _DISABLED_PLATFORMS_DICT[group_key] = {"bilibili"}
        assert is_platform_enabled(session, "bilibili") is False

        # 开启
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].discard("bilibili")
        save_disabled_platforms()

        # 验证
        assert is_platform_enabled(session, "bilibili") is True

    def test_disable_all_platforms(self):
        """测试禁用所有平台"""
        from nonebot_plugin_parser.constants import PlatformEnum
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            save_disabled_platforms,
        )

        class MockSession:
            def __init__(self):
                self.scope = "QQClient"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)

        # 禁用所有平台
        all_platforms = {p.value for p in PlatformEnum}
        _DISABLED_PLATFORMS_DICT[group_key] = all_platforms
        save_disabled_platforms()

        # 验证
        for platform in PlatformEnum:
            assert is_platform_enabled(session, platform.value) is False

    def test_enable_all_platforms(self):
        """测试开启所有平台"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
        )

        class MockSession:
            def __init__(self):
                self.scope = "QQClient"
                self.scene_path = "123456"

            @property
            def scene(self):
                class MockScene:
                    is_private = False
                return MockScene()

        session = MockSession()
        group_key = get_group_key(session)

        # 先禁用所有平台
        _DISABLED_PLATFORMS_DICT[group_key] = {"bilibili", "weibo"}
        assert is_platform_enabled(session, "bilibili") is False

        # 开启所有（删除 key）
        if group_key in _DISABLED_PLATFORMS_DICT:
            del _DISABLED_PLATFORMS_DICT[group_key]

        # 验证
        assert is_platform_enabled(session, "bilibili") is True
        assert is_platform_enabled(session, "weibo") is True


class TestPlatformNameMappingComplete:
    """测试平台名称映射 - 完整"""

    def test_all_supported_aliases(self):
        """测试所有支持的别名"""
        from nonebot_plugin_parser.matchers.filter import get_platform_display_name

        # 英文名
        assert get_platform_display_name("bilibili") == "bilibili"
        assert get_platform_display_name("weibo") == "weibo"
        assert get_platform_display_name("douyin") == "douyin"
        assert get_platform_display_name("twitter") == "twitter"
        assert get_platform_display_name("youtube") == "youtube"
        assert get_platform_display_name("kuaishou") == "kuaishou"
        assert get_platform_display_name("xiaohongshu") == "xiaohongshu"
        assert get_platform_display_name("acfun") == "acfun"
        assert get_platform_display_name("nga") == "nga"

        # 中文别名
        assert get_platform_display_name("B站") == "bilibili"
        assert get_platform_display_name("b站") == "bilibili"
        assert get_platform_display_name("微博") == "weibo"
        assert get_platform_display_name("抖音") == "douyin"
        assert get_platform_display_name("推特") == "twitter"
        assert get_platform_display_name("油管") == "youtube"
        assert get_platform_display_name("快手") == "kuaishou"
        assert get_platform_display_name("小红书") == "xiaohongshu"
        assert get_platform_display_name("xhs") == "xiaohongshu"
        assert get_platform_display_name("A站") == "acfun"

        # 大小写
        assert get_platform_display_name("BILIBILI") == "bilibili"
        assert get_platform_display_name("WEIBO") == "weibo"

        # 未知平台
        assert get_platform_display_name("unknown") is None
        assert get_platform_display_name("不存在的平台") is None


class TestCommandArgParsing:
    """测试命令参数解析"""

    def test_platform_name_truthiness(self):
        """测试平台名称真值判断"""
        # 非空字符串
        assert bool("bilibili") is True
        assert bool("B站") is True
        assert bool("  bilibili  ") is True

        # 空字符串
        assert bool("") is False

        # strip 后的空字符串
        assert bool("   ".strip()) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
