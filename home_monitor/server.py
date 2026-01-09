"""
Simple HTTP server to trigger data fetching via HTTP requests.
"""

import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

from home_monitor.fetcher import fetch_all_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class FetcherHandler(BaseHTTPRequestHandler):
    """HTTP request handler that triggers data fetching."""

    def _send_json_response(self, status: int, body: dict):
        """Send JSON response with common headers."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        import json

        self.wfile.write(json.dumps(body).encode() + b"\n")

    def do_GET(self):
        """Reject GET requests - only POST is allowed."""
        self._send_json_response(405, {"status": "error", "message": "Method not allowed."})

    def do_POST(self):
        """Handle POST requests to trigger a data fetch."""
        try:
            logger.info("Received fetch request")
            fetch_all_data()
            self._send_json_response(200, {"status": "success"})
            logger.info("Fetch request completed successfully")
        except Exception as e:
            logger.error(f"Error handling fetch request: {e}", exc_info=True)
            self._send_json_response(500, {"status": "error", "message": str(e)})

    def log_message(self, format, *args):
        """Override to use our logger instead of stderr."""
        logger.info(
            "%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args)
        )


def run_server(port: int = 8080):
    """
    Run the HTTP server.

    Args:
        port: Port to listen on (default 8080, can be overridden via PORT env var)
    """
    server_port = int(os.getenv("PORT", port))
    server = HTTPServer(("", server_port), FetcherHandler)
    logger.info(f"Starting server on port {server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down server")
        server.shutdown()


if __name__ == "__main__":
    run_server()
