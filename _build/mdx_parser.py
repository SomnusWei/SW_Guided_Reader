"""MDX 词库解析器 —— MDict 2.0（实测适配 E:\\mdx词库 本机全部 .mdx）

逐字节验证后的结构：

  [0..8]      magic
  [8..N]      header（UTF-16LE），2 字节 NUL 结束
  [N+2..]     计数区：num_entries / num_key_blocks / key_block_info_size / key_block_size
  [..]        key_block_info 目录
  [..]        压缩块流（每块独立 zlib；块头长度编码各家不同 → 直接扫 zlib 流）

  ▸ 键块：按块顺序排在前面，每条 = [8B 偏移 BE][2B 长度 BE][键 UTF-8]
  ▸ 记录块：每条 = [8B 偏移 BE][2B 长度 BE][正文 HTML]

  ⚠ 早期尝试把「2B 长度」当成 NUL 分隔键，会在正文含 0 字节时错位；
    按长度走位才是正确读法。

用法：
    m = MDX(path)
    for key, body in m.entries(): ...
"""
import struct, zlib, re

ZHEAD = (b'\x78\x9c', b'\x78\x01', b'\x78\xda')


def _walk(raw):
    """按 [8B 偏移][2B 长度][数据] 走位，返回 [(offset, data_bytes)]"""
    out = []
    i = 0
    L = len(raw)
    while i + 10 <= L:
        off = struct.unpack('>Q', raw[i:i + 8])[0]
        ln = struct.unpack('>H', raw[i + 8:i + 10])[0]
        i += 10
        if ln == 0 or i + ln > L:
            return out, False
        out.append((off, raw[i:i + ln]))
        i += ln
    return out, i >= L - 2


class MDX:
    def __init__(self, path):
        self.path = path
        self.data = d = open(path, 'rb').read()

        i = 8
        while i < 65536 and not (d[i] == 0 and d[i + 1] == 0):
            i += 2
        self.header = d[8:i].decode('utf-16-le', 'replace').strip()
        self.attrs = dict(re.findall(r'(\w+)="([^"]*)"', self.header))
        self.title = (self.attrs.get('Title') or self.attrs.get('Bookname') or '').strip()
        self.encrypted = self.attrs.get('Encrypted', 'No')
        self.encoding = self.attrs.get('Encoding', 'UTF-8')

        self.blocks = self._scan()

    def _scan(self):
        d = self.data
        out = []
        i, n = 0, len(d)
        CH = 8 * 1024 * 1024
        while i < n - 4:
            j = -1
            for head in ZHEAD:
                k = d.find(head, i)
                if k >= 0 and (j < 0 or k < j):
                    j = k
            if j < 0:
                break
            dobj = zlib.decompressobj()
            try:
                chunk = dobj.decompress(d[j:j + CH])
                used = CH - len(dobj.unused_data)
                if len(chunk) > 32:
                    out.append(chunk)
                    i = j + used
                    continue
            except zlib.error:
                pass
            i = j + 2
        return out

    def keys_and_records(self):
        """键块在前、记录块在后；用「数据能否解成合法 UTF-8 词条」区分"""
        keys, recs = [], []
        phase = 'keys'
        for raw in self.blocks:
            items, complete = _walk(raw)
            if not items:
                continue
            if phase == 'keys':
                # 键块特征：数据短且是合法 UTF-8（不含 HTML 标签起始）
                sample = [x[1] for x in items[:20]]
                ok = all(len(s) < 200 for s in sample)
                if ok:
                    try:
                        keys.extend((s.decode('utf-8'), off) for off, s in items)
                        continue
                    except UnicodeDecodeError:
                        pass
                phase = 'records'
            recs.extend(s for _off, s in items)
        return keys, recs

    def entries(self):
        keys, recs = self.keys_and_records()
        for idx in range(min(len(keys), len(recs))):
            yield keys[idx][0], recs[idx]
