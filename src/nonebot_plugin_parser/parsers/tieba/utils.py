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


def build_content(posts: Posts, create_image, create_video=None) -> list:
    """构建主楼正文为 graphics（文本 + 图片 + 视频）。"""
    contents: list = []
    if posts.thread.title:
        contents.append(posts.thread.title)

    if not posts.objs:
        return contents

    for part in posts.objs[0].contents.objs:
        if isinstance(part, FragText):
            contents.append(part.text)
        elif isinstance(part, FragImage) and create_image:
            contents.append(create_image(part.origin_src or part.src))
        elif isinstance(part, FragAt):
            if contents and isinstance(contents[-1], str):
                contents[-1] += f"@{part.text} "
            else:
                contents.append(f"@{part.text} ")
        elif isinstance(part, FragLink):
            url_str = str(part.text)
            if contents and isinstance(contents[-1], str):
                contents[-1] += url_str
            else:
                contents.append(url_str)
        elif isinstance(part, FragVideo) and create_video:
            contents.append(
                create_video(part.src, cover_url=part.cover_src, duration=part.duration)
            )
    return contents
