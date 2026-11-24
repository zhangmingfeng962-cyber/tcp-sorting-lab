"""TCP 两端共用的消息格式。"""

import json


ALGORITHMS = {
    "bubble": "冒泡排序",
    "insertion": "插入排序",
    "selection": "选择排序",
}
DEFAULT_VALUES = [42, 17, 61, 23, 17, 55, 8, 34]
MAX_VALUES = 64
MAX_ABS_VALUE = 9999
MAX_FRAME_BYTES = 8192
SOCKET_TIMEOUT = 15


class ProtocolError(ValueError):
    """收到的消息不符合约定。"""


def integer_list(value):
    if not isinstance(value, list) or len(value) > MAX_VALUES:
        raise ProtocolError(f"列表最多包含 {MAX_VALUES} 个整数")
    if any(type(item) is not int or abs(item) > MAX_ABS_VALUE for item in value):
        raise ProtocolError(f"每项必须是 {-MAX_ABS_VALUE} 到 {MAX_ABS_VALUE} 之间的整数")
    return value


def send_message(connection, message_type, **fields):
    line = json.dumps({"type": message_type, **fields}, ensure_ascii=False, separators=(",", ":"))
    connection.sendall(line.encode("utf-8") + b"\n")


def read_message(stream):
    line = stream.readline(MAX_FRAME_BYTES + 1)
    if not line:
        raise ProtocolError("对端已断开 TCP 连接")
    if len(line) > MAX_FRAME_BYTES or not line.endswith(b"\n"):
        raise ProtocolError("消息超出长度限制或缺少换行符")
    try:
        message = json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError) as error:
        raise ProtocolError("消息须为 UTF-8 编码的 JSON") from error
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ProtocolError("消息缺少有效的 type 字段")
    return message
