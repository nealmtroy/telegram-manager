from __future__ import annotations

import argparse
from pathlib import Path


def convert_line(line: str, scheme: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "://" in stripped:
        return line.rstrip("\n")

    parts = stripped.split(":")
    if len(parts) != 4:
        return line.rstrip("\n")

    host, port, username, password = parts
    if not host or not port or not username or not password:
        return line.rstrip("\n")
    return f"{scheme}://{username}:{password}@{host}:{port}"


def convert_file(path: Path, scheme: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    backup_path = path.with_suffix(path.suffix + ".bak")
    backup_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    converted = [convert_line(line, scheme) for line in lines]
    path.write_text("\n".join(converted) + ("\n" if converted else ""), encoding="utf-8")
    print(f"Converted: {path}")
    print(f"Backup: {backup_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert proxies from ip:port:username:password to URL format."
    )
    parser.add_argument("path", nargs="?", default="proxies.txt", help="Proxy file path")
    parser.add_argument("--scheme", default="http", choices=("http", "socks4", "socks5"))
    args = parser.parse_args()

    proxy_path = Path(args.path)
    if not proxy_path.exists():
        raise SystemExit(f"File not found: {proxy_path}")
    convert_file(proxy_path, args.scheme)


if __name__ == "__main__":
    main()
