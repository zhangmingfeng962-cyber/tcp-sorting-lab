"""接收服务端列表，执行排序并逐步上报。"""

import argparse
import socket
import time

from protocol import ALGORITHMS, SOCKET_TIMEOUT, ProtocolError, integer_list
from protocol import read_message, send_message


def bubble_sort(values, compare, swap):
    for end in range(len(values) - 1, 0, -1):
        changed = False
        for left in range(end):
            if compare(left, left + 1) > 0:
                swap(left, left + 1)
                changed = True
        if not changed:
            break


def insertion_sort(values, compare, swap):
    for index in range(1, len(values)):
        while index > 0 and compare(index - 1, index) > 0:
            swap(index - 1, index)
            index -= 1


def selection_sort(values, compare, swap):
    for start in range(len(values) - 1):
        minimum = start
        for index in range(start + 1, len(values)):
            if compare(index, minimum) < 0:
                minimum = index
        if minimum != start:
            swap(start, minimum)


SORT_FUNCTIONS = {
    "bubble": bubble_sort,
    "insertion": insertion_sort,
    "selection": selection_sort,
}


class SortClient:
    def __init__(self, host="127.0.0.1", port=5000, delay_ms=0):
        self.address = (host, port)
        self.delay_ms = delay_ms

    def run(self):
        with socket.create_connection(self.address, timeout=SOCKET_TIMEOUT) as connection:
            return self.run_connected(connection)

    def run_connected(self, connection):
        all_verified = True
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with connection.makefile("rb") as stream:
            print(f"已连接 TCP 服务端 {self.address[0]}:{self.address[1]}", flush=True)
            for algorithm, name in ALGORITHMS.items():
                job = self._expect(stream, "job")
                if job.get("algorithm") != algorithm:
                    raise ProtocolError("任务算法与约定顺序不一致")
                values = integer_list(job.get("values"))
                print(f"{name}收到列表 {values}", flush=True)
                self._sort(connection, algorithm, values)
                result = self._expect(stream, "result")
                if result.get("algorithm") != algorithm:
                    raise ProtocolError("服务端回执与当前算法不一致")
                steps = result.get("steps")
                elapsed_ms = result.get("elapsed_ms")
                verified = result.get("verified")
                if (
                    type(steps) is not int or steps < 0
                    or type(elapsed_ms) not in (int, float)
                    or not 0 <= elapsed_ms < float("inf")
                    or type(verified) is not bool
                ):
                    raise ProtocolError("服务端回执的统计字段无效")
                all_verified = all_verified and verified
                status = "验证通过" if verified else "验证失败"
                print(
                    f"{name}结果 {values}  总步数 {steps}  "
                    f"用时 {elapsed_ms:.2f} ms  {status}",
                    flush=True,
                )
            self._expect(stream, "complete")
        return all_verified

    def _sort(self, connection, algorithm, values):
        def report(kind, left, right):
            send_message(connection, "step", kind=kind, indices=[left, right], values=values)
            if self.delay_ms:
                time.sleep(self.delay_ms / 1000)

        def compare(left, right):
            report("compare", left, right)
            return values[left] - values[right]

        def swap(left, right):
            values[left], values[right] = values[right], values[left]
            report("swap", left, right)

        SORT_FUNCTIONS[algorithm](values, compare, swap)
        send_message(connection, "done", values=values)

    @staticmethod
    def _expect(stream, message_type):
        message = read_message(stream)
        if message["type"] == "error":
            raise ProtocolError(str(message.get("message", "服务端结束了本轮")))
        if message["type"] != message_type:
            raise ProtocolError(f"预期收到 {message_type} 消息")
        return message


def main():
    parser = argparse.ArgumentParser(description="连接 TCP 服务端并执行三种排序")
    parser.add_argument("--host", default="127.0.0.1", help="服务端 IPv4 地址")
    parser.add_argument("--port", type=int, default=5000, help="服务端 TCP 端口")
    parser.add_argument("--delay-ms", type=int, default=0, help="每步上报后的演示延时，单位毫秒")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port 须为 1 到 65535")
    if not 0 <= args.delay_ms <= 1000:
        parser.error("--delay-ms 须为 0 到 1000")
    try:
        verified = SortClient(args.host, args.port, args.delay_ms).run()
    except (OSError, ProtocolError) as error:
        parser.exit(1, f"客户端错误：{error}\n")
    return 0 if verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
