"""Authenticated local command channel for the single running GUI instance."""
from __future__ import annotations

import json
from typing import Any, Callable

from core import single_instance

ALLOWED_COMMANDS = frozenset({"show", "previous", "next", "random", "jump", "set_wallpaper", "quit"})
MAX_MESSAGE_BYTES = 64 * 1024


def _message(command: str, payload: Any = None, *, identity: dict[str, Any] | None = None) -> bytes:
    identity = identity or single_instance.read_identity()
    token = str(identity.get("ipc_token") or "")
    if not token:
        raise RuntimeError("The running instance has not published an IPC token")
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"Unsupported local command: {command}")
    raw = json.dumps(
        {"version": 1, "token": token, "command": command, "payload": payload},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(raw) > MAX_MESSAGE_BYTES:
        raise ValueError("Local command is too large")
    return raw


def send_command(
    command: str,
    payload: Any = None,
    *,
    timeout_ms: int = 1200,
    identity: dict[str, Any] | None = None,
) -> bool:
    """Send a command to the primary process using blocking QLocalSocket APIs."""
    try:
        from PySide6.QtNetwork import QLocalSocket

        identity = identity or single_instance.read_identity()
        endpoint = str(identity.get("endpoint") or single_instance.endpoint_name())
        socket = QLocalSocket()
        socket.connectToServer(endpoint)
        if not socket.waitForConnected(max(1, int(timeout_ms))):
            return False
        data = _message(command, payload, identity=identity)
        if socket.write(data) < 0:
            socket.abort()
            return False
        if not socket.waitForBytesWritten(max(1, int(timeout_ms))):
            socket.abort()
            return False
        if not socket.waitForReadyRead(max(1, int(timeout_ms))):
            socket.abort()
            return False
        response = bytes(socket.readAll()).split(b"\n", 1)[0]
        try:
            accepted = bool(json.loads(response.decode("utf-8")).get("ok"))
        except Exception:
            accepted = False
        socket.disconnectFromServer()
        if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            socket.waitForDisconnected(min(250, max(1, int(timeout_ms))))
        return accepted
    except Exception:
        return False


try:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtNetwork import QLocalServer
except Exception:  # pragma: no cover - import-safe without GUI dependencies
    QObject = object  # type: ignore[assignment]
    Signal = None  # type: ignore[assignment]
    QLocalServer = None  # type: ignore[assignment]


if Signal is not None:
    class LocalCommandServer(QObject):
        command_received = Signal(str, object)

        def __init__(self, handler: Callable[[str, Any], None] | None = None, parent=None):
            super().__init__(parent)
            self._handler = handler
            self._server = QLocalServer(self)
            try:
                self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            except Exception:
                pass
            self._server.newConnection.connect(self._accept_connections)
            self.command_received.connect(self._dispatch)

        def start(self) -> bool:
            identity = single_instance.current_identity()
            endpoint = str(identity.get("endpoint") or single_instance.endpoint_name())
            # Only the lock owner constructs this server, so removing a stale
            # endpoint cannot disconnect a valid competing instance.
            QLocalServer.removeServer(endpoint)
            return bool(self._server.listen(endpoint))

        def close(self) -> None:
            endpoint = self._server.serverName()
            self._server.close()
            if endpoint:
                QLocalServer.removeServer(endpoint)

        def _accept_connections(self) -> None:
            while self._server.hasPendingConnections():
                socket = self._server.nextPendingConnection()
                if socket is None:
                    continue
                socket.setProperty("shang_buffer", b"")
                socket.readyRead.connect(lambda s=socket: self._read_socket(s))
                socket.disconnected.connect(socket.deleteLater)
                if socket.bytesAvailable():
                    self._read_socket(socket)

        def _read_socket(self, socket) -> None:
            existing = bytes(socket.property("shang_buffer") or b"")
            chunk = bytes(socket.readAll())
            data = existing + chunk
            if len(data) > MAX_MESSAGE_BYTES:
                socket.abort()
                return
            if b"\n" not in data:
                socket.setProperty("shang_buffer", data)
                return
            line, _remainder = data.split(b"\n", 1)
            accepted = False
            command = ""
            payload = None
            try:
                message = json.loads(line.decode("utf-8"))
                identity = single_instance.current_identity()
                if isinstance(message, dict):
                    # v1.4.4: Use hmac.compare_digest for timing-attack-resistant
                    # token comparison (Python docs recommend this over ==).
                    import hmac as _hmac
                    received_token = str(message.get("token") or "")
                    expected_token = str(identity.get("ipc_token") or "")
                    token_ok = _hmac.compare_digest(received_token, expected_token)
                    command = str(message.get("command") or "")
                    if token_ok and command in ALLOWED_COMMANDS:
                        payload = message.get("payload")
                        accepted = True
            except Exception:
                accepted = False
            try:
                # v1.4.4: Limit IPC response size to prevent resource exhaustion
                response = json.dumps({"ok": accepted}, separators=(",", ":"))
                if len(response) > 4096:
                    response = json.dumps({"ok": False, "error": "response_too_large"}, separators=(",", ":"))
                socket.write(response.encode("utf-8") + b"\n")
                socket.flush()
                socket.disconnectFromServer()
            except Exception:
                socket.abort()
            if accepted:
                self.command_received.emit(command, payload)

        def _dispatch(self, command: str, payload: Any) -> None:
            if self._handler is not None:
                self._handler(command, payload)
else:
    class LocalCommandServer:  # pragma: no cover
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("PySide6 QtNetwork is required for local IPC")
