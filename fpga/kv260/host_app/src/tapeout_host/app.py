"""Flask entry point for the standalone KV260 application."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from waitress import serve

from .board import TapeoutBoard
from .pynq_overlay import PynqHardware
from .runs import RunManager


def create_app(manager: RunManager) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.post("/api/runs")
    def create_run():
        try:
            uploaded = request.files.get("program")
            if uploaded is None:
                raise ValueError("missing program upload")
            record = manager.create(
                request.form.get("mode", "source"),
                uploaded,
                float(request.form.get("timeout", "5")),
            )
            return jsonify({"run_id": record.run_id}), 202
        except (ValueError, OSError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/runs/<run_id>")
    def get_run(run_id: str):
        record = manager.get(run_id)
        if record is None:
            return jsonify({"error": "unknown run"}), 404
        return jsonify(
            {
                "run_id": record.run_id,
                "mode": record.mode,
                "filename": record.filename,
                "state": record.state,
                "result": record.result,
            }
        )

    @app.get("/api/runs/<run_id>/events")
    def events(run_id: str):
        record = manager.get(run_id)
        if record is None:
            return jsonify({"error": "unknown run"}), 404

        @stream_with_context
        def generate():
            for event in record.stream():
                yield "data: " + json.dumps(event) + "\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile/load RV32E programs and stream their UART output"
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--overlay",
        type=Path,
        default=Path("system_wrapper.bit"),
        help="PYNQ bitstream; a same-name .hwh file must be beside it",
    )
    parser.add_argument(
        "--overlay-only",
        action="store_true",
        help="load the overlay and inspect its metadata without touching AXI",
    )
    parser.add_argument(
        "--runs",
        type=Path,
        default=Path("/var/lib/tapeout-host/runs"),
        help="directory used to preserve source, binaries and results",
    )
    args = parser.parse_args()

    if args.overlay_only:
        print(f"Loading PYNQ overlay without AXI access: {args.overlay}", flush=True)
        hardware = PynqHardware(args.overlay, map_regions=False)
        try:
            names = ", ".join(sorted(hardware.overlay.ip_dict))
            print(f"PASS: overlay loaded; HWH instances: {names}", flush=True)
        finally:
            hardware.close()
        return

    print(f"Loading PYNQ overlay: {args.overlay}", flush=True)
    board = TapeoutBoard(args.overlay)
    print("Overlay loaded and AXI GPIO/UART initialized", flush=True)
    manager = RunManager(board, args.runs)
    app = create_app(manager)
    try:
        serve(app, host=args.host, port=args.port, threads=4)
    finally:
        board.close()


if __name__ == "__main__":
    main()
