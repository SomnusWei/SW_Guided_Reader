# PyInstaller 入口：冻结 reader_service 命令行主程序
import multiprocessing
import sys

multiprocessing.freeze_support()


def _force_utf8_streams() -> None:
    """把标准输出固定为 UTF-8。

    被 Tauri 以无窗口管道方式拉起时，Windows 管道默认跟随系统区域编码
    （简体中文系统为 GBK），中文日志会变成非 UTF-8 字节；父进程按 UTF-8
    读取时解码失败，会误判为"后端进程意外退出"。这里在最早时机统一编码，
    并开启行缓冲，保证 READY 就绪行立即送达父进程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            continue


_force_utf8_streams()

from reader_service.__main__ import main

if __name__ == "__main__":
    main()
