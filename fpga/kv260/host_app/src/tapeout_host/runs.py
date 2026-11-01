"""Persistent run records and background execution used by the web UI."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .board import TapeoutBoard
from .compiler import CompileError, compile_source
from .protocol import ProgramImage


@dataclass
class RunRecord:
    run_id: str
    mode: str
    filename: str
    directory: Path
    state: str = "queued"
    events: list[dict] = field(default_factory=list)
    result: dict | None = None
    _condition: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def emit(self, kind: str, data: dict) -> None:
        event = {"type": kind, "data": data, "time": time.time()}
        with self._condition:
            self.events.append(event)
            self._condition.notify_all()

    def finish(self, state: str, result: dict) -> None:
        """Publish the terminal result before exposing the terminal state."""
        if state not in {"passed", "failed"}:
            raise ValueError("terminal state must be passed or failed")
        event = {
            "type": "result",
            "data": {"state": state, **result},
            "time": time.time(),
        }
        # The state and final event must become visible together. Previously,
        # stream() could observe a failed state after the compiler finished but
        # before its result event was appended, close early, and make the UI
        # report only "Event stream disconnected".
        with self._condition:
            self.result = result
            self.events.append(event)
            self.state = state
            self._condition.notify_all()

    def stream(self) -> Iterator[dict]:
        index = 0
        while True:
            outgoing: list[dict] = []
            finished = False
            with self._condition:
                while index >= len(self.events) and self.state not in {"passed", "failed"}:
                    self._condition.wait(timeout=10)
                    if index >= len(self.events):
                        outgoing.append(
                            {"type": "keepalive", "data": {}, "time": time.time()}
                        )
                        break
                while index < len(self.events):
                    outgoing.append(self.events[index])
                    index += 1
                if self.state in {"passed", "failed"}:
                    finished = index >= len(self.events)
            # Never yield while holding the condition lock: emitters must stay
            # able to append UART data even when the browser reads slowly.
            yield from outgoing
            if finished:
                return


class RunManager:
    def __init__(self, board: TapeoutBoard, storage: Path):
        self.board = board
        self.storage = storage
        self.storage.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def create(self, mode: str, uploaded, timeout: float) -> RunRecord:
        if mode not in {"source", "binary"}:
            raise ValueError("mode must be source or binary")
        if not 0.1 <= timeout <= 300:
            raise ValueError("timeout must be between 0.1 and 300 seconds")
        # Browsers normally strip the client path, but normalize both slash
        # styles before taking the basename so Windows uploads are safe too.
        filename = Path((uploaded.filename or "").replace("\\", "/")).name
        if not filename:
            raise ValueError("choose a file first")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        directory = self.storage / run_id
        directory.mkdir()
        input_path = directory / filename
        uploaded.save(input_path)
        record = RunRecord(run_id, mode, filename, directory)
        with self._lock:
            self._records[run_id] = record
        threading.Thread(
            target=self._execute,
            args=(record, input_path, timeout),
            name=f"tapeout-run-{run_id}",
            daemon=True,
        ).start()
        return record

    def get(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self._records.get(run_id)

    def _execute(self, record: RunRecord, input_path: Path, timeout: float) -> None:
        record.state = "running"
        record.emit("state", {"state": record.state})
        try:
            if record.mode == "source":
                record.emit("log", {"message": f"Compiling {record.filename} for RV32E"})
                build = compile_source(input_path, record.directory / "build")
                (record.directory / "compile.log").write_text(build.log, encoding="utf-8")
                record.emit("compiler", {"text": build.log})
                image = build.image
                shutil.copy2(build.binary_path, record.directory / "loaded.bin")
            else:
                record.emit("log", {"message": "Using uploaded binary without recompiling"})
                image = ProgramImage.from_path(input_path)
                shutil.copy2(input_path, record.directory / "loaded.bin")

            digest = hashlib.sha256(image.data).hexdigest()
            record.emit("image", {"bytes": len(image.data), "sha256": digest})
            board_result = self.board.run(image, timeout, record.emit)
            (record.directory / "uart.bin").write_bytes(board_result.uart)
            result = {
                "completed": board_result.completed,
                "timed_out": board_result.timed_out,
                "elapsed_seconds": board_result.elapsed_seconds,
                "status": board_result.status.as_dict(),
                "program_bytes": len(image.data),
                "sha256": digest,
                "uart_text": board_result.uart.decode("utf-8", errors="replace"),
            }
            final_state = "passed" if result["completed"] and not result["timed_out"] else "failed"
        except CompileError as exc:
            if exc.log:
                (record.directory / "compile.log").write_text(exc.log, encoding="utf-8")
                record.emit("compiler", {"text": exc.log})
            result = {"error": str(exc)}
            final_state = "failed"
        except Exception as exc:  # The error is persisted and displayed by the UI.
            result = {"error": str(exc)}
            final_state = "failed"

        (record.directory / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
        )
        record.finish(final_state, result)
