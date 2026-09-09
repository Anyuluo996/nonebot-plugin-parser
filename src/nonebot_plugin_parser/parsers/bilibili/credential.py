"""B站凭据管理：持久化、二维码登录、有效期校验与自动刷新。

从 ``parsers/bilibili/__init__.py`` 拆出。BilibiliParser 通过组合持有
:class:`BilibiliCredentialManager`，将登录/凭据职责从解析逻辑中分离。
"""

import asyncio
from pathlib import Path
from collections.abc import AsyncGenerator

from nonebot import logger
from bilibili_api import Credential
from bilibili_api.login_v2 import QrCodeLogin, QrCodeLoginEvents

from ..cookie import ck2dict
from ...config import pconfig
from ...persist import load_json_or, atomic_write_json


class BilibiliCredentialManager:
    """B站登录凭据的加载/保存/刷新与二维码登录流程。

    凭据来源优先级：``parser_bili_ck`` 配置 → 本地 cookies 文件。
    ``get()`` 每次调用都会校验有效性并按需刷新。
    """

    def __init__(self, cookies_file: Path):
        self._cookies_file = cookies_file
        self._credential: Credential | None = None
        self._qr_login: QrCodeLogin | None = None

    def _save(self) -> None:
        """存储哔哩哔哩登录凭证（原子写）"""
        if self._credential is None:
            return
        atomic_write_json(self._cookies_file, self._credential.get_cookies())

    def _load(self) -> None:
        """从文件加载哔哩哔哩登录凭证；文件缺失/损坏时降级为无凭据"""
        data = load_json_or(self._cookies_file, None, context="哔哩哔哩凭证持久化")
        if not isinstance(data, dict):
            return
        self._credential = Credential.from_cookies(data)

    async def login_with_qrcode(self) -> bytes:
        """通过二维码登录获取哔哩哔哩登录凭证（返回二维码图片内容）"""
        self._qr_login = QrCodeLogin()
        await self._qr_login.generate_qrcode()
        return self._qr_login.get_qrcode_picture().content

    async def check_qr_state(self) -> AsyncGenerator[str]:
        """检查二维码登录状态"""
        if self._qr_login is None:
            yield "请先生成二维码"
            return
        scan_tip_pending = True

        for _ in range(30):
            state = await self._qr_login.check_state()
            match state:
                case QrCodeLoginEvents.DONE:
                    yield "登录成功"
                    self._credential = self._qr_login.get_credential()
                    self._save()
                    break
                case QrCodeLoginEvents.CONF:
                    if scan_tip_pending:
                        yield "二维码已扫描, 请确认登录"
                        scan_tip_pending = False
                case QrCodeLoginEvents.TIMEOUT:
                    yield "二维码过期, 请重新生成"
                    break
            await asyncio.sleep(2)
        else:
            yield "二维码登录超时, 请重新生成"

    async def _init(self) -> None:
        """初始化哔哩哔哩登录凭证"""
        if pconfig.bili_ck is None:
            self._load()
            return

        credential = Credential.from_cookies(ck2dict(pconfig.bili_ck))
        if await credential.check_valid():
            logger.info(f"`parser_bili_ck` 有效, 保存到 {self._cookies_file}")
            self._credential = credential
            self._save()
        else:
            logger.info(f"`parser_bili_ck` 已过期, 尝试从 {self._cookies_file} 加载")
            self._load()

    async def get(self) -> Credential | None:
        """获取当前有效凭据；无效时按需初始化 / 刷新（原 parser.credential 逻辑）"""
        if self._credential is None:
            await self._init()
            return self._credential

        if not await self._credential.check_valid():
            logger.warning("哔哩哔哩凭证已过期, 请重新配置")
            return None

        if await self._credential.check_refresh():
            logger.info("哔哩哔哩凭证需要刷新")
            if self._credential.has_ac_time_value() and self._credential.has_bili_jct():
                await self._credential.refresh()
                logger.info(f"哔哩哔哩凭证刷新成功, 保存到 {self._cookies_file}")
                self._save()
            else:
                logger.warning("哔哩哔哩凭证刷新需要包含 `SESSDATA`, `ac_time_value` 项")

        return self._credential
