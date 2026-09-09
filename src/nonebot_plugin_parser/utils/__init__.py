"""通用工具门面。

按领域拆分实现，此处统一 re-export 保持既有导入路径兼容：

- :mod:`._common`  缓存字典 / 文本清洗 / 文件与格式化小工具
- :mod:`.media`    ffmpeg / ffprobe / gifsicle 音视频处理
- :mod:`.imaging`  PIL 图像处理（二维码渲染 / ugoira 动图，PIL 可选）
"""

from .media import (
    FFMPEG_TIMEOUT as FFMPEG_TIMEOUT,
)
from .media import (
    merge_av as merge_av,
)
from .media import (
    optimize_gif as optimize_gif,
)
from .media import (
    merge_av_h264 as merge_av_h264,
)
from .media import (
    _run_subprocess as _run_subprocess,
)
from .media import (
    exec_ffmpeg_cmd as exec_ffmpeg_cmd,
)
from .media import (
    exec_ffprobe_cmd as exec_ffprobe_cmd,
)
from .media import (
    has_audio_stream as has_audio_stream,
)
from .media import (
    convert_video_to_gif as convert_video_to_gif,
)
from .media import (
    encode_video_to_h264 as encode_video_to_h264,
)
from .media import (
    extract_video_thumbnail as extract_video_thumbnail,
)
from ._common import (
    LimitedSizeDict as LimitedSizeDict,
)
from ._common import (
    fmt_size as fmt_size,
)
from ._common import (
    safe_unlink as safe_unlink,
)
from ._common import (
    fmt_duration as fmt_duration,
)
from ._common import (
    keep_zh_en_num as keep_zh_en_num,
)
from ._common import (
    generate_file_name as generate_file_name,
)
from ._common import (
    write_json_to_data as write_json_to_data,
)
from ._common import (
    is_module_available as is_module_available,
)
from .imaging import (
    PIL_AVAILABLE as PIL_AVAILABLE,
)
from .imaging import (
    convert_ugoira_to_gif as convert_ugoira_to_gif,
)
from .imaging import (
    render_qr_ascii_to_png as render_qr_ascii_to_png,
)
from .imaging import (
    extract_ugoira_thumbnail as extract_ugoira_thumbnail,
)
