"""A local HTTP server for a transport test: a request costs the request and nothing else.

`http.server.HTTPServer.server_bind` reverse-resolves the address it bound (`socket.getfqdn`), and
`shutdown()` waits up to its poll interval for the serving thread. Neither is the behaviour under
test, and both are the machine's: a runner whose resolver stalls on `127.0.0.1` made a module of a
dozen cases cost two minutes (measured: five seconds a case for one stub lookup). So the server names
itself without a lookup and polls a hundredth of a second.
"""

import socketserver
import threading
from http.server import ThreadingHTTPServer


class LocalServer(ThreadingHTTPServer):
    """A `ThreadingHTTPServer` on a named address, serving in the background until `stop`."""

    def server_bind(self):
        """Bind without the name lookup `HTTPServer` does: the address is already the answer."""
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host.decode() if isinstance(host, (bytes, bytearray)) else host
        self.server_port = port

    def serve_in_background(self):
        """Start serving on a daemon thread and return it, so `stop` can close the socket."""
        thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": 0.01})
        thread.daemon = True
        thread.start()
        return thread

    def stop(self, thread):
        """Stop serving and close the port: the half-second `shutdown` poll is not the test's cost."""
        self.shutdown()
        self.server_close()
        thread.join(timeout=10)
