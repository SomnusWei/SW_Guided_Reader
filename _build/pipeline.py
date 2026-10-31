# -*- coding: utf-8 -*-
"""
一键重建管线：MDX -> 清洗 -> 解码 -> 别名压缩 -> 分片
用法：PYTHONPATH=stub python pipeline.py
"""
import os, sys, json, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_dict, clean_dict, decode_fields2, compress_alias, shard_dict


def main():
    print('== 1/4 导出 MDX ==', flush=True)
    # build_dict.main 会读 E:/mdx词库 写 data/*.json + index.json
    build_dict.main()
    print('== 2/5 清洗 ==', flush=True)
    clean_dict.main()
    print('== 3/5 字段解码（康熙/说文/本草）==', flush=True)
    decode_fields2.main()
    print('== 4/5 别名压缩 ==', flush=True)
    compress_alias.main()
    print('== 5/5 大词典分片 ==', flush=True)
    shard_dict.main()
    print('ALL DONE', flush=True)


if __name__ == '__main__':
    main()
