from __future__ import annotations

import argparse
import os
import shlex
import time
from pathlib import Path

import paramiko


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    last_error: Exception | None = None
    for attempt in range(1, args.connect_retries + 1):
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                args.host,
                port=args.port,
                username=args.user,
                password=args.password,
                timeout=30,
                banner_timeout=60,
                auth_timeout=30,
            )
            ssh.get_transport().set_keepalive(30)
            return ssh
        except Exception as exc:
            last_error = exc
            try:
                ssh.close()
            except Exception:
                pass
            wait_s = min(30, attempt * 5)
            print(f"CONNECT retry {attempt}/{args.connect_retries}: {exc}; wait {wait_s}s", flush=True)
            time.sleep(wait_s)
    raise RuntimeError(f"Could not connect after retries: {last_error}")


def remote_size(ssh: paramiko.SSHClient, remote_path: str) -> int:
    command = f"stat -c %s {shlex.quote(remote_path)}"
    _, stdout, stderr = ssh.exec_command(command, timeout=30)
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    if not out.isdigit():
        raise RuntimeError(f"Could not stat remote file: {err or out}")
    return int(out)


def fetch_chunk(
    ssh: paramiko.SSHClient,
    remote_path: str,
    offset: int,
    length: int,
    block_size: int,
) -> bytes:
    if offset % block_size:
        command = (
            f"dd if={shlex.quote(remote_path)} bs=1 skip={offset} count={length} "
            "status=none"
        )
    else:
        command = (
            f"dd if={shlex.quote(remote_path)} bs={block_size} "
            f"skip={offset // block_size} count={(length + block_size - 1) // block_size} "
            "status=none"
        )
    _, stdout, stderr = ssh.exec_command(command, timeout=300)
    data = stdout.read(length)
    err = stderr.read().decode("utf-8", "replace").strip()
    if len(data) != length:
        raise EOFError(f"Expected {length} bytes at {offset}, got {len(data)}. {err}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Download one remote file over SSH stdout in resumable chunks.")
    parser.add_argument("--host", default=os.getenv("ASR_SSH_HOST", "connect.bjb1.seetacloud.com"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ASR_SSH_PORT", "22861")))
    parser.add_argument("--user", default=os.getenv("ASR_SSH_USER", "root"))
    parser.add_argument("--password", default=os.getenv("ASR_SSH_PASSWORD", ""))
    parser.add_argument("--remote-file", required=True)
    parser.add_argument("--local-file", required=True)
    parser.add_argument("--chunk-mb", type=int, default=64)
    parser.add_argument("--connect-retries", type=int, default=8)
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("Set ASR_SSH_PASSWORD or pass --password.")

    local_path = Path(args.local_file)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    block_size = 1024 * 1024
    chunk_size = max(1, args.chunk_mb) * block_size

    ssh = connect(args)
    try:
        size = remote_size(ssh, args.remote_file)
    finally:
        ssh.close()

    done = local_path.stat().st_size if local_path.exists() else 0
    if done > size:
        raise RuntimeError(f"Local file is larger than remote file: {local_path}")
    print(f"REMOTE {size} bytes", flush=True)
    print(f"LOCAL  {done} bytes", flush=True)

    started = time.time()
    while done < size:
        length = min(chunk_size, size - done)
        for attempt in range(1, args.connect_retries + 1):
            ssh = connect(args)
            try:
                data = fetch_chunk(ssh, args.remote_file, done, length, block_size)
                break
            except Exception as exc:
                data = b""
                print(f"CHUNK retry {attempt}/{args.connect_retries} at {done}: {exc}", flush=True)
                time.sleep(min(30, attempt * 5))
            finally:
                ssh.close()
        if not data:
            raise RuntimeError(f"Failed to fetch chunk at offset {done}")
        with local_path.open("ab") as handle:
            handle.write(data)
            handle.flush()
        done += length
        elapsed = max(time.time() - started, 0.001)
        mbps = done / elapsed / 1024 / 1024
        print(f"{done}/{size} {done * 100 / size:.2f}% {mbps:.2f} MiB/s", flush=True)

    print(f"OK {local_path}", flush=True)


if __name__ == "__main__":
    main()
