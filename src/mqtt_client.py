"""A small MQTT 3.1.1 client (QoS 0, optional TLS) over the standard library."""

import select
import socket
import ssl
import struct
import threading
import time

CONNECT, CONNACK, PUBLISH, PUBACK, SUBSCRIBE, SUBACK, PINGREQ, PINGRESP, DISCONNECT = 1, 2, 3, 4, 8, 9, 12, 13, 14


class MqttError(Exception):
    """A broker that refuses, or a connection that breaks; str() is an error code."""


def _string(text):
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    return struct.pack("!H", len(data)) + data


def _length(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def packet(kind, flags, body=b""):
    return bytes([(kind << 4) | flags]) + _length(len(body)) + body


def connect_packet(client_id, keepalive, username="", password="", will=None):
    """CONNECT, clean session; `will` = (topic, payload, retain)."""
    flags, payload = 0x02, _string(client_id)
    if will:
        topic, message, retain = will
        flags |= 0x04 | (0x20 if retain else 0)
        payload += _string(topic) + _string(message)
    if username:
        flags |= 0x80
        payload += _string(username)
        if password:
            flags |= 0x40
            payload += _string(password)
    return packet(CONNECT, 0, _string("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive) + payload)


def publish_packet(topic, payload, retain=False):
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    return packet(PUBLISH, 0x01 if retain else 0, _string(topic) + data)


def subscribe_packet(packet_id, topics):
    return packet(SUBSCRIBE, 0x02, struct.pack("!H", packet_id) + b"".join(_string(t) + b"\x00" for t in topics))


def split_packets(buffer):
    """The whole packets at the start of `buffer` as (kind, flags, body), and what is left."""
    out = []
    while len(buffer) >= 2:
        length, multiplier, i = 0, 1, 1
        while True:
            if i >= len(buffer):
                return out, buffer
            byte = buffer[i]
            length += (byte & 0x7F) * multiplier
            multiplier *= 128
            i += 1
            if not byte & 0x80:
                break
            if i > 4:
                raise MqttError("bad_packet")
        if len(buffer) < i + length:
            return out, buffer
        out.append((buffer[0] >> 4, buffer[0] & 0x0F, bytes(buffer[i:i + length])))
        buffer = buffer[i + length:]
    return out, buffer


class Client:
    def __init__(self, host, port=1883, client_id="rukebox", username="", password="", tls=False,
                 keepalive=60, will=None):
        self.host, self.port, self.tls = host, int(port), bool(tls)
        self.client_id, self.username, self.password = client_id, username, password
        self.keepalive, self.will = keepalive, will
        self.sock = None
        self._buffer = b""
        self._sent_at = 0.0
        self._send_lock = threading.Lock()
        self._packet_id = 0

    def connect(self, timeout=10):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except socket.gaierror:
            raise MqttError("unknown_host")
        except OSError:
            raise MqttError("unreachable")
        if self.tls:
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
            except (ssl.SSLError, OSError):
                sock.close()
                raise MqttError("tls_failed")
        self.sock = sock
        self._send(connect_packet(self.client_id, self.keepalive, self.username, self.password, self.will))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for kind, _, body in self._read(deadline - time.monotonic()):
                if kind == CONNACK:
                    code = body[1] if len(body) > 1 else 255
                    if code == 0:
                        return
                    self.close()
                    raise MqttError("auth_refused" if code in (4, 5) else "refused")
        self.close()
        raise MqttError("timeout")

    def _send(self, data):
        if not self.sock:
            raise MqttError("disconnected")
        with self._send_lock:
            try:
                self.sock.sendall(data)
            except OSError:
                raise MqttError("disconnected")
            self._sent_at = time.monotonic()

    def _read(self, timeout):
        """The packets that arrive within `timeout` seconds (perhaps none)."""
        if not self.sock:
            raise MqttError("disconnected")
        pending = getattr(self.sock, "pending", lambda: 0)()
        if not pending:
            ready, _, _ = select.select([self.sock], [], [], max(0.0, timeout))
            if not ready:
                return []
        try:
            data = self.sock.recv(65536)
        except (ssl.SSLWantReadError, socket.timeout):
            return []
        except OSError:
            raise MqttError("disconnected")
        if not data:
            raise MqttError("disconnected")
        packets, self._buffer = split_packets(self._buffer + data)
        return packets

    def publish(self, topic, payload, retain=False):
        self._send(publish_packet(topic, payload, retain))

    def subscribe(self, topics):
        self._packet_id = self._packet_id % 65535 + 1
        self._send(subscribe_packet(self._packet_id, topics))

    def poll(self, timeout=1.0):
        """The messages received within `timeout`, as (topic, payload text); keeps the link alive."""
        if time.monotonic() - self._sent_at > self.keepalive / 2:
            self._send(packet(PINGREQ, 0))
        messages = []
        for kind, flags, body in self._read(timeout):
            if kind != PUBLISH or len(body) < 2:
                continue
            size = struct.unpack("!H", body[:2])[0]
            topic, rest = body[2:2 + size].decode("utf-8", "replace"), body[2 + size:]
            qos = (flags >> 1) & 0x03
            if qos:
                packet_id, rest = rest[:2], rest[2:]
                if qos == 1:
                    self._send(packet(PUBACK, 0, packet_id))
            messages.append((topic, rest.decode("utf-8", "replace")))
        return messages

    def close(self):
        if self.sock:
            try:
                with self._send_lock:
                    self.sock.sendall(packet(DISCONNECT, 0))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
