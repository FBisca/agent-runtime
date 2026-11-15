"""The local server a transport test binds pays the request, not the machine's resolver or its poll.

`HTTPServer.server_bind` reverse-resolves the address it bound and `shutdown()` waits out its poll
interval; both were the cost of a module that starts a server per case (111 s of a macOS job).
"""

import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

import pytest
from local_http import LocalServer

pytestmark = pytest.mark.integration


class LocalServerTests(unittest.TestCase):
    def test_the_server_never_asks_the_resolver_for_its_own_name(self):
        with mock.patch("socket.getfqdn", side_effect=AssertionError("a lookup is not the test")):
            server = LocalServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            thread = server.serve_in_background()
            try:
                self.assertEqual(server.server_name, "127.0.0.1")
                self.assertEqual(server.server_port, server.server_address[1])
            finally:
                server.stop(thread)
