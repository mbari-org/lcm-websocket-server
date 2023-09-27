import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

from lcm_websocket_server.log import get_logger
logger = get_logger(__name__)


class HTTPRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler.
    """
    def do_GET(self):
        """
        Handle GET requests.
        """
        logger.debug(f"Received HTTP request: {self.command} {self.path}")
        
        # Set the headers/status code
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        
        # Create the JSON body
        body = json.dumps({"count": len(self.server.clients)})
        logger.debug(f"Sending HTTP response: {body}")
        
        # Send the response
        self.wfile.write(body.encode("utf-8"))
    
    def log_message(self, format, *args):
        """
        Override the default log_message method to redirect to application logger.
        
        Args:
            format: Format string.
            *args: Format arguments.
        """
        formatted_message = format % args
        logger.info(f"{self.address_string()} - {formatted_message}")


class LCMWebSocketHTTPServer:
    """
    LCM WebSocket HTTP server.
    """
    def __init__(self, host: str, port: int, ws_clients: list):
        """
        Initialize the LCM WebSocket HTTP server.
        
        Args:
            host: Host to listen on.
            port: Port to listen on.
            ws_clients: List of WebSocket clients.
        """
        self.host = host
        self.port = port
        self.ws_clients = ws_clients
        self.httpd = None
        self.thread = None
    
    def start(self):
        """
        Start the LCM WebSocket HTTP server.
        """
        logger.debug(f"Starting HTTP server at http://{self.host}:{self.port}...")
        self.httpd = HTTPServer((self.host, self.port), HTTPRequestHandler)
        self.httpd.clients = self.ws_clients
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"HTTP server at http://{self.host}:{self.port} started")
    
    def stop(self):
        """
        Stop the LCM WebSocket HTTP server.
        """
        logger.debug(f"Shutting down HTTP server at http://{self.host}:{self.port}...")
        self.httpd.shutdown()
        self.thread.join()
        logger.info(f"HTTP server at http://{self.host}:{self.port} closed")
    
    def __enter__(self):
        """
        Enter the context manager.
        
        Returns:
            LCM WebSocket HTTP server.
        """
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Exit the context manager.
        
        Args:
            exc_type: Exception type.
            exc_val: Exception value.
            exc_tb: Exception traceback.
        """
        self.stop()