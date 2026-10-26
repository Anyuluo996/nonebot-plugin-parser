from nonebot import logger


def test_font():
    from nonebot_plugin_parser.renders import CommonRenderer

    # 字体/logo 等类资源由 load_resources() 惰性加载（启动钩子或首次渲染时），
    # 直接读类属性会依赖「前面某个用例渲染过」的执行顺序 —— 单独跑或 -n 分发到
    # 新 worker 时会 AttributeError。这里显式加载（幂等）。
    CommonRenderer.load_resources()

    font = CommonRenderer.fontset.text
    chars = ["中", "A", "1", "a", ",", "。"]
    for char in chars:
        logger.info(f"{char}: {font.get_char_width(char)}")
    for char in range(128):
        char = chr(char)
        logger.info(f"{char}: {font.get_char_width(char)}")


def test_cjk_width():
    from nonebot_plugin_parser.renders import CommonRenderer

    CommonRenderer.load_resources()  # 依赖类资源, 见 test_font 注释

    font = CommonRenderer.fontset.name
    count = 0
    for char_ord in range(ord("\u4e00"), ord("\u9fff")):
        char = chr(char_ord)
        width = font.get_char_width(char)
        if width != font.cjk_width:
            # logger.warning(f"{char}({char_ord}): {width} != {font.cjk_width}")
            count += 1
    cjk_count = ord("\u9fff") - ord("\u4e00") + 1
    logger.info(f"CJK 字符数: {cjk_count}，不等于 CJK 宽度的字符数: {count}，占比: {count / cjk_count:.2%}")
