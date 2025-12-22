#!/usr/bin/env python3

"""Serve a run directory with an auto-regenerating gallery.

This is a lightweight local preview server intended for browsing synthetic UI
outputs while a run is still in progress.

Features:
- Serves files from out/<run_id>/ (renders, gallery, etc.)
- Regenerates gallery/index.html on each request to / or /gallery/index.html
- Supports minimum score filtering (e.g. only show >= 9.0 winners)
"""

from __future__ import annotations

import argparse
import http.server
import time
from pathlib import Path

from titan_factory.gallery import build_gallery


class GalleryHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler that regenerates the gallery before serving it."""

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        # Always route "/" to the gallery.
        if self.path in ("/", "/index.html"):
            self.path = "/gallery/index.html"

        # Regenerate gallery on request for the gallery page.
        if self.path.startswith("/gallery/index.html"):
            self._regenerate_gallery()

        return super().do_GET()

    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib signature
        # Keep logs concise and timestamped.
        ts = time.strftime("%H:%M:%S")
        msg = format % args
        print(f"[{ts}] {self.client_address[0]} {msg}")

    def _regenerate_gallery(self) -> None:
        run_dir = self.server.run_dir  # type: ignore[attr-defined]
        min_score = self.server.min_score  # type: ignore[attr-defined]
        try:
            build_gallery(run_dir, min_score=min_score)
        except Exception as e:
            # Don't fail the HTTP request; just log and serve stale gallery.
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] WARN: failed to regenerate gallery: {e}")


class GalleryHTTPServer(http.server.ThreadingHTTPServer):
    """Threading HTTP server with attached run metadata."""

    def __init__(self, server_address, RequestHandlerClass, *, run_dir: Path, min_score: float | None):
        super().__init__(server_address, RequestHandlerClass)
        self.run_dir = run_dir
        self.min_score = min_score


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve TITAN run gallery on localhost.")
    parser.add_argument(
        "--run-dir",
        required=True,
        help="Path to the run directory (e.g. out/run_... or out/oss20_...)",
    )
    parser.add_argument("--port", type=int, default=3001, help="Port to serve on (default: 3001)")
    parser.add_argument(
        "--min-score",
        type=float,
        default=9.0,
        help="Minimum winner score to include in gallery (default: 9.0)",
    )

    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    # Pre-generate once on startup.
    build_gallery(run_dir, min_score=args.min_score)

    handler = lambda *h_args, **h_kwargs: GalleryHandler(  # noqa: E731
        *h_args, directory=str(run_dir), **h_kwargs
    )

    with GalleryHTTPServer(("0.0.0.0", args.port), handler, run_dir=run_dir, min_score=args.min_score) as httpd:
        print(f"Serving {run_dir} at http://localhost:{args.port}/ (min_score={args.min_score})")
        httpd.serve_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
