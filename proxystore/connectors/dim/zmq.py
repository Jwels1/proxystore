"""ZeroMQ-based distributed in-memory connector implementation."""
from __future__ import annotations

import asyncio
import atexit
import logging
import multiprocessing
import signal
import sys
import time
import uuid
from types import TracebackType
from typing import Any
from typing import NamedTuple
from typing import Sequence

if sys.version_info >= (3, 11):  # pragma: >=3.11 cover
    from typing import Self
else:  # pragma: <3.11 cover
    from typing_extensions import Self

try:
    import zmq
    import zmq.asyncio

    zmq_import_error = None
except ImportError as e:  # pragma: no cover
    zmq_import_error = e

import proxystore.utils as utils
from proxystore.connectors.dim.exceptions import ServerTimeoutError
from proxystore.connectors.dim.rpc import RPC
from proxystore.connectors.dim.rpc import RPCResponse
from proxystore.connectors.dim.utils import get_ip_address
from proxystore.serialize import deserialize
from proxystore.serialize import serialize

MAX_CHUNK_LENGTH_DEFAULT = 64 * 1024

logger = logging.getLogger(__name__)


class ZeroMQKey(NamedTuple):
    """Key to objects stored across `ZeroMQConnector`s."""

    key: str
    """Unique object key."""
    size: int
    """Object size in bytes."""
    peer: str
    """Peer where object is located."""


class ZeroMQConnector:
    """ZeroMQ-based distributed in-memory connector.

    Note:
        The first instance of this connector created on a process will
        spawn a [`ZeroMQServer`][proxystore.connectors.dim.zmq.ZeroMQServer]
        that will store data. Hence, this connector just acts as an interface
        to that server.

    Args:
        interface: The network interface to use.
        port: The desired port for the spawned server.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
        timeout: Timeout in seconds to try connecting to local server before
            spawning one.

    Raises:
        ServerTimeoutError: If a local server cannot be connected to within
            `timeout` seconds, and a new local server does not response within
            `timeout` seconds after being started.
    """

    def __init__(
        self,
        interface: str,
        port: int,
        chunk_length: int | None = None,
        timeout: float = 1,
    ) -> None:
        # ZMQ is not a default dependency so we don't want to raise
        # an error unless the user actually tries to use this code
        if zmq_import_error is not None:  # pragma: no cover
            raise zmq_import_error

        self.interface = interface
        self.port = port
        self.chunk_length = (
            MAX_CHUNK_LENGTH_DEFAULT if chunk_length is None else chunk_length
        )
        self.timeout = timeout

        self.host = get_ip_address(interface)
        self.addr = f'tcp://{self.host}:{self.port}'

        self.server: multiprocessing.Process | None
        try:
            logger.info(
                f'Connecting to local server (address={self.addr})...',
            )
            wait_for_server(self.host, self.port, self.timeout)
            logger.info(
                f'Connected to local server (address={self.addr})',
            )
        except ServerTimeoutError:
            logger.info(
                'Failed to connect to local server '
                f'(address={self.addr}, timeout={self.timeout})',
            )
            self.server = spawn_server(
                self.host,
                self.port,
                chunk_length=self.chunk_length,
                spawn_timeout=self.timeout,
            )
            logger.info(f'Spawned local server (address={self.addr})')
        else:
            self.server = None

        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _send_rpcs(self, rpcs: Sequence[RPC]) -> list[RPCResponse]:
        """Send an RPC request to the server.

        Args:
            rpcs: List of RPCs to invoke on local server.

        Returns:
            List of RPC responses.

        Raises:
            Exception: Any exception returned by the local server.
        """
        responses = []

        for rpc in rpcs:
            message = serialize(rpc)
            with self.socket.connect(self.addr):
                self.socket.send_multipart(
                    list(utils.chunk_bytes(message, self.chunk_length)),
                )
                logger.debug(
                    f'Sent {rpc.operation.upper()} RPC '
                    f'(key={rpc.key}, server={self.addr})',
                )
                result = b''.join(self.socket.recv_multipart())

            response = deserialize(result)
            logger.debug(
                f'Received {rpc.operation.upper()} RPC response '
                f'(key={response.key}, server={self.addr}, '
                f'exception={response.exception is not None})',
            )

            if response.exception is not None:
                raise response.exception

            assert rpc.operation == response.operation
            assert rpc.key == response.key

            responses.append(response)

        return responses

    def close(self, kill_server: bool = True) -> None:
        """Close the connector.

        Args:
            kill_server: Whether to kill the server process. If this instance
                did not spawn the local node's server process, this is a
                no-op.
        """
        if kill_server and self.server is not None:
            self.server.terminate()
            self.server.join()
            logger.info(
                'Terminated local server on connector close '
                f'(pid={self.server.pid})',
            )

        self.socket.close()
        self.context.term()
        logger.info('Closed ZMQ connector')

    def config(self) -> dict[str, Any]:
        """Get the connector configuration.

        The configuration contains all the information needed to reconstruct
        the connector object.
        """
        return {
            'interface': self.interface,
            'port': self.port,
            'chunk_length': self.chunk_length,
            'timeout': self.timeout,
        }

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ZeroMQConnector:
        """Create a new connector instance from a configuration.

        Args:
            config: Configuration returned by `#!python .config()`.
        """
        return cls(**config)

    def evict(self, key: ZeroMQKey) -> None:
        """Evict the object associated with the key.

        Args:
            key: Key associated with object to evict.
        """
        rpc = RPC(operation='evict', key=key.key, size=key.size)
        self._send_rpcs([rpc])

    def exists(self, key: ZeroMQKey) -> bool:
        """Check if an object associated with the key exists.

        Args:
            key: Key potentially associated with stored object.

        Returns:
            If an object associated with the key exists.
        """
        rpc = RPC(operation='exists', key=key.key, size=key.size)
        (response,) = self._send_rpcs([rpc])
        assert response.exists is not None
        return response.exists

    def get(self, key: ZeroMQKey) -> bytes | None:
        """Get the serialized object associated with the key.

        Args:
            key: Key associated with the object to retrieve.

        Returns:
            Serialized object or `None` if the object does not exist.
        """
        rpc = RPC(operation='get', key=key.key, size=key.size)
        (result,) = self._send_rpcs([rpc])
        return result.data

    def get_batch(self, keys: Sequence[ZeroMQKey]) -> list[bytes | None]:
        """Get a batch of serialized objects associated with the keys.

        Args:
            keys: Sequence of keys associated with objects to retrieve.

        Returns:
            List with same order as `keys` with the serialized objects or \
            `None` if the corresponding key does not have an associated object.
        """
        rpcs = [
            RPC(operation='get', key=key.key, size=key.size) for key in keys
        ]
        responses = self._send_rpcs(rpcs)
        return [r.data for r in responses]

    def put(self, obj: bytes) -> ZeroMQKey:
        """Put a serialized object in the store.

        Args:
            obj: Serialized object to put in the store.

        Returns:
            Key which can be used to retrieve the object.
        """
        key = ZeroMQKey(key=str(uuid.uuid4()), size=len(obj), peer=self.addr)
        rpc = RPC(operation='put', key=key.key, size=key.size, data=obj)
        self._send_rpcs([rpc])
        return key

    def put_batch(self, objs: Sequence[bytes]) -> list[ZeroMQKey]:
        """Put a batch of serialized objects in the store.

        Args:
            objs: Sequence of serialized objects to put in the store.

        Returns:
            List of keys with the same order as `objs` which can be used to \
            retrieve the objects.
        """
        keys = [
            ZeroMQKey(key=str(uuid.uuid4()), size=len(obj), peer=self.addr)
            for obj in objs
        ]
        rpcs = [
            RPC(operation='put', key=key.key, size=key.size, data=obj)
            for key, obj in zip(keys, objs)
        ]
        self._send_rpcs(rpcs)
        return keys


class ZeroMQServer:
    """ZeroMQServer implementation."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def evict(self, key: str) -> None:
        """Evict the object associated with the key.

        Args:
            key: Key associated with object to evict.
        """
        self.data.pop(key, None)

    def exists(self, key: str) -> bool:
        """Check if an object associated with the key exists.

        Args:
            key: Key potentially associated with stored object.

        Returns:
            If an object associated with the key exists.
        """
        return key in self.data

    def get(self, key: str) -> bytes | None:
        """Get the serialized object associated with the key.

        Args:
            key: Key associated with the object to retrieve.

        Returns:
            Data or `None` if no data associated with the key exists.
        """
        return self.data.get(key, None)

    def put(self, key: str, data: bytes) -> None:
        """Put data in the store.

        Args:
            key: Key associated with data.
            data: Data to put in the store.
        """
        self.data[key] = data

    def handle_rpc(self, rpc: RPC) -> RPCResponse:
        """Process an RPC request.

        Args:
            rpc: Client RPC to process.

        Returns:
            Response containing result or an exception if the operation failed.
        """
        response: RPCResponse
        try:
            if rpc.operation == 'exists':
                exists = self.exists(rpc.key)
                response = RPCResponse(
                    'exists',
                    key=rpc.key,
                    size=rpc.size,
                    exists=exists,
                )
            elif rpc.operation == 'evict':
                self.evict(rpc.key)
                response = RPCResponse('evict', key=rpc.key, size=rpc.size)
            elif rpc.operation == 'get':
                data = self.get(rpc.key)
                response = RPCResponse(
                    'get',
                    key=rpc.key,
                    size=rpc.size,
                    data=data,
                )
            elif rpc.operation == 'put':
                assert rpc.data is not None
                self.put(rpc.key, rpc.data)
                response = RPCResponse('put', key=rpc.key, size=rpc.size)
            else:
                raise AssertionError('Unreachable.')
        except Exception as e:
            response = RPCResponse(
                rpc.operation,
                key=rpc.key,
                size=rpc.size,
                exception=e,
            )
        return response


async def run_server(
    host: str,
    port: int,
    chunk_length: int | None = None,
) -> None:
    """Listen and reply to RPCs from clients.

    Warning:
        This function does not return until SIGINT or SIGTERM is received.

    Args:
        host: IP address the server should bind to.
        port: Port the server should listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
    """
    loop = asyncio.get_running_loop()
    close_future = loop.create_future()

    loop.add_signal_handler(signal.SIGINT, close_future.set_result, None)
    loop.add_signal_handler(signal.SIGTERM, close_future.set_result, None)

    server = ZeroMQServer()
    chunk_length = (
        MAX_CHUNK_LENGTH_DEFAULT if chunk_length is None else chunk_length
    )

    context = zmq.asyncio.Context()
    socket = context.socket(zmq.REP)
    socket.setsockopt(zmq.RCVTIMEO, 100)

    with socket.bind(f'tcp://{host}:{port}'):
        while not close_future.done():
            try:
                rpc_parts = await socket.recv_multipart()
            except zmq.error.Again:
                continue

            rpc_bytes = b''.join(rpc_parts)

            if rpc_bytes == b'ping':
                await socket.send(b'pong')
                continue

            rpc: RPC = deserialize(rpc_bytes)
            response = server.handle_rpc(rpc)

            message = serialize(response)
            await socket.send_multipart(
                list(utils.chunk_bytes(message, chunk_length)),
            )

    socket.close()
    context.term()


def start_server(
    host: str,
    port: int,
    chunk_length: int | None = None,
) -> None:
    """Run a local server.

    Note:
        This function creates an event loop and executes
        [`run_server()`][proxystore.connectors.dim.zmq.run_server] within
        that loop.

    Args:
        host: IP address the server should bind to.
        port: Port the server should listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
    """
    asyncio.run(run_server(host, port, chunk_length))


def spawn_server(
    host: str,
    port: int,
    *,
    chunk_length: int | None = None,
    spawn_timeout: float = 5.0,
    kill_timeout: float | None = 1.0,
) -> multiprocessing.Process:
    """Spawn a local server running in a separate process.

    Note:
        An `atexit` callback is registered which will terminate the spawned
        server process when the calling process exits.

    Args:
        host: IP address the server should bind to.
        port: Port the server will listen on.
        chunk_length: Message chunk size in bytes. Defaults to
            `MAX_CHUNK_LENGTH_DEFAULT`.
        spawn_timeout: Max time in seconds to wait for the server to start.
        kill_timeout: Max time in seconds to wait for the server to shutdown
            on exit.

    Returns:
        The process that the server is running in.
    """
    server_process = multiprocessing.Process(
        target=start_server,
        args=(host, port, chunk_length),
    )
    server_process.start()

    def _kill_on_exit() -> None:  # pragma: no cover
        server_process.terminate()
        server_process.join(timeout=kill_timeout)
        if server_process.is_alive():
            server_process.kill()
            server_process.join()
        logger.debug(
            'Server terminated on parent process exit '
            f'(pid={server_process.pid})',
        )

    atexit.register(_kill_on_exit)
    logger.debug('Registered server cleanup atexit callback')

    wait_for_server(host, port, timeout=spawn_timeout)
    logger.debug(
        f'Server started (host={host}, port={port}, pid={server_process.pid})',
    )

    return server_process


def wait_for_server(host: str, port: int, timeout: float = 0.1) -> None:
    """Wait until the server responds.

    Args:
        host: Host of the server to ping.
        port: Port of the server to ping.
        timeout: Max time in seconds to wait for server response.

    Raises:
        ServerTimeoutError: If the server does not respond within the timeout.
    """
    start = time.time()
    context = zmq.Context()
    socket = context.socket(zmq.REQ)
    socket.setsockopt(zmq.LINGER, 0)
    socket.connect(f'tcp://{host}:{port}')
    socket.send(b'ping')

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while time.time() - start < timeout:
        # Poll for 100ms
        event = poller.poll(100)
        if len(event) != 0:
            response = socket.recv()
            assert response == b'pong'
            socket.close()
            return

    socket.close()

    raise ServerTimeoutError(
        f'Failed to connect to server within timeout ({timeout} seconds).',
    )
