#!/usr/bin/python3

# Tests for server.py and client.py
# Uses real TCP loopback connections (127.0.0.1, OS-assigned port).

import unittest
import socket
import threading
import struct
import tempfile
import os
import getpass
import shutil
import importlib.util


# ── module loading ────────────────────────────────────────────────────────────

def _load(name, filename):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(name, os.path.join(here, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

srv = _load("server", "server.py")
cli = _load("client", "client.py")


# ── helpers ───────────────────────────────────────────────────────────────────

def pair():
    """Return (server_session, client_session) over a real TCP loopback connection."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    result = [None]
    def _accept():
        conn, addr = listener.accept()
        result[0] = (conn, addr)

    t = threading.Thread(target=_accept, daemon=True)
    t.start()

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))

    t.join(timeout=3)
    listener.close()

    return result[0], (client_sock, ("127.0.0.1", port))


# ── encode / decode ───────────────────────────────────────────────────────────

class TestEncoding(unittest.TestCase):
    def test_roundtrip_text(self):
        self.assertEqual(cli.decode(cli.encode(b"hello world")), b"hello world")

    def test_roundtrip_empty(self):
        self.assertEqual(cli.decode(cli.encode(b"")), b"")

    def test_roundtrip_all_byte_values(self):
        data = bytes(range(256))
        self.assertEqual(cli.decode(cli.encode(data)), data)

    def test_roundtrip_large(self):
        data = os.urandom(1024 * 1024)  # 1 MB
        self.assertEqual(cli.decode(cli.encode(data)), data)

    def test_roundtrip_null_bytes(self):
        data = b"\x00" * 64
        self.assertEqual(cli.decode(cli.encode(data)), data)

    def test_roundtrip_single_byte(self):
        for b in range(256):
            data = bytes([b])
            self.assertEqual(cli.decode(cli.encode(data)), data)

    def test_output_is_ascii(self):
        # Base64 output must be pure ASCII (safe for embedding in protocols)
        encoded = cli.encode(bytes(range(256)))
        encoded.decode('ascii')  # raises if not ASCII

    def test_deterministic(self):
        data = b"same input"
        self.assertEqual(cli.encode(data), cli.encode(data))

    def test_server_client_interop(self):
        data = b"cross-module check"
        self.assertEqual(cli.decode(srv.encode(data)), data)
        self.assertEqual(srv.decode(cli.encode(data)), data)

    def test_invalid_base64_raises(self):
        with self.assertRaises(Exception):
            cli.decode(b"not valid base64!!!")


# ── wire format ───────────────────────────────────────────────────────────────

class TestWireFormat(unittest.TestCase):
    """Inspect raw bytes on the wire to verify the framing contract."""

    def setUp(self):
        self.ss, self.cs = pair()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()

    def _raw_recv(self, sock, n):
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            self.assertTrue(chunk, "connection closed unexpectedly")
            data += chunk
        return data

    def test_length_prefix_is_4_bytes_big_endian(self):
        payload = b"hello"
        srv.send(srv.CMD.MSG, payload, self.ss)
        raw = self._raw_recv(self.cs[0], 4)
        length = struct.unpack(">I", raw)[0]
        # message = 1 (cmd byte) + len(payload)
        self.assertEqual(length, 1 + len(payload))

    def test_command_byte_follows_length(self):
        srv.send(srv.CMD.EXECUTE, b"test", self.ss)
        self._raw_recv(self.cs[0], 4)          # skip length
        cmd_byte = self._raw_recv(self.cs[0], 1)
        self.assertEqual(cmd_byte, b'\x01')    # CMD.EXECUTE

    def test_payload_follows_command_byte(self):
        payload = b"raw payload"
        srv.send(srv.CMD.MSG, payload, self.ss)
        raw_len = self._raw_recv(self.cs[0], 4)
        length = struct.unpack(">I", raw_len)[0]
        self._raw_recv(self.cs[0], 1)          # skip command byte
        received = self._raw_recv(self.cs[0], length - 1)
        self.assertEqual(received, payload)

    def test_all_command_codes_round_trip(self):
        codes = [
            (srv.CMD.MSG,      b'\x00'),
            (srv.CMD.EXECUTE,  b'\x01'),
            (srv.CMD.UPLOAD,   b'\x02'),
            (srv.CMD.DOWNLOAD, b'\x03'),
            (srv.CMD.SUCCESS,  b'\x04'),
        ]
        for cmd, expected_byte in codes:
            srv.send(cmd, b"x", self.ss)
            result = cli.recv(self.cs)
            self.assertIsNotNone(result)
            self.assertEqual(result[0], expected_byte, f"wrong byte for {cmd!r}")

    def test_manually_crafted_message_parsed_correctly(self):
        # Build a valid framed message by hand and send it raw
        cmd  = b'\x01'
        body = b"manual"
        msg  = cmd + body
        raw  = struct.pack(">I", len(msg)) + msg
        self.cs[0].sendall(raw)
        result = srv.recv(self.ss)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], b'\x01')
        self.assertEqual(result[1], b"manual")

    def test_zero_length_body_after_command_byte(self):
        cmd = b'\x00'
        msg = cmd           # command byte only, no payload
        raw = struct.pack(">I", len(msg)) + msg
        self.cs[0].sendall(raw)
        result = srv.recv(self.ss)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], b'\x00')
        self.assertEqual(result[1], b"")


# ── message framing (send / recv / _receive_all) ──────────────────────────────

class TestFraming(unittest.TestCase):
    def setUp(self):
        self.ss, self.cs = pair()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()

    def test_send_recv_text(self):
        srv.send(srv.CMD.MSG, b"ping", self.ss)
        cmd, payload = cli.recv(self.cs)
        self.assertEqual(cmd, b'\x00')
        self.assertEqual(payload, b"ping")

    def test_send_recv_binary_all_bytes(self):
        data = bytes(range(256))
        srv.send(srv.CMD.MSG, data, self.ss)
        _, payload = cli.recv(self.cs)
        self.assertEqual(payload, data)

    def test_send_recv_empty_payload(self):
        srv.send(srv.CMD.MSG, b"", self.ss)
        result = cli.recv(self.cs)
        self.assertIsNotNone(result)
        self.assertEqual(result[1], b"")

    def test_send_recv_single_byte_payload(self):
        srv.send(srv.CMD.MSG, b"\xff", self.ss)
        _, payload = cli.recv(self.cs)
        self.assertEqual(payload, b"\xff")

    def test_send_recv_null_bytes(self):
        data = b"\x00" * 100
        srv.send(srv.CMD.MSG, data, self.ss)
        _, payload = cli.recv(self.cs)
        self.assertEqual(payload, data)

    def test_send_recv_1mb(self):
        data = os.urandom(1024 * 1024)
        srv.send(srv.CMD.MSG, data, self.ss)
        _, payload = cli.recv(self.cs)
        self.assertEqual(payload, data)

    def test_send_str_payload_auto_encoded(self):
        cli.send(cli.CMD_MSG, "hello", self.cs)
        _, payload = srv.recv(self.ss)
        self.assertEqual(payload, b"hello")

    def test_bidirectional_exchange(self):
        # Server sends to client, then client replies
        srv.send(srv.CMD.MSG, b"question", self.ss)
        _, q = cli.recv(self.cs)
        self.assertEqual(q, b"question")
        cli.send(cli.CMD_MSG, b"answer", self.cs)
        _, a = srv.recv(self.ss)
        self.assertEqual(a, b"answer")

    def test_many_messages_in_sequence(self):
        n = 50
        for i in range(n):
            srv.send(srv.CMD.MSG, str(i).encode(), self.ss)
        for i in range(n):
            _, payload = cli.recv(self.cs)
            self.assertEqual(payload, str(i).encode())

    def test_interleaved_bidirectional(self):
        for i in range(10):
            srv.send(srv.CMD.MSG, f"s{i}".encode(), self.ss)
            cli.send(cli.CMD_MSG, f"c{i}".encode(), self.cs)
        for i in range(10):
            _, sp = cli.recv(self.cs)
            _, cp = srv.recv(self.ss)
            self.assertEqual(sp, f"s{i}".encode())
            self.assertEqual(cp, f"c{i}".encode())

    def test_send_invalid_command_string(self):
        self.assertEqual(cli.send("bad", b"data", self.cs), -1)

    def test_send_invalid_command_too_long(self):
        self.assertEqual(cli.send(b"\x00\x01", b"data", self.cs), -1)

    def test_send_invalid_command_empty(self):
        self.assertEqual(cli.send(b"", b"data", self.cs), -1)

    def test_send_none_payload(self):
        self.assertEqual(cli.send(cli.CMD_MSG, None, self.cs), -1)

    def test_send_none_session(self):
        self.assertEqual(cli.send(cli.CMD_MSG, b"data", None), -1)

    def test_send_int_payload_rejected(self):
        self.assertEqual(cli.send(cli.CMD_MSG, 12345, self.cs), -1)

    def test_recv_on_closed_socket(self):
        self.ss[0].close()
        self.assertIsNone(cli.recv(self.cs))

    def test_recv_returns_none_when_peer_closes_mid_header(self):
        # Send only 2 of the 4 header bytes then close
        self.ss[0].sendall(b"\x00\x00")
        self.ss[0].close()
        self.assertIsNone(cli.recv(self.cs))

    def test_recv_returns_none_when_peer_closes_mid_body(self):
        # Send a header claiming 100 bytes but only send 10
        header = struct.pack(">I", 100)
        self.ss[0].sendall(header + b"\x00" * 10)
        self.ss[0].close()
        self.assertIsNone(cli.recv(self.cs))

    def test_exact_power_of_two_payload(self):
        for size in [1, 2, 4, 8, 16, 256, 1024, 4096]:
            with self.subTest(size=size):
                data = bytes([size % 256]) * size
                srv.send(srv.CMD.MSG, data, self.ss)
                _, payload = cli.recv(self.cs)
                self.assertEqual(payload, data)


# ── execute ───────────────────────────────────────────────────────────────────

class TestExecute(unittest.TestCase):
    def setUp(self):
        self.ss, self.cs = pair()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()

    def _server_execute_once(self):
        result = srv.recv(self.ss)
        if result is None:
            return
        _, payload = result
        srv.execute(srv.decode(payload), self.ss)

    def _run(self, cmd):
        t = threading.Thread(target=self._server_execute_once, daemon=True)
        t.start()
        cli.execute(cmd, self.cs)
        response = cli.recv(self.cs)
        t.join(timeout=5)
        self.assertIsNotNone(response)
        _, payload = response
        return cli.decode(payload).decode('utf-8').strip()

    def test_whoami(self):
        self.assertEqual(self._run("whoami"), getpass.getuser())

    def test_echo_single_word(self):
        self.assertEqual(self._run("echo hello"), "hello")

    def test_echo_multiple_words(self):
        self.assertEqual(self._run("echo one two three"), "one two three")

    def test_uname(self):
        self.assertEqual(self._run("uname -s"), "Linux")

    def test_pwd(self):
        output = self._run("pwd")
        self.assertTrue(output.startswith("/"))

    def test_true_empty_output(self):
        # `true` exits 0 with no output
        output = self._run("true")
        self.assertEqual(output, "")

    def test_hostname(self):
        import socket as _socket
        output = self._run("hostname")
        self.assertEqual(output, _socket.gethostname())

    def test_id_contains_uid(self):
        output = self._run("id")
        self.assertIn("uid=", output)

    def test_echo_numbers(self):
        self.assertEqual(self._run("echo 12345"), "12345")

    def test_large_output(self):
        # seq generates many lines; verify we get them all back
        output = self._run("seq 1 200")
        lines = output.splitlines()
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[-1], "200")

    def test_stderr_captured(self):
        # ls on a nonexistent path writes to stderr
        output = self._run("ls /no/such/path/xyz")
        self.assertTrue(len(output) > 0)  # stderr should be captured

    def test_invalid_command_no_crash(self):
        output = self._run("nonexistent_command_xyz_abc")
        self.assertIsInstance(output, str)

    def test_response_command_byte_on_success(self):
        # On success, server sends CMD.SUCCESS (0x04)
        t = threading.Thread(target=self._server_execute_once, daemon=True)
        t.start()
        cli.execute("echo ok", self.cs)
        result = cli.recv(self.cs)
        t.join(timeout=5)
        self.assertIsNotNone(result)
        cmd, _ = result
        self.assertEqual(cmd, srv.CMD.SUCCESS)

    def test_multiple_sequential_executes(self):
        commands = ["echo a", "echo b", "echo c", "echo d", "echo e"]
        expected = ["a", "b", "c", "d", "e"]

        def server_side():
            for _ in commands:
                result = srv.recv(self.ss)
                if result is None:
                    break
                _, payload = result
                srv.execute(srv.decode(payload), self.ss)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()

        outputs = []
        for cmd in commands:
            cli.execute(cmd, self.cs)
            result = cli.recv(self.cs)
            self.assertIsNotNone(result)
            _, payload = result
            outputs.append(cli.decode(payload).decode('utf-8').strip())

        t.join(timeout=5)
        self.assertEqual(outputs, expected)


# ── upload (client → server) ──────────────────────────────────────────────────

class TestUpload(unittest.TestCase):
    def setUp(self):
        self.ss, self.cs = pair()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _server_receive_upload(self):
        result = srv.recv(self.ss)
        if result is None:
            return
        _, payload = result
        filename = srv.decode(payload).decode('utf-8')
        result = srv.recv(self.ss)
        if result is None:
            return
        _, file_data = result
        dest = os.path.join(self.tmpdir, os.path.basename(filename))
        srv.download(dest, file_data, self.ss)

    def _upload(self, src_path):
        t = threading.Thread(target=self._server_receive_upload, daemon=True)
        t.start()
        ret = cli.upload(src_path, self.cs)
        cli.recv(self.cs)  # consume server's response
        t.join(timeout=5)
        return ret

    def _make_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_upload_text_file(self):
        content = b"Hello from client!\n"
        src = self._make_file("hello.txt", content)
        self.assertEqual(self._upload(src), 0)
        with open(os.path.join(self.tmpdir, "hello.txt"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_binary_file(self):
        content = bytes(range(256)) * 40  # 10 KB
        src = self._make_file("data.bin", content)
        self._upload(src)
        with open(os.path.join(self.tmpdir, "data.bin"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_empty_file(self):
        src = self._make_file("empty.txt", b"")
        self._upload(src)
        self.assertEqual(os.path.getsize(os.path.join(self.tmpdir, "empty.txt")), 0)

    def test_upload_large_file(self):
        content = os.urandom(2 * 1024 * 1024)  # 2 MB
        src = self._make_file("large.bin", content)
        self._upload(src)
        with open(os.path.join(self.tmpdir, "large.bin"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_null_bytes(self):
        content = b"\x00" * 512
        src = self._make_file("nulls.bin", content)
        self._upload(src)
        with open(os.path.join(self.tmpdir, "nulls.bin"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_single_byte_file(self):
        content = b"\xab"
        src = self._make_file("one.bin", content)
        self._upload(src)
        with open(os.path.join(self.tmpdir, "one.bin"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_exact_content_preserved(self):
        # Verify no encoding artifacts — bytes in == bytes out
        content = bytes(range(256))
        src = self._make_file("exact.bin", content)
        self._upload(src)
        with open(os.path.join(self.tmpdir, "exact.bin"), 'rb') as f:
            self.assertEqual(f.read(), content)

    def test_upload_overwrites_existing_file(self):
        # Write a file to the destination first, then overwrite via upload
        dst = os.path.join(self.tmpdir, "overwrite.txt")
        with open(dst, 'wb') as f:
            f.write(b"old content")
        src = self._make_file("overwrite.txt", b"new content")
        self._upload(src)
        with open(dst, 'rb') as f:
            self.assertEqual(f.read(), b"new content")

    def test_upload_nonexistent_source_returns_error(self):
        ret = cli.upload("/no/such/file.txt", self.cs)
        self.assertEqual(ret, -1)

    def test_upload_server_sends_success_response(self):
        src = self._make_file("resp.txt", b"data")
        t = threading.Thread(target=self._server_receive_upload, daemon=True)
        t.start()
        cli.upload(src, self.cs)
        response = cli.recv(self.cs)
        t.join(timeout=5)
        self.assertIsNotNone(response)
        cmd, _ = response
        self.assertEqual(cmd, srv.CMD.SUCCESS)

    def test_multiple_sequential_uploads(self):
        files = {f"file{i}.txt": os.urandom(256) for i in range(4)}

        def server_side():
            for _ in files:
                result = srv.recv(self.ss)
                if result is None:
                    break
                _, payload = result
                filename = srv.decode(payload).decode('utf-8')
                result = srv.recv(self.ss)
                if result is None:
                    break
                _, file_data = result
                dest = os.path.join(self.tmpdir, os.path.basename(filename))
                srv.download(dest, file_data, self.ss)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()

        for name, content in files.items():
            src = self._make_file(f"src_{name}", content)
            # rename so src filename == expected dest filename
            real_src = os.path.join(self.tmpdir, name)
            os.rename(src, real_src)
            cli.upload(real_src, self.cs)
            cli.recv(self.cs)  # consume response

        t.join(timeout=5)

        for name, content in files.items():
            dst = os.path.join(self.tmpdir, name)
            self.assertTrue(os.path.exists(dst), f"{name} not found on server")
            with open(dst, 'rb') as f:
                self.assertEqual(f.read(), content)


# ── download (server → client) ────────────────────────────────────────────────

class TestDownload(unittest.TestCase):
    def setUp(self):
        self.ss, self.cs = pair()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _server_handle_download(self):
        result = srv.recv(self.ss)
        if result is None:
            return
        _, payload = result
        path = srv.decode(payload).decode('utf-8')
        srv.upload(path, self.ss)

    def _download(self, remote_path):
        t = threading.Thread(target=self._server_handle_download, daemon=True)
        t.start()
        cli.download(remote_path, self.cs)
        response = cli.recv(self.cs)
        t.join(timeout=5)
        return response

    def _make_server_file(self, name, content):
        path = os.path.join(self.tmpdir, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_download_text_file(self):
        content = b"Top secret server data\n"
        path = self._make_server_file("secret.txt", content)
        response = self._download(path)
        self.assertIsNotNone(response)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_binary_file(self):
        content = bytes(range(256)) * 40
        path = self._make_server_file("blob.bin", content)
        response = self._download(path)
        self.assertIsNotNone(response)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_empty_file(self):
        path = self._make_server_file("empty.txt", b"")
        response = self._download(path)
        self.assertIsNotNone(response)
        _, payload = response
        self.assertEqual(cli.decode(payload), b"")

    def test_download_large_file(self):
        content = os.urandom(2 * 1024 * 1024)  # 2 MB
        path = self._make_server_file("large.bin", content)
        response = self._download(path)
        self.assertIsNotNone(response)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_null_bytes(self):
        content = b"\x00" * 512
        path = self._make_server_file("nulls.bin", content)
        response = self._download(path)
        self.assertIsNotNone(response)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_single_byte(self):
        content = b"\xde"
        path = self._make_server_file("one.bin", content)
        response = self._download(path)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_all_byte_values(self):
        content = bytes(range(256))
        path = self._make_server_file("allbytes.bin", content)
        response = self._download(path)
        _, payload = response
        self.assertEqual(cli.decode(payload), content)

    def test_download_nonexistent_file_no_crash(self):
        response = self._download("/no/such/file_on_server.txt")
        self.assertIsNotNone(response)

    def test_download_response_command_byte(self):
        path = self._make_server_file("cmd.txt", b"data")
        response = self._download(path)
        self.assertIsNotNone(response)
        cmd, _ = response
        # Server's upload() sends CMD.MSG (0x00)
        self.assertEqual(cmd, srv.CMD.MSG)

    def test_download_byte_for_byte_accuracy(self):
        # Spot-check every possible single byte value arrives exactly
        for byte_val in range(256):
            content = bytes([byte_val])
            path = self._make_server_file(f"byte_{byte_val}.bin", content)
            response = self._download(path)
            self.assertIsNotNone(response)
            _, payload = response
            self.assertEqual(cli.decode(payload), content, f"failed for byte 0x{byte_val:02x}")

    def test_multiple_sequential_downloads(self):
        files = {f"file{i}.txt": os.urandom(128) for i in range(4)}
        paths = {name: self._make_server_file(name, content)
                 for name, content in files.items()}

        def server_side():
            for _ in files:
                result = srv.recv(self.ss)
                if result is None:
                    break
                _, payload = result
                path = srv.decode(payload).decode('utf-8')
                srv.upload(path, self.ss)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()

        for name, content in files.items():
            cli.download(paths[name], self.cs)
            response = cli.recv(self.cs)
            self.assertIsNotNone(response)
            _, payload = response
            self.assertEqual(cli.decode(payload), content)

        t.join(timeout=5)


# ── full session ──────────────────────────────────────────────────────────────

class TestFullSession(unittest.TestCase):
    """Simulate a realistic multi-command session over one connection."""

    def setUp(self):
        self.ss, self.cs = pair()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_execute_then_upload_then_download(self):
        file_content = b"session test file content"
        src = os.path.join(self.tmpdir, "session.txt")
        with open(src, 'wb') as f:
            f.write(file_content)

        errors = []

        def server_side():
            # 1. Handle execute
            result = srv.recv(self.ss)
            if result is None:
                errors.append("recv failed for execute")
                return
            _, payload = result
            srv.execute(srv.decode(payload), self.ss)

            # 2. Handle upload (client sends file to server)
            result = srv.recv(self.ss)
            if result is None:
                errors.append("recv failed for upload filename")
                return
            _, payload = result
            filename = srv.decode(payload).decode('utf-8')
            result = srv.recv(self.ss)
            if result is None:
                errors.append("recv failed for upload data")
                return
            _, file_data = result
            dest = os.path.join(self.tmpdir, "received_" + os.path.basename(filename))
            srv.download(dest, file_data, self.ss)

            # 3. Handle download (client requests file from server)
            result = srv.recv(self.ss)
            if result is None:
                errors.append("recv failed for download")
                return
            _, payload = result
            path = srv.decode(payload).decode('utf-8')
            srv.upload(path, self.ss)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()

        # 1. Execute
        cli.execute("whoami", self.cs)
        r = cli.recv(self.cs)
        self.assertIsNotNone(r)
        output = cli.decode(r[1]).decode('utf-8').strip()
        self.assertEqual(output, getpass.getuser())

        # 2. Upload
        cli.upload(src, self.cs)
        cli.recv(self.cs)  # consume server response

        # 3. Download the file the server just received
        dest = os.path.join(self.tmpdir, "received_session.txt")
        cli.download(dest, self.cs)
        r = cli.recv(self.cs)

        t.join(timeout=10)

        self.assertEqual(errors, [], f"Server errors: {errors}")
        self.assertIsNotNone(r)
        received = cli.decode(r[1])
        self.assertEqual(received, file_content)

    def test_upload_roundtrip(self):
        """Upload a file then download it back and verify identical content."""
        original = os.urandom(4096)
        src = os.path.join(self.tmpdir, "roundtrip.bin")
        server_store = os.path.join(self.tmpdir, "server_roundtrip.bin")
        with open(src, 'wb') as f:
            f.write(original)

        def server_side():
            # upload phase
            r = srv.recv(self.ss)
            filename = srv.decode(r[1]).decode('utf-8')
            r = srv.recv(self.ss)
            srv.download(server_store, r[1], self.ss)
            # download phase
            r = srv.recv(self.ss)
            path = srv.decode(r[1]).decode('utf-8')
            srv.upload(path, self.ss)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()

        cli.upload(src, self.cs)
        cli.recv(self.cs)  # consume success response

        cli.download(server_store, self.cs)
        r = cli.recv(self.cs)
        t.join(timeout=10)

        self.assertIsNotNone(r)
        self.assertEqual(cli.decode(r[1]), original)


# ── client connect / disconnect ───────────────────────────────────────────────

class TestClientConnect(unittest.TestCase):
    """Test cli.connect() and cli.disconnect() against a real listening socket."""

    def setUp(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.accepted = [None]

        def _accept():
            conn, addr = self.listener.accept()
            self.accepted[0] = (conn, addr)

        self.accept_thread = threading.Thread(target=_accept, daemon=True)
        self.accept_thread.start()

    def tearDown(self):
        self.listener.close()
        if self.accepted[0]:
            self.accepted[0][0].close()

    def test_connect_returns_session_tuple(self):
        orig_server, orig_port = cli.SERVER, cli.PORT
        cli.SERVER, cli.PORT = "127.0.0.1", self.port
        try:
            session = cli.connect()
            self.accept_thread.join(timeout=3)
            self.assertIsNotNone(session)
            self.assertIsInstance(session, tuple)
            self.assertEqual(len(session), 2)
            session[0].close()
        finally:
            cli.SERVER, cli.PORT = orig_server, orig_port

    def test_connect_socket_is_connected(self):
        orig_server, orig_port = cli.SERVER, cli.PORT
        cli.SERVER, cli.PORT = "127.0.0.1", self.port
        try:
            session = cli.connect()
            self.accept_thread.join(timeout=3)
            self.assertIsNotNone(session)
            # Should be able to send data without error
            conn, _ = session
            conn.sendall(b"\x00" * 4 + b"\x00")  # minimal valid frame
            session[0].close()
        finally:
            cli.SERVER, cli.PORT = orig_server, orig_port

    def test_connect_to_wrong_port_returns_none(self):
        orig_server, orig_port = cli.SERVER, cli.PORT
        cli.SERVER, cli.PORT = "127.0.0.1", 1  # port 1 will be refused
        try:
            session = cli.connect()
            self.assertIsNone(session)
        finally:
            cli.SERVER, cli.PORT = orig_server, orig_port

    def test_disconnect_closes_socket(self):
        orig_server, orig_port = cli.SERVER, cli.PORT
        cli.SERVER, cli.PORT = "127.0.0.1", self.port
        try:
            session = cli.connect()
            self.accept_thread.join(timeout=3)
            self.assertIsNotNone(session)
            cli.disconnect(session)
            # After disconnect, sending should raise
            with self.assertRaises(OSError):
                session[0].sendall(b"dead")
        finally:
            cli.SERVER, cli.PORT = orig_server, orig_port

    def test_disconnect_twice_does_not_raise(self):
        orig_server, orig_port = cli.SERVER, cli.PORT
        cli.SERVER, cli.PORT = "127.0.0.1", self.port
        try:
            session = cli.connect()
            self.accept_thread.join(timeout=3)
            cli.disconnect(session)
            cli.disconnect(session)  # second call must not raise
        finally:
            cli.SERVER, cli.PORT = orig_server, orig_port


# ── client main() response path ───────────────────────────────────────────────

class TestClientResponseDecoding(unittest.TestCase):
    """Verify the decode(payload).decode('utf-8') path used in main()."""

    def setUp(self):
        self.ss, self.cs = pair()

    def tearDown(self):
        self.ss[0].close()
        self.cs[0].close()

    def _run_and_get_decoded(self, cmd):
        """Simulate exactly what main() does after calling execute()."""
        t = threading.Thread(target=lambda: (
            srv.execute(srv.decode(srv.recv(self.ss)[1]), self.ss)
        ), daemon=True)
        t.start()
        cli.execute(cmd, self.cs)
        response = cli.recv(self.cs)
        t.join(timeout=5)
        self.assertIsNotNone(response)
        _, payload = response
        # This is the exact expression from client.py main()
        return cli.decode(payload).decode('utf-8')

    def test_execute_response_is_decodable_string(self):
        result = self._run_and_get_decoded("whoami")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 0)

    def test_execute_response_matches_whoami(self):
        result = self._run_and_get_decoded("whoami")
        self.assertEqual(result.strip(), getpass.getuser())

    def test_execute_response_matches_echo(self):
        result = self._run_and_get_decoded("echo test123")
        self.assertEqual(result.strip(), "test123")

    def test_download_response_is_decodable(self):
        """Server sends base64(file_data); client does decode(payload) to get bytes."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"file content for download")
            path = f.name
        try:
            def server_side():
                result = srv.recv(self.ss)
                _, payload = result
                srv.upload(srv.decode(payload).decode('utf-8'), self.ss)

            t = threading.Thread(target=server_side, daemon=True)
            t.start()
            cli.download(path, self.cs)
            response = cli.recv(self.cs)
            t.join(timeout=5)

            self.assertIsNotNone(response)
            _, payload = response
            # download response is raw file bytes wrapped in base64
            decoded = cli.decode(payload)
            self.assertEqual(decoded, b"file content for download")
        finally:
            os.unlink(path)

    def test_upload_response_is_decodable_string(self):
        """Server sends a base64-encoded success message after upload."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"upload test data")
            src = f.name
        try:
            def server_side():
                r = srv.recv(self.ss)
                filename = srv.decode(r[1]).decode('utf-8')
                r = srv.recv(self.ss)
                _, file_data = r
                with tempfile.NamedTemporaryFile(delete=False) as out:
                    out_path = out.name
                srv.download(out_path, file_data, self.ss)
                os.unlink(out_path)

            t = threading.Thread(target=server_side, daemon=True)
            t.start()
            cli.upload(src, self.cs)
            response = cli.recv(self.cs)
            t.join(timeout=5)

            self.assertIsNotNone(response)
            _, payload = response
            msg = cli.decode(payload).decode('utf-8')
            self.assertIsInstance(msg, str)
            self.assertGreater(len(msg), 0)
        finally:
            os.unlink(src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
