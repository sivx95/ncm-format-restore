"""网易云音乐 .ncm 格式解密核心。

算法对齐 nondanee/ncmdump：
- 密钥 AES-ECB 解密后 unpad，再切 neteasecloudmusic 前缀
- 音频使用「改版 RC4」256 字节密钥流（非 box[(i+1)&0xff]）
- 图像区：image_space + image_size，可能带 padding
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687A4852416D736F356B496E62617857")  # hzHRAmso5kInbaxW
META_KEY = bytes.fromhex("2331346C6A6B5F215C5D2630553C2728")  # #14ljk_!\]&0U<'(
NETEASE_PREFIX = b"neteasecloudmusic"
MUSIC_PREFIX = b"Music:"
META_PREFIX = b"163 key(Don't modify):"

KNOWN_FORMATS = frozenset({"mp3", "flac", "m4a", "wav", "ogg", "aac", "ape"})


class NcmError(Exception):
    """ncm 解密失败。"""


@dataclass
class NcmResult:
    audio: bytes
    format: str
    metadata: dict
    image: bytes | None

    @property
    def title(self) -> str:
        return str(self.metadata.get("musicName") or self.metadata.get("title") or "").strip()

    @property
    def artist(self) -> str:
        artist = self.metadata.get("artist")
        if isinstance(artist, list):
            parts: list[str] = []
            for item in artist:
                if isinstance(item, (list, tuple)) and item:
                    parts.append(str(item[0]))
                elif item is not None:
                    parts.append(str(item))
            return " / ".join(p for p in parts if p)
        if artist is None:
            return ""
        return str(artist).strip()

    def suggest_name(self, fallback_stem: str) -> str:
        title = self._sanitize(self.title)
        artist = self._sanitize(self.artist)
        if title and artist:
            stem = f"{artist} - {title}"
        elif title:
            stem = title
        else:
            stem = fallback_stem
        return f"{stem}.{self.format}"

    @staticmethod
    def _sanitize(name: str) -> str:
        bad = '<>:"/\\|?*'
        for ch in bad:
            name = name.replace(ch, "_")
        return name.strip(" .")


def _aes_decrypt_unpad(data: bytes, key: bytes) -> bytes:
    if not data or len(data) % 16 != 0:
        raise NcmError("密文长度不是 16 的倍数")
    plain = AES.new(key, AES.MODE_ECB).decrypt(data)
    return unpad(plain, 16)


def _build_rc4_box(key: bytes) -> bytearray:
    if not key:
        raise NcmError("解密密钥为空")
    box = bytearray(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + box[i] + key[i % key_len]) & 0xFF
        box[i], box[j] = box[j], box[i]
    return box


def _build_stream(box: bytearray, length: int) -> bytes:
    """改版 RC4 密钥流（与官方 ncmdump 一致）。"""
    table = [box[(box[i] + box[(i + box[i]) & 0xFF]) & 0xFF] for i in range(256)]
    repeats = length // 256 + 1
    expanded = bytes(bytearray(table * repeats))
    # 跳过第 0 字节，取 length 长
    return expanded[1 : 1 + length]


def _xor_bytes(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def _parse_metadata(raw: bytes) -> dict:
    # 标准实现：整段 UTF-8 后 base64[22:]
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="ignore")

    if text.startswith(META_PREFIX.decode("ascii")):
        b64 = text[22:]
    else:
        # 容错：找前缀
        idx = text.find("163 key(Don't modify):")
        b64 = text[idx + 22 :] if idx >= 0 else text

    try:
        decoded = base64.b64decode(b64)
        plain = _aes_decrypt_unpad(decoded, META_KEY).decode("utf-8")
        # 官方样本为 "music:"，历史实现多为 "Music:"
        if plain[:6].lower() == "music:":
            plain = plain[6:]
        return json.loads(plain)
    except Exception as exc:  # noqa: BLE001
        raise NcmError(f"元数据解析失败: {exc}") from exc


def _meta_format(metadata: dict, file_size: int) -> str:
    value = metadata.get("format")
    if value:
        fmt = str(value).lower().strip()
        if fmt in KNOWN_FORMATS:
            return fmt
    # 官方 fallback：大文件更可能是 flac
    if file_size > 1024 * 1024 * 16:
        return "flac"
    return "mp3"


def sniff_strong(data: bytes) -> str | None:
    if len(data) < 4:
        return None
    if data[:4] == b"fLaC":
        return "flac"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"MAC ":
        return "ape"
    if data[:3] == b"ID3":
        return "mp3"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "m4a"
    return None


def decrypt_ncm(path: str | Path) -> NcmResult:
    path = Path(path)
    if not path.is_file():
        raise NcmError(f"文件不存在: {path}")
    if path.suffix.lower() != ".ncm":
        raise NcmError(f"不是 .ncm 文件: {path.name}")

    file_size = path.stat().st_size

    with path.open("rb") as f:
        header = f.read(8)
        if header != MAGIC:
            raise NcmError(f"文件头无效: {path.name}")

        f.seek(2, 1)

        key_length = struct.unpack("<I", f.read(4))[0]
        if key_length <= 0 or key_length > 1024 * 1024:
            raise NcmError("密钥长度异常")
        key_data = bytearray(f.read(key_length))
        if len(key_data) != key_length:
            raise NcmError("密钥数据不完整")
        for i in range(key_length):
            key_data[i] ^= 0x64

        try:
            rc4_key = _aes_decrypt_unpad(bytes(key_data), CORE_KEY)[17:]
        except Exception as exc:  # noqa: BLE001
            raise NcmError(f"核心密钥解密失败: {exc}") from exc

        box = _build_rc4_box(rc4_key)

        meta_length = struct.unpack("<I", f.read(4))[0]
        if meta_length < 0 or meta_length > 10 * 1024 * 1024:
            raise NcmError("元数据长度异常")

        if meta_length:
            meta_data = bytearray(f.read(meta_length))
            if len(meta_data) != meta_length:
                raise NcmError("元数据不完整")
            for i in range(meta_length):
                meta_data[i] ^= 0x63
            try:
                metadata = _parse_metadata(bytes(meta_data))
            except NcmError:
                metadata = {}
        else:
            metadata = {}

        # 与官方 ncmdump 一致：跳过 5 字节后读 image_space / image_size
        f.seek(5, 1)
        image_space = struct.unpack("<I", f.read(4))[0]
        image_size = struct.unpack("<I", f.read(4))[0]
        if image_size < 0 or image_space < image_size or image_space > 50 * 1024 * 1024:
            # 回退：旧结构 = CRC(4)+gap(5)+image_size(4)，已在上面误读
            # 此时把 image_space 当作 image_size 更稳妥
            image_size = image_space
            image_space = image_size

        image = f.read(image_size) if image_size else None
        if image_size and (image is None or len(image) != image_size):
            raise NcmError("封面图数据不完整")

        # 图像区可能有预留空白
        padding = image_space - image_size
        if padding > 0:
            f.seek(padding, 1)

        encrypted = f.read()
        if not encrypted:
            raise NcmError("音频数据为空")

    stream = _build_stream(box, len(encrypted))
    audio = _xor_bytes(encrypted, stream)

    # 若魔数不对，尝试旧算法 box[(i+1)&0xff]（少数旧样本）
    if sniff_strong(audio) is None:
        alt = bytes(encrypted[i] ^ box[(i + 1) & 0xFF] for i in range(len(encrypted)))
        if sniff_strong(alt) is not None:
            audio = alt

    fmt = _meta_format(metadata, file_size)
    strong = sniff_strong(audio)
    if strong and strong in KNOWN_FORMATS:
        # 元数据与魔数冲突时，以魔数为准（更可靠）
        if strong != "mp3" or fmt == "mp3":
            fmt = strong
        elif strong == "mp3" and fmt not in KNOWN_FORMATS:
            fmt = "mp3"

    return NcmResult(audio=audio, format=fmt, metadata=metadata, image=image)


def convert_file(
    src: str | Path,
    out_dir: str | Path | None = None,
    overwrite: bool = False,
) -> Path:
    src = Path(src)
    result = decrypt_ncm(src)
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / result.suggest_name(src.stem)
    if out_path.exists() and not overwrite:
        stem = out_path.stem
        for i in range(1, 1000):
            candidate = out_dir / f"{stem}_{i}.{result.format}"
            if not candidate.exists():
                out_path = candidate
                break

    out_path.write_bytes(result.audio)
    return out_path
