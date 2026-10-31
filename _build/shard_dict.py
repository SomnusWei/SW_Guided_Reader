# -*- coding: utf-8 -*-
"""
为大词典切分：词头索引 + 分块正文（惰性加载）
输出：
  <code>.keys.json    [[key, chunk, offset_in_chunk], ...] + alias 映射
  <code>.<n>.json     [[text, ...], ...]   每块 512 条正文
适用于条目多/体积大的词典（教材仓库、康熙、说文）
"""
import os, json, sys

OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'
CHUNK = 512   # 每块正文条数


def split(code, name, label):
    fp = os.path.join(OUT, code + '.json')
    p = json.load(open(fp, encoding='utf-8'))
    entries = p['e']
    alias = p.get('a', {})
    keys = []
    chunks = []
    cur = []
    ci = 0
    for k, v in entries:
        if len(cur) >= CHUNK:
            chunks.append(cur)
            cur = []
            ci += 1
        keys.append([k, ci, len(cur)])
        cur.append(v)
    if cur:
        chunks.append(cur)

    for i, ch in enumerate(chunks):
        with open(os.path.join(OUT, f'{code}.{i}.json'), 'w', encoding='utf-8') as fh:
            json.dump(ch, fh, ensure_ascii=False, separators=(',', ':'))
    meta = {'keys': keys, 'alias': alias, 'chunks': len(chunks), 'size': CHUNK}
    with open(os.path.join(OUT, code + '.keys.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, separators=(',', ':'))
    os.remove(fp)
    ks = os.path.getsize(os.path.join(OUT, code + '.keys.json')) / 1048576
    tot = sum(os.path.getsize(os.path.join(OUT, f'{code}.{i}.json')) for i in range(len(chunks))) / 1048576
    print(f'{label:16s} keys={len(keys):6d} chunks={len(chunks):4d}  keyidx={ks:.2f}M body={tot:.1f}M')
    return {'chunks': len(chunks), 'chunkSize': CHUNK, 'keyIndex': round(ks, 2)}


def main():
    idx = json.load(open(os.path.join(OUT, 'index.json'), encoding='utf-8'))
    plan = {'tcmtb', 'kangxi', 'swjz', 'bcgm'}
    for d in idx:
        code = d['code']
        if code in plan:
            info = split(code, d['name'], d['name'])
            d.update(info)
            d['sharded'] = True
    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
