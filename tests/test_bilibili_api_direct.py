"""
测试 bilibili_api 库直接调用
"""

import asyncio


async def test_opus_with_credential():
    """测试 Opus 接口（带和不带 credential）"""

    print("\n" + "╔" + "="*68 + "╗")
    print("║" + " "*20 + "测试 bilibili_api.opus.Opus" + " "*20 + "║")
    print("╚" + "="*68 + "╝")

    # 测试1: 无 credential
    print(f"\n{'='*70}")
    print("测试1: Opus 无 credential")
    print(f"{'='*70}\n")

    try:
        from bilibili_api.opus import Opus

        opus_id = 1156587796127809560
        print(f"ID: {opus_id}\n")

        opus = Opus(opus_id)
        info = await opus.get_info()

        print(f"✅ 成功！")
        print(f"类型: {type(info)}")
        if isinstance(info, dict):
            print(f"键: {list(info.keys())[:10]}")

            # 保存数据
            import json
            with open('opus_no_credential.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            print(f"💾 数据已保存: opus_no_credential.json")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # 测试2: 带 credential
    print(f"\n{'='*70}")
    print("测试2: Opus 带 credential")
    print(f"{'='*70}\n")

    try:
        from bilibili_api.opus import Opus
        from bilibili_api import Credential

        opus_id = 1156587796127809560
        print(f"ID: {opus_id}\n")

        # 使用空 credential（只是测试）
        credential = Credential()
        opus = Opus(opus_id, credential)
        info = await opus.get_info()

        print(f"✅ 成功！")
        print(f"类型: {type(info)}")
        if isinstance(info, dict):
            print(f"键: {list(info.keys())[:10]}")

            # 保存数据
            import json
            with open('opus_with_credential.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            print(f"💾 数据已保存: opus_with_credential.json")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")


async def test_dynamic_with_credential():
    """测试 Dynamic 接口"""

    print(f"\n{'='*70}")
    print("测试3: Dynamic 无 credential")
    print(f"{'='*70}\n")

    try:
        from bilibili_api.dynamic import Dynamic

        dynamic_id = 1156587796127809560
        print(f"ID: {dynamic_id}\n")

        dynamic = Dynamic(dynamic_id)
        info = await dynamic.get_info()

        print(f"✅ 成功！")
        print(f"类型: {type(info)}")
        if isinstance(info, dict):
            print(f"键: {list(info.keys())[:10]}")

            # 保存数据
            import json
            with open('dynamic_no_credential.json', 'w', encoding='utf-8') as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            print(f"💾 数据已保存: dynamic_no_credential.json")

    except Exception as e:
        print(f"❌ 失败: {type(e).__name__}: {e}")


async def test_check_bilibili_api_version():
    """检查 bilibili_api 版本和配置"""

    print(f"\n{'='*70}")
    print("bilibili_api 版本信息")
    print(f"{'='*70}\n")

    try:
        import bilibili_api
        print(f"版本: {bilibili_api.__version__}")

        # 检查当前配置
        from bilibili_api import HEADERS, request_settings
        print(f"\n默认 HEADERS:")
        for k, v in HEADERS.items():
            print(f"  {k}: {v}")

        print(f"\nrequest_settings:")
        print(f"  {dict(request_settings._settings)}")

    except Exception as e:
        print(f"❌ 失败: {e}")


async def main():
    await test_check_bilibili_api_version()
    await test_opus_with_credential()
    await test_dynamic_with_credential()

    print("\n" + "="*70)
    print("测试完成")
    print("="*70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
