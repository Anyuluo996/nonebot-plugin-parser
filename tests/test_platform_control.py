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
