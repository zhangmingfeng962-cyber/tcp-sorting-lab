"""发布 TCP 排序任务、统计回报，并在本机展示结果。"""

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from client import SortClient
from protocol import ALGORITHMS, DEFAULT_VALUES, SOCKET_TIMEOUT, ProtocolError
from protocol import integer_list, read_message, send_message


WEB_ROOT = Path(__file__).resolve().parent / "web"
DEMO_DELAY_MS = 180
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
}


def endpoint(address):
    return f"{address[0]}:{address[1]}"


def empty_result():
    return {
        "status": "pending",
        "comparisons": 0,
        "swaps": 0,
        "steps": 0,
        "elapsed_ms": None,
        "verified": None,
    }


class SortServer:
    def __init__(self, values=None, tcp_host="127.0.0.1", tcp_port=5000, web_port=8000):
        self.values = integer_list(list(DEFAULT_VALUES if values is None else values))
        self.tcp_address = (tcp_host, tcp_port)
        self.web_port = web_port
        self._lock = threading.Lock()
        self._demo_requested = threading.Event()
        self._state = {
            "version": 0,
            "status": "waiting",
            "server": None,
            "client": None,
            "original": self.values,
            "values": self.values,
            "current": None,
            "operation": None,
            "algorithms": ALGORITHMS,
            "results": self._empty_results(),
            "error": None,
            "demo_active": False,
            "demo_error": None,
        }

    @staticmethod
    def _empty_results():
        return {algorithm: empty_result() for algorithm in ALGORITHMS}

    def _publish(self, **changes):
        with self._lock:
            self._state.update(changes)
            self._state["version"] += 1

    def snapshot(self):
        with self._lock:
            return json.dumps(self._state, ensure_ascii=False).encode("utf-8")

    def request_demo(self):
        with self._lock:
            if self._state["demo_active"] or self._state["status"] == "running":
                return False
            self._state.update(demo_active=True, demo_error=None)
            self._state["version"] += 1
            self._demo_requested.set()
            return True

    def _serve_demo(self, listener, target):
        try:
            with socket.create_connection(target, timeout=SOCKET_TIMEOUT) as demo_connection:
                demo_peer = demo_connection.getsockname()
                while True:
                    connection, peer = listener.accept()
                    with connection:
                        if peer == demo_peer:
                            worker = threading.Thread(
                                target=self._run_demo_client, args=(demo_connection, target), daemon=True,
                            )
                            worker.start()
                            self._handle_connection(connection, peer)
                            break
                        connection.settimeout(SOCKET_TIMEOUT)
                        try:
                            send_message(connection, "error", message="网页演示正在启动，请稍后重试")
                        except OSError:
                            pass
                worker.join()
        except OSError as error:
            self._report_demo_error(error)
        finally:
            self._publish(demo_active=False)

    def _run_demo_client(self, connection, target):
        try:
            SortClient(*target, delay_ms=DEMO_DELAY_MS).run_connected(connection)
        except (OSError, ProtocolError) as error:
            self._report_demo_error(error)
            # 让仍在等待消息的服务端立刻结束本轮。
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _report_demo_error(self, error):
        message = f"网页演示连接失败：{error}"
        self._publish(demo_error=message)
        print(message, flush=True)

    def serve(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            # Windows 下避免多个进程同时监听同一个端口。
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(self.tcp_address)
            listener.listen()
            listener.settimeout(0.25)
            host, port = listener.getsockname()
            demo_address = ("127.0.0.1" if host == "0.0.0.0" else host, port)
            with ThreadingHTTPServer(("127.0.0.1", self.web_port), self._handler()) as dashboard:
                self._publish(server=endpoint((host, port)))
                worker = threading.Thread(target=dashboard.serve_forever, daemon=True)
                worker.start()
                print(f"TCP 监听 {endpoint((host, port))}", flush=True)
                print(f"可视化 http://{endpoint(dashboard.server_address)}", flush=True)
                try:
                    while True:
                        if self._demo_requested.is_set():
                            self._demo_requested.clear()
                            self._serve_demo(listener, demo_address)
                            continue
                        try:
                            connection, address = listener.accept()
                        except socket.timeout:
                            continue
                        with connection:
                            self._handle_connection(connection, address)
                finally:
                    dashboard.shutdown()
                    worker.join()

    def _handle_connection(self, connection, address):
        connection.settimeout(SOCKET_TIMEOUT)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        server = endpoint(connection.getsockname())
        client = endpoint(address)
        print(f"\nTCP 客户端 {client} 已连接服务端 {server}", flush=True)
        results = self._empty_results()
        self._publish(
            status="running", server=server, client=client, error=None,
            current=None, operation=None, values=self.values, results=results.copy(),
        )
        try:
            with connection.makefile("rb") as stream:
                for algorithm in ALGORITHMS:
                    self._run_sort(connection, stream, algorithm, results)
                send_message(connection, "complete")
        except (OSError, ProtocolError) as error:
            message = "等待客户端消息超时" if isinstance(error, socket.timeout) else str(error)
            self._publish(status="error", operation=None, error=message)
            print(f"本轮连接结束：{message}", flush=True)
            try:
                send_message(connection, "error", message=message)
            except OSError:
                pass
            return
        status = "complete" if all(result["verified"] for result in results.values()) else "failed"
        self._publish(status=status, operation=None)

    def _run_sort(self, connection, stream, algorithm, results):
        values = self.values
        expected_result = sorted(values)
        comparisons = swaps = 0
        results[algorithm] = {**empty_result(), "status": "running"}
        self._publish(current=algorithm, values=values, operation=None, results=results.copy())
        started = time.perf_counter()
        send_message(connection, "job", algorithm=algorithm, values=values)
        while True:
            message = read_message(stream)
            if message["type"] == "done":
                elapsed_ms = (time.perf_counter() - started) * 1000
                final_values = integer_list(message.get("values"))
                break

            values, kind, indices = self._validate_step(message, values)
            if kind == "compare":
                comparisons += 1
            else:
                swaps += 1
            results[algorithm] = {
                **results[algorithm],
                "comparisons": comparisons,
                "swaps": swaps,
                "steps": comparisons + swaps,
            }
            self._publish(
                values=values, operation={"kind": kind, "indices": indices}, results=results.copy(),
            )

        verified = final_values == expected_result and final_values == values
        result = {
            **results[algorithm],
            "status": "complete" if verified else "failed",
            "verified": verified,
            "elapsed_ms": elapsed_ms,
        }
        results[algorithm] = result
        self._publish(values=final_values, operation=None, results=results.copy())
        status = "验证通过" if verified else "验证失败"
        print(
            f"{ALGORITHMS[algorithm]}  {result['steps']} 步（比较 {comparisons}，"
            f"交换 {swaps}）  {elapsed_ms:.2f} ms  {status}  {final_values}",
            flush=True,
        )
        send_message(connection, "result", algorithm=algorithm, **result)

    @staticmethod
    def _validate_step(message, previous):
        if message["type"] != "step" or message.get("kind") not in ("compare", "swap"):
            raise ProtocolError("预期收到比较、交换或完成消息")
        indices = message.get("indices")
        if (
            not isinstance(indices, list)
            or len(indices) != 2
            or any(type(index) is not int or not 0 <= index < len(previous) for index in indices)
            or indices[0] == indices[1]
        ):
            raise ProtocolError("操作下标超出范围或重复")
        kind = message["kind"]
        values = integer_list(message.get("values"))
        expected = previous
        if kind == "swap":
            expected = previous.copy()
            left, right = indices
            expected[left], expected[right] = expected[right], expected[left]
        if values != expected:
            raise ProtocolError("列表与上报的操作不一致")
        return values, kind, indices

    def _handler(self):
        sort_server = self

        class DashboardHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/api/demo":
                    self.send_error(404)
                    return
                origin = self.headers.get("Origin")
                port = self.server.server_port
                if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                    self.send_error(403)
                    return
                accepted = sort_server.request_demo()
                self._respond(202 if accepted else 409, sort_server.snapshot(), "application/json; charset=utf-8")

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api/state":
                    body = sort_server.snapshot()
                    content_type = "application/json; charset=utf-8"
                elif path in ASSETS:
                    filename, content_type = ASSETS[path]
                    try:
                        body = (WEB_ROOT / filename).read_bytes()
                    except OSError:
                        self.send_error(404)
                        return
                else:
                    self.send_error(404)
                    return
                self._respond(200, body, content_type)

            def _respond(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        return DashboardHandler


def parse_values(text):
    try:
        values = [int(item.strip()) for item in text.split(",")] if text.strip() else []
        return integer_list(values)
    except ProtocolError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    except ValueError as error:
        raise argparse.ArgumentTypeError("用逗号分隔整数，例如 3,2,1") from error


def main():
    parser = argparse.ArgumentParser(description="发布 TCP 排序任务并启动本地网页")
    parser.add_argument("--tcp-host", default="127.0.0.1", help="TCP 监听地址")
    parser.add_argument("--tcp-port", type=int, default=5000, help="TCP 监听端口")
    parser.add_argument("--web-port", type=int, default=8000, help="本地网页端口")
    parser.add_argument("--values", type=parse_values, default=DEFAULT_VALUES, help="逗号分隔的整数")
    args = parser.parse_args()
    if not 0 <= args.tcp_port <= 65535 or not 0 <= args.web_port <= 65535:
        parser.error("端口须为 0 到 65535，0 表示自动分配")
    try:
        SortServer(args.values, args.tcp_host, args.tcp_port, args.web_port).serve()
    except KeyboardInterrupt:
        print("\n服务端已停止", flush=True)
    except OSError as error:
        parser.exit(1, f"服务端错误：{error}\n")


if __name__ == "__main__":
    main()
