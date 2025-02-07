#!/usr/bin/env python3
# Download Visual Studio Code client and server, and a list of extensions

import argparse
import os
import requests
from pathlib import Path
import re
import typing as t
from email.utils import parsedate_to_datetime
import hashlib


def download(dest_dir: Path, url: str) -> str:

    session = requests.Session()

    r = session.head(url)
    if "Location" not in r.headers:
        print("no Location header:", url, r)
        return

    real_url = r.headers["Location"]
    filename = Path(real_url).name
    file = dest_dir / filename

    if file.is_file():
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if digest != r.headers["X-SHA256"]:
            file.unlink()

    if not file.is_file():
        file.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {file}")
        r = session.get(real_url)
        file.write_bytes(r.content)

        if int(r.headers["Content-Length"]) != file.stat().st_size:
            file.unlink()
            print(f"download problem {url}")
            exit(2)

        url_date = parsedate_to_datetime(r.headers["Last-Modified"])
        mtime = round(url_date.timestamp() * 1_000_000_000)
        os.utime(file, ns=(mtime, mtime))
    else:
        print(f"already downloaded: {filename}")

    return filename


def write_assets_file(config_file: Path, assets: t.Dict[str, str]):

    if config_file.is_file():
        config = config_file.read_text()
    else:
        config = ""

    new_config = []
    for k, v in assets.items():
        new_config.append(f"{k}={v}")

    for line in config.splitlines():
        for k in assets.keys():
            if line.lstrip().startswith(f"{k}="):
                break
        else:
            new_config.append(line)

    config = "\n".join(new_config) + "\n"

    old = config_file.with_suffix(".old")
    if old.is_file():
        old.unlink()
    if config_file.is_file():
        config_file.rename(old)

    config_file.write_text(config)


def main():
    """Main function."""

    parser = argparse.ArgumentParser()
    parser.add_argument("-d", "--dest-dir", help="output dir", type=Path, default="latest")
    parser.add_argument("-v", "--version", help="version", default="latest")
    parser.add_argument("--channel", help=argparse.SUPPRESS, choices=["stable"], default="stable")
    args = parser.parse_args()

    ###############################################################################
    print("\n\033[1;34m🍻 Downloading VSCode\033[0m")

    print(f"Visual Studio Code: \033[1;33m{args.channel}\033[0m")

    # retrieve Windows version download link
    # ref: https://code.visualstudio.com/docs/supporting/faq#_previous-release-versions
    url = f"https://update.code.visualstudio.com/{args.version}/win32-x64-archive/{args.channel}"

    r = requests.get(url, allow_redirects=False)
    if r is None or r.status_code != 302:
        print("request error")
        exit(2)

    url = r.headers["Location"]

    # extract the commit and the version from the download link
    m = re.search(r"/(\w+)/([a-f0-9]{40})/VSCode-win32-x64-([\d.]+).zip", url)
    if not m:
        print("version not found")
        exit(2)

    channel, commit_id, version = m.groups()
    if channel != args.channel:
        print("bad channel")
        exit(2)

    print(f"Found version: \033[1;32m{version}\033[0m")
    print(f"Found commit: \033[1;32m{commit_id}\033[0m")

    # prepare the version dependant output directory
    dest_dir = args.dest_dir
    dest_dir.mkdir(exist_ok=True, parents=True)

    # save the version information
    (dest_dir / "version").write_text(f"version={version}\ncommit={commit_id}\nchannel={channel}\n")

    assets = dict()
    assets["version"] = version
    assets["commit"] = commit_id
    assets["channel"] = channel

    # the following mess is found here:
    # https://github.com/microsoft/vscode/blob/master/cli/src/update_service.rs#L224
    # https://code.visualstudio.com/docs/supporting/FAQ

    urls = {
        # archive for Windows and Linux
        "code_win32": f"https://update.code.visualstudio.com/{version}/win32-x64-archive/{channel}",
        "code_tar": f"https://update.code.visualstudio.com/{version}/linux-x64/{channel}",
        "code_deb": f"https://update.code.visualstudio.com/{version}/linux-deb-x64/{channel}",
        # headless (server) for Linux (glibc)
        "server_linux": f"https://update.code.visualstudio.com/{version}/server-linux-x64/{channel}",
        # headless (server) for Alpine Linux (musl-libc)
        # "server_linux_alpine": f"https://update.code.visualstudio.com/{version}/server-linux-alpine/{channel}",
        # cli for Linux
        "cli_linux": f"https://update.code.visualstudio.com/{version}/cli-linux-x64/{channel}",
        # cli for Alpine
        # "cli_linux_alpine": f"https://update.code.visualstudio.com/{version}/cli-alpine-x64/{channel}",
    }

    for name, url in urls.items():
        assets[name] = download(dest_dir, url)

    write_assets_file(dest_dir / "files", assets)


if __name__ == "__main__":
    main()
