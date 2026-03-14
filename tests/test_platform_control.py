"""
测试开启/关闭解析功能
"""

import inspect

import pytest


def get_mock_session():
    """获取 MockSession 类，延迟导入避免 pytest 收集阶段报错"""
    from nonebot_plugin_parser.matchers.filter import _DISABLED_PLATFORMS_DICT, get_group_key, is_platform_enabled

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


class MockTextMessage:
    """模拟纯文本消息"""

    def __init__(self, text: str):
        self.text = text

    def extract_plain_text(self) -> str:
        return self.text


class MockCommandArg:
    """模拟命令参数"""

    def __init__(self, text: str):
        self.text = text

    def extract_plain_text(self) -> str:
        return self.text


class MockMatcher:
    """模拟 matcher.finish 行为"""

    def __init__(self):
        self.finished_messages: list[str] = []

    async def finish(self, message: str):
        from nonebot.exception import FinishedException

        self.finished_messages.append(message)
        raise FinishedException()


def build_group_message_event(raw_message: str, *, self_id: int = 1660188286, user_id: int = 1247572395, group_id: int = 881042075):
    """构造用于 matcher 测试的 OneBot 群消息事件"""
    from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
    from nonebot.adapters.onebot.v11.event import Sender

    message = Message([
        MessageSegment.at(self_id),
        MessageSegment.text(f" {raw_message}"),
    ])
    return GroupMessageEvent(
        time=0,
        self_id=self_id,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=message,
        original_message=message,
        raw_message=f"[CQ:at,qq={self_id}] {raw_message}",
        font=0,
        sender=Sender(user_id=user_id, role="admin"),
        to_me=True,
        group_id=group_id,
    )


def get_platform_control_matchers():
    """获取平台控制命令 matcher"""
    from nonebot.matcher import matchers

    result = {}
    for matcher_group in matchers.values():
        for matcher in matcher_group:
            if getattr(matcher, "module_name", None) != "nonebot_plugin_parser.matchers.filter":
                continue
            handler_name = matcher.handlers[0].call.__name__
            if handler_name in {"enable_parser", "disable_parser", "parser_status"}:
                result[handler_name] = matcher
    return result


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

    def test_is_enabled_when_some_platforms_still_enabled(self):
        """测试仅禁用部分平台时自动解析仍可用"""
        from nonebot_plugin_parser.matchers.filter import is_enabled

        MockSession, get_group_key, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        session = MockSession("QQ", "group_123", is_private=False)
        _DISABLED_PLATFORMS_DICT[get_group_key(session)] = {"bilibili"}

        assert is_enabled(MockTextMessage("https://example.com"), session) is True

    def test_is_enabled_false_when_all_platforms_disabled(self):
        """测试仅在所有平台都禁用时自动解析才关闭"""
        from nonebot_plugin_parser.constants import PlatformEnum
        from nonebot_plugin_parser.matchers.filter import is_enabled

        MockSession, get_group_key, _, _DISABLED_PLATFORMS_DICT = get_mock_session()
        session = MockSession("QQ", "group_123", is_private=False)
        _DISABLED_PLATFORMS_DICT[get_group_key(session)] = {platform.value for platform in PlatformEnum}

        assert is_enabled(MockTextMessage("https://example.com"), session) is False

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
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            check_platform_available,
            get_platform_display_name,
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

        _DISABLED_PLATFORMS_DICT[group_key] = {"bilibili"}

        # 模拟命令处理逻辑
        if group_key not in _DISABLED_PLATFORMS_DICT:
            _DISABLED_PLATFORMS_DICT[group_key] = set()
        _DISABLED_PLATFORMS_DICT[group_key].discard(standard_name)
        if not _DISABLED_PLATFORMS_DICT[group_key]:
            del _DISABLED_PLATFORMS_DICT[group_key]

        # 验证结果
        assert is_platform_enabled(session, "bilibili") is True
        assert group_key not in _DISABLED_PLATFORMS_DICT

    def test_platform_control_handlers_use_message_command_arg_signature(self):
        """测试平台控制 handler 使用可注入的 Message 参数声明"""
        from nonebot.adapters import Message
        from nonebot.params import CommandArg
        import nonebot_plugin_parser.matchers.filter as filter_module

        enable_args = inspect.signature(filter_module.enable_parser).parameters["args"]
        disable_args = inspect.signature(filter_module.disable_parser).parameters["args"]
        expected_default_type = type(CommandArg())

        assert enable_args.annotation is Message
        assert disable_args.annotation is Message
        assert type(enable_args.default) is expected_default_type
        assert type(disable_args.default) is expected_default_type

    @pytest.mark.asyncio
    async def test_enable_parser_handler_removes_empty_group_key(self, monkeypatch):
        """测试开启最后一个被禁用平台时会清理残留状态"""
        import nonebot_plugin_parser.matchers.filter as filter_module
        from nonebot.exception import FinishedException

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = filter_module.get_group_key(session)
        filter_module._DISABLED_PLATFORMS_DICT[group_key] = {"bilibili"}
        monkeypatch.setattr(filter_module, "save_disabled_platforms", lambda: None)

        matcher = MockMatcher()
        with pytest.raises(FinishedException):
            await filter_module.enable_parser(matcher, session, MockCommandArg("bilibili"))

        assert matcher.finished_messages == ["bilibili 解析已开启"]
        assert group_key not in filter_module._DISABLED_PLATFORMS_DICT

    @pytest.mark.asyncio
    async def test_disable_parser_handler_accepts_real_message_command_arg(self, monkeypatch):
        """测试 disable_parser 可直接处理真实 OneBot Message 参数"""
        from nonebot.adapters.onebot.v11 import Message
        from nonebot.exception import FinishedException
        import nonebot_plugin_parser.matchers.filter as filter_module

        MockSession, _, _, _ = get_mock_session()
        session = MockSession("QQ", "group_test", is_private=False)
        group_key = filter_module.get_group_key(session)
        filter_module._DISABLED_PLATFORMS_DICT.clear()
        monkeypatch.setattr(filter_module, "save_disabled_platforms", lambda: None)

        matcher = MockMatcher()
        with pytest.raises(FinishedException):
            await filter_module.disable_parser(matcher, session, Message("b站"))

        assert matcher.finished_messages == ["b站 解析已关闭"]
        assert filter_module._DISABLED_PLATFORMS_DICT[group_key] == {"bilibili"}

    def test_disable_single_platform_logic(self):
        """测试关闭单个平台的逻辑"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            check_platform_available,
            get_platform_display_name,
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
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
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
        if not _DISABLED_PLATFORMS_DICT[group_key]:
            del _DISABLED_PLATFORMS_DICT[group_key]

        # 验证
        assert is_platform_enabled(session, "bilibili") is True
        assert group_key not in _DISABLED_PLATFORMS_DICT

    def test_disable_platform_with_chinese_alias(self):
        """测试使用中文别名关闭平台"""
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
            get_platform_display_name,
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
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
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
        from nonebot_plugin_parser.constants import PlatformEnum
        from nonebot_plugin_parser.matchers.filter import (
            _DISABLED_PLATFORMS_DICT,
            get_group_key,
            is_platform_enabled,
        )

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

    def test_platform_control_matchers_require_to_me_and_admin_owner_permissions(self):
        """测试平台控制命令 matcher 的权限和 to_me 规则"""
        matchers = get_platform_control_matchers()

        assert set(matchers) == {"enable_parser", "disable_parser", "parser_status"}

        for matcher in matchers.values():
            permission_reprs = {repr(checker) for checker in matcher.permission.checkers}
            rule_reprs = {repr(checker) for checker in matcher.rule.checkers}

            assert len(matcher.permission.checkers) == 3
            assert any("Superuser" in checker for checker in permission_reprs)

            assert len(matcher.rule.checkers) == 2
            assert any("Command" in checker for checker in rule_reprs)
            assert any("ToMe" in checker for checker in rule_reprs)
