from msgspec import Struct, field
from msgspec.json import Decoder


class Word(Struct):
    startMs: int
    endMs: int
    text: str


class Sentence(Word):
    words: list[Word]


class Lyrics(Struct):
    sentences: list[Sentence] = field(default_factory=list)


class Stats(Struct):
    count_collected: int = 0
    count_comment: int = 0
    count_shared: int = 0


class AlbumInfo(Struct):
    name: str
    id: str


class TrackInfo(Struct):
    stats: Stats
    album: AlbumInfo


class Urls(Struct):
    urls: list[str]


class User(Struct):
    id: str
    nickname: str
    medium_avatar_url: Urls

    @property
    def avatar_url(self) -> str:
        return self.medium_avatar_url.urls[0]


class _AudioWithLyrics(Struct):
    url: str
    duration: float
    artistName: str
    trackName: str
    trackInfo: TrackInfo
    coverURL: str
    _lyrics: Lyrics = field(name="lyrics", default_factory=Lyrics)

    @property
    def lyrics(self) -> str:
        """将 KRC 歌词转换为 LRC 格式。"""
        lrc_lines: list[str] = []
        for sentence in self._lyrics.sentences:
            start_ms = sentence.startMs
            sentence_text = (sentence.text or "").strip()
            if not sentence_text and sentence.words:
                sentence_text = "".join(w.text for w in sentence.words).strip()
            if not sentence_text:
                continue
            minutes = start_ms // 60000
            seconds = (start_ms % 60000) // 1000
            centiseconds = (start_ms % 1000) // 10
            time_tag = f"[{minutes:02d}:{seconds:02d}.{centiseconds:02d}]"
            lrc_lines.append(time_tag + sentence_text)
        return "\n".join(lrc_lines)


class _TrackPage(Struct):
    audioWithLyricsOption: _AudioWithLyrics


class _LoaderData(Struct):
    track_page: _TrackPage


class RouterData(Struct):
    loaderData: _LoaderData


decoder = Decoder(RouterData)
