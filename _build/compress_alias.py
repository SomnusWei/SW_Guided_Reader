# -*- coding: utf-8 -*-
"""
最终压缩：把重复释义改为别名引用
输出格式：
  {"e": [[key, text], ...], "a": {"alias": "目标key", ...}}
前端查词时先查 e，未命中再查 a 并跳转。
"""
import os, json, collections

OUT = r'D:/workbuddy/2026-10-22-09-54-21/学习工具平台/tools/dict/data'


def main():
    idx = json.load(open(os.path.join(OUT, 'index.json'), encoding='utf-8'))
    total_in = total_out = 0
    for d in idx:
        fp = os.path.join(OUT, d['file'])
        arr = json.load(open(fp, encoding='utf-8'))
        total_in += os.path.getsize(fp)
        # 找重复
        groups = collections.defaultdict(list)
        for k, v in arr:
            groups[v].append(k)
        alias = {}
        keep = []
        used = set()
        for v, keys in groups.items():
            if len(keys) > 1:
                keys = sorted(keys, key=len)
                head = keys[0]
                keep.append([head, v])
                used.add(head)
                for other in keys[1:]:
                    if other != head:
                        alias[other] = head
            else:
                keep.append([keys[0], v])
                used.add(keys[0])
        payload = {'e': keep}
        if alias:
            payload['a'] = alias
        with open(fp, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(',', ':'))
        sz = os.path.getsize(fp)
        total_out += sz
        print(f"{d['name']:16s} {len(arr):7d} -> {len(keep):7d} + alias {len(alias):6d}  {sz/1048576:6.2f}M")
        d['count'] = len(arr)
        d['size'] = round(sz / 1048576, 2)
    with open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8') as fh:
        json.dump(idx, fh, ensure_ascii=False, indent=1)
    print(f'\nTOTAL {total_in/1048576:.1f}M -> {total_out/1048576:.1f}M')


if __name__ == '__main__':
    main()
