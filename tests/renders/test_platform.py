def test_platform_enum():
    from nonebot_plugin_parser.renders import CommonRenderer
    from nonebot_plugin_parser.constants import PlatformEnum

    # 平台 logo 是 load_resources() 加载的类属性（见 test_font 注释）
    CommonRenderer.load_resources()

    assert PlatformEnum.BILIBILI == "bilibili"
    assert str(PlatformEnum.BILIBILI) == "bilibili"
    assert CommonRenderer.platform_logos[PlatformEnum.BILIBILI] is not None
    assert CommonRenderer.platform_logos[str(PlatformEnum.BILIBILI)] is not None
