from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import paramiko


INFERENCE_FILES = [
    "config.json",
    "generation_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "processor_config.json",
    "training_args.bin",
    "model.safetensors",
]


def copy_resume(sftp: paramiko.SFTPClient, remote_path: str, local_path: Path, chunk_size: int = 1024 * 1024) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    remote_size = sftp.stat(remote_path).st_size
    done = local_path.stat().st_size if local_path.exists() else 0
    if done == remote_size:
        print(f"OK {local_path}", flush=True)
        return
    if done > remote_size:
        raise RuntimeError(f"Local file is larger than remote file: {local_path}")

    print(f"GET {remote_path} -> {local_path} ({done}/{remote_size} bytes)", flush=True)
    with sftp.open(remote_path, "rb") as remote, local_path.open("ab") as local:
        remote.seek(done)
        last = time.time()
        while done < remote_size:
            data = remote.read(min(chunk_size, remote_size - done))
            if not data:
                raise EOFError(f"Connection ended while reading {remote_path}")
            local.write(data)
            done += len(data)
            now = time.time()
            if now - last > 15 or done == remote_size:
                print(f"  {local_path.name}: {done * 100 / remote_size:.1f}%", flush=True)
                last = now


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume-copy inference files from the rented GPU server.")
    parser.add_argument("--host", default=os.getenv("ASR_SSH_HOST", "connect.bjb1.seetacloud.com"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ASR_SSH_PORT", "22861")))
    parser.add_argument("--user", default=os.getenv("ASR_SSH_USER", "root"))
    parser.add_argument("--password", default=os.getenv("ASR_SSH_PASSWORD", ""))
    parser.add_argument("--remote-dir", required=True)
    parser.add_argument("--local-dir", required=True)
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("Set ASR_SSH_PASSWORD or pass --password.")

    print(f"CONNECT {args.user}@{args.host}:{args.port}", flush=True)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        timeout=30,
        banner_timeout=30,
        auth_timeout=30,
    )
    ssh.get_transport().set_keepalive(30)
    print("OPEN SFTP", flush=True)
    sftp = ssh.open_sftp()
    sftp.get_channel().settimeout(120)
    try:
        for file_name in INFERENCE_FILES:
            copy_resume(sftp, f"{args.remote_dir.rstrip('/')}/{file_name}", Path(args.local_dir) / file_name)
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    main()
