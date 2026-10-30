"""Private per-wait Unix datagram listeners; messages are wake hints only."""
import errno
import hashlib
import os
import socket
import tempfile
import uuid
from pathlib import Path


def endpoints(directory, create=False):
    # macOS Unix paths are short; a private hashed root avoids long HOME/TMPDIR paths.
    root = Path("/tmp" if Path("/tmp").is_dir() else tempfile.gettempdir()) / ("agent-runtime-%s" % os.getuid())
    digest = hashlib.sha256(str(Path(directory).resolve()).encode()).hexdigest()[:20]
    target = root / digest
    for path in (root, target):
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        if path.exists():
            if path.is_symlink() or path.stat().st_uid != os.getuid():
                raise PermissionError("IPC directory must be owned by the current user")
            path.chmod(0o700)
    return target


def notify(directory):
    """Best effort after commit. Recovery never depends on successful notification."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
            sender.setblocking(False)
            for path in endpoints(directory).glob("*.sock"):
                try:
                    sender.sendto(b"1", str(path))
                except OSError as error:
                    if error.errno == errno.ECONNREFUSED:
                        path.unlink(missing_ok=True)  # A crashed recipient left an endpoint.
    except OSError:
        pass


class Listener:
    def __init__(self, directory):
        self.path = endpoints(directory, create=True) / (uuid.uuid4().hex[:20] + ".sock")
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)

    def __enter__(self):
        try:
            self.socket.bind(str(self.path))
            self.path.chmod(0o600)
            self.socket.setblocking(False)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def wake(self):
        try:
            self.socket.sendto(b"1", str(self.path))
        except OSError:
            pass

    def wait(self, seconds):
        import select
        if select.select([self.socket], [], [], seconds)[0]:
            try:
                for _ in range(64):  # A hint flood cannot postpone cancellation indefinitely.
                    self.socket.recv(4096)
            except BlockingIOError:
                pass

    def __exit__(self, *_):
        self.socket.close()
        self.path.unlink(missing_ok=True)
