"""贴吧 protobuf 请求/解析工具。

适配自 parser-lite 的 tieba/utils.py。
将全局 httpx client 替换为 BaseParser.request，把 create_image 等回调用参数传入。
"""

from pathlib import Path
from functools import lru_cache

from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.message_factory import GetMessageClass

from .models import (
    Posts,
    FragAt,
    FragLink,
    FragText,
    FragImage,
    FragVideo,
)


@lru_cache(maxsize=2)
def get_message(name: str):
    fds = descriptor_pb2.FileDescriptorSet()
    fds.ParseFromString((Path(__file__).parent / f"{name}.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    msg_descriptor = pool.FindMessageTypeByName(name)
    return GetMessageClass(msg_descriptor)


def make_req(tid: int) -> bytes:
    req_proto = get_message("PbPageReqIdl")()
    req_proto.data.common._client_type = 2  # type: ignore
    req_proto.data.common._client_version = "12.64.1.1"  # type: ignore
    req_proto.data.kz = tid  # type: ignore
    req_proto.data.pn = 1  # type: ignore
    req_proto.data.rn = 30  # type: ignore
    req_proto.data.r = 0  # type: ignore
    req_proto.data.lz = 0  # type: ignore
    req_proto.data.with_floor = 1  # type: ignore
    req_proto.data.floor_sort_type = 1  # type: ignore
    req_proto.data.floor_rn = 4  # type: ignore
    return req_proto.SerializeToString()


async def pack_req(parser, data: bytes) -> bytes:
    """通过 BaseParser.request 发送 protobuf 请求。"""
    boundary = "-*_r1999"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="data"; filename="file"\r\n'
            f"\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = await parser.request(
        "http://tiebac.baidu.com/c/f/pb/page",
        method="POST",
        headers={
            "x_bd_data_type": "protobuf",
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            "User-Agent": "miku/39",
            "Host": "tiebac.baidu.com",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        params={"cmd": 302001},
        content=body,
    )
    return response.content


def parse_res(data: bytes) -> Posts:
    res = get_message("PbPageResIdl")()
    res.ParseFromString(data)
    if res.error.errorno:  # type: ignore
        raise ValueError(res.error.errmsg)  # type: ignore
    data_proto = res.data  # type: ignore
    return Posts.from_tbdata(data_proto)


async def get_post(parser, tid: int) -> Posts:
    req = make_req(tid)
    data = await pack_req(parser, req)
    return parse_res(data)


def _frags_to_graphics(contents_objs, create_image, create_video=None) -> list:
    """把一个楼层的 Contents.objs 碎片列表转为 graphics（文本 + 图片 + 视频）。

    相邻文本碎片合并，@/链接拼到当前文本末尾，图片/视频独立成项。
    build_content（主楼）与 build_reply_floors（回复楼层）共用本逻辑。
    """
    out: list = []
    for part in contents_objs:
        if isinstance(part, FragText):
            out.append(part.text)
        elif isinstance(part, FragImage) and create_image:
            out.append(create_image(part.origin_src or part.src))
        elif isinstance(part, FragAt):
            if out and isinstance(out[-1], str):
                out[-1] += f"@{part.text} "
            else:
                out.append(f"@{part.text} ")
        elif isinstance(part, FragLink):
            url_str = str(part.text)
            if out and isinstance(out[-1], str):
                out[-1] += url_str
            else:
                out.append(url_str)
        elif isinstance(part, FragVideo) and create_video:
            out.append(
                create_video(part.src, cover_url=part.cover_src, duration=part.duration)
            )
    return out


def build_content(posts: Posts, create_image, create_video=None) -> list:
    """构建主楼正文为 graphics（文本 + 图片 + 视频）。"""
    contents: list = []
    if posts.thread.title:
        contents.append(posts.thread.title)

    if not posts.objs:
        return contents

    contents.extend(_frags_to_graphics(posts.objs[0].contents.objs, create_image, create_video))
    return contents


# 前几楼回复渲染数（与 NGA 的 MAX_REPLY_FLOORS、知乎 TOP_ANSWERS_LIMIT 对齐）
MAX_REPLY_FLOORS = 4


def build_reply_floors(posts: Posts, create_image, create_video=None, limit: int = MAX_REPLY_FLOORS) -> list:
    """从 posts.objs[1:] 构造前 ``limit`` 个回复楼层，供专用渲染器画长图。

    每个 floor dict 字段对齐 NGA 的 posts 结构：
    - floor: 楼层号
    - name: 用户显示名
    - avatar: ImageContent（头像下载任务）或 None
    - agree: 点赞数
    - text: 文本内容（碎片拼接）
    - images: ImageContent 列表（楼层内嵌图）
    """
    floors: list[dict] = []
    for post in posts.objs[1:]:  # objs[0] 是主楼
        if len(floors) >= limit:
            break

        graphics = _frags_to_graphics(post.contents.objs, create_image, create_video)
        text_parts = [g for g in graphics if isinstance(g, str)]
        images = [g for g in graphics if not isinstance(g, str)]

        portrait = post.user.portrait
        floors.append(
            {
                "floor": post.floor,
                "name": post.user.show_name or "匿名",
                "avatar": create_image(f"http://tb.himg.baidu.com/sys/portraith/item/{portrait}") if portrait else None,
                "agree": post.agree,
                "text": "".join(text_parts).strip(),
                "images": images,
            }
        )
    return floors
