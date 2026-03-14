"""
测试开启/关闭解析功能
"""

import pytest


def get_mock_session():
    """获取 MockSession 类，延迟导入避免 pytest 收集阶段报错"""
    from nonebot_plugin_parser.matchers.filter import get_group_key, is_platform_enabled, _DISABLED_PLATFORMS_DICT

    class MockSession:
        """模拟 Session 对象"""

        def __init__(self, scope: str, scene_path: str, is_private: bool = False):
            self.scope = scope
            self.scene_path = scene_path
            self._is_private = is_private

        @property
        def scene(self):
            return type("MockScene", (), {"is_private": self._is_private})()

    return MockSession, get_group_key, is_platform_enabled, _DISABLED_PLATFORMS_DICT


class TestPlatformNameMapping:
    """测试平台名称映射"""

    def test_get_platform_display_name(self):
        """测试平台名称转换为标准值"""
        from nonebot_plugin_parser.matchers.filter import get_platform_display_name

        # 测试 value
        assert get_platform_display_name("bilibili") == "bilibili"
        assert get_platform_display_name("weibo") == "weibo"
        assert get_platform_display_name("douyin") == "douyin"

        # 测试大小写不敏感
        assert get_platform_display_name("BILIBILI") == "bilibili"
        assert get_platform_display_name("Weibo") == "weibo"

        # 测试未知平台
        assert get_platform_display_name("unknown_platform") is None

    def test_check_platform_available(self):
        """测试平台是否可用"""
        from nonebot_plugin_parser.matchers.filter import check_platform_available

        # 测试已实现的平台
        assert check_platform_available("bilibili") is True
        assert check_platform_available("weibo") is True
        assert check_platform_available("douyin") is True

        # 测试未实现的平台
        assert check_platform_available("unknown_platform") is False


class TestGroupKey:
    """测试群组 key 获取"""

    def test_get_group_key_private(self):
        """测试私聊场景的 group key"""
        MockSession, get_group_key, _, _ = get_mock_session()
        session = MockSession("QQ", "private", is_private=True)
        key = get_group_key(session)
        assert key == "QQ_private"

    def test_get_group_key_group(self):
        """测试群聊场景的 group key"""
        MockSession, get_group_key, _, _ = get_mock_session()
        session = MockSession("QQ", "group_123456", is_private=False)
        key = get_group_key(session)
        assert key == "QQ_group_123456"


class TestPlatformEnabled:
    """测试平台启用状态"""

    def setup_method(self):
        """每个测试前清空数据"""
        _, _, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        _DISABLED_PLATFORMS_DICT.clear()

    def teardown_method(self):
        """每个测试后清理数据"""
        _, _, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        _DISABLED_PLATFORMS_DICT.clear()

    def test_private_session_always_enabled(self):
        """测试私聊始终启用"""
        MockSession, _, is_platform_enabled, _ = get_mock_session()
        session = MockSession("QQ", "private", is_private=True)

        assert is_platform_enabled(session, "bilibili") is True
        assert is_platform_enabled(session, "weibo") is True

    def test_no_disabled_all_enabled(self):
        """测试没有禁用时所有平台启用"""
        MockSession, _, is_platform_enabled, _ = get_mock_session()
        session = MockSession("QQ", "group_123", is_private=False)

        assert is_platform_enabled(session, "bilibili") is True
        assert is_platform_enabled(session, "weibo") is True

    def test_disable_single_platform(self):
        """测试禁用单个平台"""
        MockSession, _, is_platform_enabled, _DISABLED_PLATFORMS_DICT = get_mock_session()
        session = MockSession("QQ", "group_123", is_private=False)

        # 禁用 bilibili
        _DISABLED_PLATFORMS_DICT["QQ_group_123"] = {"bilibili"}

        assert is_platform_enabled(session, "bilibili") is False
        assert is_platform_enabled(session, "weibo") is True

    def test_disable_multiple_platforms(self):
        """测试禁用多个平台"""
        MockSession, _, is_platform_enabled, _DISABLED_PLATFORMS_DICT = get_mock_session()
        session = MockSession("QQ", "group_123", is_private=False)

        # 禁用多个平台
        _DISABLED_PLATFORMS_DICT["QQ_group_123"] = {"bilibili", "weibo"}

        assert is_platform_enabled(session, "bilibili") is False
        assert is_platform_enabled(session, "weibo") is False
        assert is_platform_enabled(session, "douyin") is True

    def test_different_groups_independent(self):
        """测试不同群组独立管理"""
        MockSession, _, is_platform_enabled, _DISABLED_PLATFORMS_DICT = get_mock_session()
        session1 = MockSession("QQ", "group_111", is_private=False)
        session2 = MockSession("QQ", "group_222", is_private=False)

        # 禁用 group_111 的 bilibili
        _DISABLED_PLATFORMS_DICT["QQ_group_111"] = {"bilibili"}

        # group_222 不受影响
        assert is_platform_enabled(session1, "bilibili") is False
        assert is_platform_enabled(session2, "bilibili") is True


class TestPlatformEnum:
    """测试平台枚举"""

    def test_all_platforms(self):
        """测试所有平台枚举值"""
        from nonebot_plugin_parser.constants import PlatformEnum

        expected = {
            "acfun",
            "bilibili",
            "douyin",
            "kuaishou",
            "nga",
            "tiktok",
            "twitter",
            "weibo",
            "xiaohongshu",
            "youtube",
        }

        actual = {p.value for p in PlatformEnum}
        assert actual == expected

    def test_platform_enum_str(self):
        """测试平台枚举字符串化"""
        from nonebot_plugin_parser.constants import PlatformEnum

        assert str(PlatformEnum.BILIBILI) == "bilibili"
        assert str(PlatformEnum.WEIBO) == "weibo"


class TestPlatformCommandHandler:
    """测试平台控制命令处理逻辑"""

    def setup_method(self):
        """每个测试前清空数据"""
        _, _, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        _DISABLED_PLATFORMS_DICT.clear()

    def teardown_method(self):
        """每个测试后清理数据"""
        _, _, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        _DISABLED_PLATFORMS_DICT.clear()

    def test_enable_single_platform_logic(self):
        """测试开启单个平台的逻辑"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
            check_platform_available,
            _DISABLED_PLATFORMS_DICT,
        )

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 模拟命令参数：开启解析 bilibili
        platform_name = "bilibili"

        # 验证参数解析
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "bilibili"
        assert check_platform_available(standard_name) is True

        # 模拟命令处理逻辑
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].discard(standard_name)

        # 验证结果
        assert is_platform_enabled(session, "bilibili") is True
        assert group_key in _DISABLED_PLATFORMS_DICT
        assert "bilibili" not in _DISABLED_PLATFORMS_DICT[group_key]

    def test_disable_single_platform_logic(self):
        """测试关闭单个平台的逻辑"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
            check_platform_available,
            _DISABLED_PLATFORMS_DICT,
        )

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 模拟命令参数：关闭解析 bilibili
        platform_name = "bilibili"

        # 验证参数解析
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "bilibili"
        assert check_platform_available(standard_name) is True

        # 模拟命令处理逻辑
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)

        # 验证结果
        assert is_platform_enabled(session, "bilibili") is False

    def test_enable_platform_with_chinese_alias(self):
        """测试使用中文别名开启平台"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
            _DISABLED_PLATFORMS_DICT,
        )

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 先禁用
        _DISABLED_PLATFORMS_DICT[group_key] = {"bilibili"}
        assert is_platform_enabled(session, "bilibili") is False

        # 使用中文别名开启
        platform_name = "B站"  # 中文别名
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "bilibili"

        # 模拟开启命令
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].discard(standard_name)

        # 验证
        assert is_platform_enabled(session, "bilibili") is True

    def test_disable_platform_with_chinese_alias(self):
        """测试使用中文别名关闭平台"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
            _DISABLED_PLATFORMS_DICT,
        )

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 使用中文别名关闭
        platform_name = "抖音"  # 中文别名
        standard_name = get_platform_display_name(platform_name)
        assert standard_name == "douyin"

        # 模拟关闭命令
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].add(standard_name)

        # 验证
        assert is_platform_enabled(session, "douyin") is False
        assert is_platform_enabled(session, "bilibili") is True

    def test_enable_all_platforms_logic(self):
        """测试开启所有平台的逻辑"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            _DISABLED_PLATFORMS_DICT,
        )

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 先禁用所有平台
        _DISABLED_PLATFORMS_DICT[group_key] = {"bilibili", "weibo", "douyin"}
        assert is_platform_enabled(session, "bilibili") is False
        assert is_platform_enabled(session, "weibo") is False
        assert is_platform_enabled(session, "douyin") is False

        # 模拟开启所有平台命令（无参数）
        if group_key in _DISABLED_PLATFORMS_DICT:
            del _DISABLED_PLATFORMS_DICT[group_key]

        # 验证
        assert is_platform_enabled(session, "bilibili") is True
        assert is_platform_enabled(session, "weibo") is True
        assert is_platform_enabled(session, "douyin") is True

    def test_disable_all_platforms_logic(self):
        """测试关闭所有平台的逻辑"""
        from nonebot_plugin_parser.matchers.filter import (
            get_group_key,
            is_platform_enabled,
            _DISABLED_PLATFORMS_DICT,
        )
        from nonebot_plugin_parser.constants import PlatformEnum

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = get_group_key(session)

        # 模拟关闭所有平台命令（无参数）
        all_platforms = {p.value for p in PlatformEnum}
        _DISABLED_PLATFORMS_DICT[group_key] = all_platforms

        # 验证所有平台都被禁用
        for platform in PlatformEnum:
            assert is_platform_enabled(session, platform.value) is False

    def test_unknown_platform_returns_error(self):
        """测试未知平台返回错误"""
        from nonebot_plugin_parser.matchers.filter import (
            get_platform_display_name,
            check_platform_available,
        )

        # 测试未知平台
        platform_name = "unknown_platform"
        standard_name = get_platform_display_name(platform_name)
        assert standard_name is None

    def test_platform_not_available_returns_error(self):
        """测试不可用平台返回错误"""
        from nonebot_plugin_parser.matchers.filter import check_platform_available

        # 测试存在的平台
        assert check_platform_available("bilibili") is True

        # 测试不存在的平台
        assert check_platform_available("not_exist") is False
