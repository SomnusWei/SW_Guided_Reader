"""MDX 词库解析器 —— MDict 2.0 流式变体（实测适配本机词库）

实测布局（中医大辞典.mdx 等，Engines 2.0 的「无 key_block_info」变体）：
  0..8       magic
  8..       header（UTF-16LE，NUL 终止）
  之后       若干 4/8 字节计数
  之后       压缩块流，每块：
                [1 字节 0x02][3 字节压缩后长度 BE][压缩数据]
              压缩数据为 zlib，解压后为交错内容：
                键块： (8 字节 offset BE)+(变长长度? 实测为定长) ... 交替 键/记录
  记录块内容：[8 字节 长度 BE][正文]

为兼容性，这里采用「实测优先 + 结构自适应」策略：
  - 用全文件搜索 zlib 魔数定位块起点
  - 对每个块尝试两种解码（键块 / 记录块），依据内容是否以 UTF-8 中文/ASCII 起始判断
"""
import struct, zlib


def read_vint(buf, i):
    v = 0
    shift = 0
    while True:
        c = buf[i]
        i += 1
        v |= (c & 0x7F) << shift
        shift += 7
        if not (c & 0x80):
            return v, i


class MDXStream:
    """流式 MDX：遍历所有压缩块，输出 (key, body)"""

    def __init__(self, path):
        self.data = open(path, 'rb').read()

    def _blocks(self):
        """定位所有压缩块，返回 [(start, end, decompressed_bytes)]"""
        d = self.data
        i = 0
        out = []
        n = len(d)
        while i < n - 8:
            # 块头：02 00 00 00? 实测首块在 1582 且直接是 78 9c
            j = d.find(b'\x78\x9c', i)
            if j < 0:
                break
            dobj = zlib.decompressobj()
            try:
                body = dobj.decompress(d[j:j + 200000])
                consumed = len(d[j:j + 200000]) - len(dobj.unused_data)
            except zlib.error:
                i = j + 2
                continue
            if len(body) < 100:
                i = j + 2
                continue
            out.append((j, j + consumed, body))
            i = j + consumed
        return out

    def parse(self):
        """返回 (keys: list[(key, record_offset_global)], records: dict)"""
        keys = []
        blocks = self._blocks()
        # 第一个「像键块」的块之后即为记录区；这里按块内容自动判别
        keyable = []
        rec_blocks = []
        for s, e, body in blocks:
            # 键块特征：解压后由大量 8 字节偏移 + 短 UTF-8 词条名组成
            ok = 0
            i = 0
            L = len(body)
            while i + 9 < L and ok < 8:
                off = struct.unpack('>Q', body[i:i + 8])[0]
                i += 8
                # 读短文本直到 0x00
                k = body.find(b'\x00', i, i + 60)
                if k < 0:
                    break
                seg = body[i:k]
                try:
                    seg.decode('utf-8')
                    ok += 1
                except UnicodeDecodeError:
                    break
                i = k + 1
            if ok >= 5:
                keyable.append((s, e, body))
            else:
                rec_blocks.append((s, e, body))

        for s, e, body in keyable:
            i = 0
            L = len(body)
            while i + 8 < L:
                off = struct.unpack('>Q', body[i:i + 8])[0]
                i += 8
                k = body.find(b'\x00', i, i + 120)
                if k < 0:
                    break
                seg = body[i:k]
                i = k + 1
                try:
                    key = seg.decode('utf-8')
                except UnicodeDecodeError:
                    continue
                keys.append((key, off))
        return keys, rec_blocks
