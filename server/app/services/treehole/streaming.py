"""树洞流式输出的回调上下文（线程本地）。

on_delta 回调不能放进 TreeholeState（SqliteSaver 会把状态序列化进 checkpoint，函数
对象不可序列化）——用线程本地槽：service 在 invoke 前挂上、generate 节点取用、invoke
后清掉。同步路由跑在请求线程里，worker 线程各自独立，无串扰。
"""
import threading

_local = threading.local()


def set_delta_cb(cb) -> None:
    _local.cb = cb


def get_delta_cb():
    return getattr(_local, "cb", None)


def clear() -> None:
    _local.cb = None
