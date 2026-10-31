"""插件体系（2.0）：两个根扫描、manifest 校验、启停、安装、卸载回收。"""

from reader_service.plugins.registry import (
    PluginError,
    PluginNotFound,
    PluginRecord,
    PluginRegistry,
    clean_selection_text,
)

__all__ = [
    "PluginError",
    "PluginNotFound",
    "PluginRecord",
    "PluginRegistry",
    "clean_selection_text",
]
