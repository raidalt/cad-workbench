"""Local project worker. Private Unix socket RPC carries JSON metadata and binary meshes."""
from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .errors import CadError


class ProjectWorker:
    def __init__(self, root: Path, *, idle_seconds: float = 300):
        self.directory = tempfile.TemporaryDirectory(prefix="cad-w-", dir="/tmp")
        self.socket = str(Path(self.directory.name) / "rpc")
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "cad.project_worker", str(root), self.socket, str(os.getpid()), str(idle_seconds)],
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        except BaseException:
            self.directory.cleanup()
            raise

        self._reaper = threading.Thread(target=self._reap, name="cad-worker-reaper", daemon=True)
        self._reaper.start()

    def _reap(self):
        self.process.wait()
        self.directory.cleanup()

    @contextlib.contextmanager
    def _call(self, message):
        deadline = time.monotonic() + 30
        with socket.socket(socket.AF_UNIX) as sock:
            while True:
                try:
                    sock.connect(self.socket)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        raise CadError("WORKER_UNAVAILABLE", "Project worker stopped before accepting the request")
                    time.sleep(.04)
            sock.sendall(json.dumps(message).encode() + b"\n")
            with sock.makefile("rb") as stream:
                yield stream

    def request(self, method, path, query, body, content_type):
        with self._call([method, path, query, body, content_type]) as stream:
            header = stream.readline()
            if not header:
                raise CadError("WORKER_CRASHED", "Project worker exited during this operation")
            code, size, ctype, headers = json.loads(header)
            data = stream.read(size)
            if len(data) != size:
                raise CadError("WORKER_CRASHED", "Project worker returned an incomplete result")
            return code, data, ctype, headers

    def events(self):
        with self._call(["events"]) as stream:
            # Forward complete SSE lines as they arrive, including heartbeats.
            yield from stream

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self._reaper.join()


def run(root, address, parent, idle_seconds):
    from .serve import ProjectAPI, Service, event_stream

    service = Service(root)
    api = ProjectAPI(service, library=True)

    class WorkerServer(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    active = 0
    last_used = time.monotonic()
    lock = threading.Lock()

    class Operation(socketserver.StreamRequestHandler):
        def handle(self):
            nonlocal active, last_used
            with lock:
                active += 1
            try:
                args = json.loads(self.rfile.readline())
                if args == ["events"]:
                    for chunk in event_stream(service.hub):
                        self.wfile.write(chunk)
                    return
                try:
                    code, data, ctype, headers = api.request(*args)
                except CadError as error:
                    code = 404 if error.code == "NOT_FOUND" else 409 if error.code.startswith("STALE_") else 400
                    code, data, ctype, headers = api._json({"ok": False, "error": error.to_dict()}, code)
                except Exception as error:
                    code, data, ctype, headers = api._json({"ok": False, "error": {
                        "code": "INTERNAL", "message": str(error)}}, 500)
                self.wfile.write(json.dumps([code, len(data), ctype, headers]).encode() + b"\n")
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with lock:
                    active -= 1
                    last_used = time.monotonic()

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    with WorkerServer(address, Operation) as server:
        server.timeout = 1
        service.start_engine()
        try:
            while not stopping.is_set() and os.getppid() == parent:
                server.handle_request()
                with lock:
                    idle = not active and time.monotonic() - last_used > idle_seconds
                if idle and not service.builder.building and not service.chat.running:
                    break
        finally:
            service.stop()


if __name__ == "__main__":
    run(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
