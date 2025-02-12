#!/usr/bin/env python3
# Download the last Visual Studio Code extension compatible with a given version

import os
import argparse
import json
import requests
from pathlib import Path
from dataclasses import dataclass
import typing as t
import re
import sys
import zipfile
import subprocess
from datetime import datetime
from collections import defaultdict
import logging
from io import StringIO


# cf. https://github.com/microsoft/vscode/blob/main/cli/src/update_service.rs#L241

PLATFORMS = {
    "alpine-arm64": False,
    "alpine-x64": False,
    "darwin-arm64": True,
    "darwin-x64": False,
    "linux-arm64": False,
    "linux-armhf": False,
    "linux-x64": True,
    "web": False,
    "win32-arm64": False,
    "win32-ia32": False,
    "win32-x64": True,
}


# constants from vscode extension API
# https://github.com/microsoft/vscode/blob/main/src/vs/platform/extensionManagement/common/extensionGalleryService.ts

FilterType_Tag = 1
FilterType_ExtensionId = 4
FilterType_Category = 5
FilterType_ExtensionName = 7
FilterType_Target = 8
FilterType_Featured = 9
FilterType_SearchText = 10
FilterType_ExcludeWithFlags = 12

Flags_None = 0x0
Flags_IncludeVersions = 0x1
Flags_IncludeFiles = 0x2
Flags_IncludeCategoryAndTags = 0x4
Flags_IncludeSharedAccounts = 0x8
Flags_IncludeVersionProperties = 0x10
Flags_ExcludeNonValidated = 0x20
Flags_IncludeInstallationTargets = 0x40
Flags_IncludeAssetUri = 0x80
Flags_IncludeStatistics = 0x100
Flags_IncludeLatestVersionOnly = 0x200
Flags_Unpublished = 0x1000
Flags_IncludeNameConflictInfo = 0x8000


def get_property(version, name):
    if "properties" not in version:
        # print(version)
        return
    for property in version.get("properties", ()):
        if property["key"] == name:
            return property["value"]
    return


def version_serial(version):
    v = version.split(".", maxsplit=2)
    if "-" in v[2]:
        r = v[2].split("-", maxsplit=1)
        t = (int(v[0]), int(v[1]), int(r[0]), r[1])
        return t
    elif "x" in v[2]:
        t = (int(v[0]), int(v[1]), 0)
        return t
    else:
        return tuple(map(int, v))


def engine_match(pattern, engine):
    if pattern == "*":
        return True

    if pattern[0] != "^":
        if pattern == "0.10.x" or pattern.endswith("-insider"):
            return False
        # print("missing caret:", pattern)
        return False

    assert pattern[0] == "^"

    def rr():
        p = version_serial(pattern[1:])
        v = version_serial(engine)

        if len(p) == 4 and p[3] == "insiders":
            return False

        if p[0] != v[0]:  # major must be the same
            return False
        if p[1] > v[1]:  # minor must be greater or equal
            return False
        if p[1] == v[1] and p[2] != 0 and p[2] > v[2]:
            return False

        return True

    r = rr()
    # print(pattern, engine, r)
    return r


@dataclass
class Asset:
    name: str
    "Vame of the extension (<publisherName>.<extensionName>)."

    version: str
    "Version string."

    engine: str
    "Visual Studio Code engine."

    uri: str
    "Download link."

    timestamp: str
    "lastUpdated time."

    platform: t.Optional[str]
    "Platform string or None."

    ignore: bool = False
    "Ignore the extension in the inventory."

    @property
    def vsix(self) -> str:
        """Filename of the vsix."""
        if self.platform:
            return f"{self.name}-{self.platform}-{self.version}.vsix"
        else:
            return f"{self.name}-{self.version}.vsix"

    def vsix_glob(self) -> str:
        """Pattern for all versions of the extension."""
        if self.platform:
            return f"{self.name}-{self.platform}-*.vsix"
        else:
            return f"{self.name}-*.vsix"


def vscode_lldb(asset: Asset, dest_dir: Path) -> t.List[Asset]:
    """
    vscode-lldb is special: platform packages are downloaded separately.
    """
    assert asset.name == "vadimcn.vscode-lldb"
    assert asset.platform is None

    zip = zipfile.ZipFile(dest_dir / asset.vsix)
    m = json.loads(zip.open("extension/package.json").read())
    zip.close()

    version = m.get("version")
    platform_packages = m.get("config", {}).get("platformPackages", {})
    url = platform_packages.get("url")
    platforms = platform_packages.get("platforms")
    if not url or not platforms:
        logging.error(f"{asset.name} package.json has changed")
        return

    logging.debug(f"vscode-lldb url: {url}")
    logging.debug(f"vscode-lldb version: {version}")
    logging.debug(f"vscode-lldb platforms: {list(platforms.keys())}")

    assets = list()

    for platform, vsix in platforms.items():
        if PLATFORMS.get(platform) is True:
            uri = url.replace("${version}", version).replace("${platformPackage}", vsix)

            assets.append(Asset(asset.name, asset.version, asset.engine, uri, asset.timestamp, platform))

    return assets


class Extensions:
    def __init__(self, engine: str, dest_dir: Path, verbose: bool = False):
        self.engine = engine
        self.verbose = verbose
        self.dest_dir = dest_dir
        self.all_assets_list = None

    def run(self, extension_ids: t.Iterable[str]) -> t.List[Asset]:
        """
        Download all extensions and packs.
        """

        all_assets = dict()

        all_extension_ids = set(extension_ids)  # set of extension identifiers already fetched
        assets, packs = self.find_assets(extension_ids)
        all_assets.update(assets)

        self.download_vsix_files(assets.values())

        # as long we have packs
        while packs:
            new_extension_ids = set()

            for pack in packs:
                # load the extension pack manifest (on disk) and get the child extensions
                zip = zipfile.ZipFile(self.dest_dir / assets[pack].vsix)
                m = json.loads(zip.open("extension/package.json").read())
                new_extension_ids.update(m["extensionPack"])
                zip.close()
                logging.debug(f'pack {pack} has {len(m["extensionPack"])} extension(s)')

            new_extension_ids.difference_update(all_extension_ids)

            # download new found extensions
            assets, packs = self.find_assets(new_extension_ids)
            self.download_vsix_files(assets.values())

            all_assets.update(assets)

        all_assets_list = list(all_assets.values())

        for asset in all_assets_list:
            if asset.name == "vadimcn.vscode-lldb":
                a = vscode_lldb(asset, self.dest_dir)
                self.download_vsix_files(a)
                asset.ignore = True
                all_assets_list.extend(a)
                break

        print(f"downloaded {len(all_assets_list)} vsix")

        all_extension_ids = set(asset.name.casefold() for asset in all_assets_list)
        missing = set(map(str.casefold, extension_ids)).difference(all_extension_ids)
        if missing:
            logging.error(f"extensions not found: {missing}")

        self.all_assets_list = all_assets_list

    def download_vsix_files(self, assets: t.List[Asset]):
        """
        Download extension archive (VSIX).
        """

        for asset in assets:

            vsix = self.dest_dir / asset.vsix
            if not vsix.exists():

                vsix.parent.mkdir(parents=True, exist_ok=True)
                print(f"download {vsix}")

                r = requests.get(asset.uri)
                vsix.write_bytes(r.content)

                mtime_ns = int(datetime.fromisoformat(asset.timestamp).timestamp() * 1_000_000_000)
                os.utime(vsix, ns=(mtime_ns, mtime_ns))
            else:
                if asset.platform:
                    logging.debug(f"already downloaded: {asset.name} {asset.version} ({asset.platform})")
                else:
                    logging.debug(f"already downloaded: {asset.name} {asset.version}")

    def find_assets(self, extension_ids: t.Iterable[str]) -> t.Tuple[t.Dict[str, Asset], t.Set[str]]:
        """Build the list of extensions to download."""

        assets = dict()
        packs = set()

        if extension_ids:

            # do the request to extension server
            # result is an array of extensions
            r = self.do_extension_query(extension_ids)

            for result in r["results"]:
                for extension in result["extensions"]:
                    extension_assets = self.parse_extension_details(extension)
                    if extension_assets:
                        assets.update(extension_assets)

                        # if the category is "Extension Packs" we will analyze the extension manifest
                        # to find which extensions are in the pack to download them too
                        if "Extension Packs" in extension["categories"]:
                            packs.update(extension_assets.keys())

            logging.debug(f"found {len(assets)} extension(s) and {len(packs)} pack(s)")

        return assets, packs

    def do_extension_query(self, extension_ids: t.Iterable[str]):
        """
        Make the HTTP request to the extension server, with::
           - assets uri (Flags.IncludeAssetUri)
           - details (Flags.IncludeVersionProperties)
           - categories (Flags.IncludeCategoryAndTags)
        """
        data = {
            "filters": [
                {
                    "criteria": [
                        {
                            "filterType": FilterType_Target,
                            "value": "Microsoft.VisualStudio.Code",
                        },
                        {
                            "filterType": FilterType_ExcludeWithFlags,
                            "value": str(Flags_Unpublished),
                        },
                        # {
                        #     "filterType": FilterType_ExtensionName,
                        #     "value": args.slug,
                        # },
                    ]
                }
            ],
            "flags": Flags_IncludeAssetUri + Flags_IncludeVersionProperties + Flags_IncludeCategoryAndTags,
        }

        for extension_id in sorted(extension_ids):
            data["filters"][0]["criteria"].append({"filterType": FilterType_ExtensionName, "value": extension_id})

        data = json.dumps(data)

        hash = f"{zipfile.crc32(data.encode()):04x}"
        cache = Path(f"response_{hash}.json")
        if cache.is_file():
            logging.debug(f"load cached response {cache}")
            r = json.loads(cache.read_bytes())
        else:
            r = requests.post(
                "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json;api-version=3.0-preview.1",
                },
            )
            if self.verbose:
                Path(f"query_{hash}.json").write_text(data)
                cache.write_bytes(r.content)
                logging.debug(f"write query and response {cache}")
            r = r.json()

        return r

    def parse_extension_details(self, extension: dict) -> t.Dict[str, Asset]:
        """
        Parse the response of the query to the server to find the download links for the extension.

        Return a list of all .vsix filenames according to target platforms
        and fill the self.download_assets map

        The response is a JSON object with an array containing elements like this:

        ```json
        {
            "publisher": {
                "publisherId": "uuid",
                "publisherName": "ms-python",                   // the left part of the extension identifier
                "displayName": "Microsoft",
                "flags": "verified",
                "domain": "https://microsoft.com",
                "isDomainVerified": true
            },
            "extensionId": "uuid",
            "extensionName": "python",                          // the right part of the extension identifier
            "displayName": "Python",
            "flags": "validated, public",
            "lastUpdated": "2025-01-24T10:42:55.8Z",
            "publishedDate": "2016-01-19T15:03:11.337Z",
            "releaseDate": "2016-01-19T15:03:11.337Z",
            "shortDescription": "Python....",
            "versions": [
                {
                    "version": "2024.23.2025012401",            // the version
                    "win32-arm64",                              // target platform
                    "flags": "validated",
                    "lastUpdated": "2025-01-24T10:42:55.8Z",
                    "properties": [],                           // to filter by engine and exclude prerelease
                    "assetUri": "...",                          // the download link
                    "fallbackAssetUri": "..."
                },
            ],
            "categories": [],                                   // could be Extension packs: recursively look for extensions
            "tags": [],
            "deploymentType": 0
        },
        ```

        """

        name = extension["publisher"]["publisherName"] + "." + extension["extensionName"]

        def filter_version(extension, platform):
            has_target_platform = set()

            for version in extension["versions"]:
                # sanity check
                if version["flags"] != "validated" and version["flags"] != "none":
                    logging.fatal("flags should be 'validated' or 'none'")
                    print(json.dumps(version, indent=2))
                    exit()

                # do not use pre-release version
                v = get_property(version, "Microsoft.VisualStudio.Code.PreRelease")
                if v == "true":
                    continue

                # we have to match the engine version
                v = get_property(version, "Microsoft.VisualStudio.Code.Engine")
                if not (v and engine_match(v, self.engine)):
                    continue

                if version.get("targetPlatform") is not None:
                    assert version["targetPlatform"] in PLATFORMS
                    has_target_platform.add(version["version"])

                # we have to match the platform if asked and specified for the version
                if version["version"] in has_target_platform and platform and version.get("targetPlatform") != platform:
                    continue

                yield version

        def find_latest_version(extension, platform):
            versions = filter_version(extension, platform)
            versions = sorted(versions, key=lambda v: version_serial(v["version"]))
            if versions:
                return versions[-1]

        def find_version_vsix(extension: dict, platform: str) -> Asset:
            version = find_latest_version(extension, platform)

            if not version:
                logging.error(f"missing {platform} for {name}")
                return None
            asset_uri = version["assetUri"] + "/Microsoft.VisualStudio.Services.VSIXPackage"
            target_platform = version.get("targetPlatform")

            asset = Asset(
                name,
                version["version"],
                get_property(version, "Microsoft.VisualStudio.Code.Engine"),
                asset_uri,
                version["lastUpdated"],
                target_platform,
            )

            return asset

        assets = dict()

        for target_platform, wanted in PLATFORMS.items():
            if wanted:
                asset = find_version_vsix(extension, target_platform)
                if asset:
                    assets[asset.vsix] = asset

        return assets

    def prune(self):
        all_vsix = set(file.name for file in self.dest_dir.glob("*.vsix"))
        our_vsix = set(asset.vsix for asset in self.all_assets_list)

        for file in all_vsix.difference(our_vsix):
            logging.debug(f"purge {file}")
            (self.dest_dir / file).unlink()

    def assets(self) -> t.List[Asset]:
        return self.all_assets_list


def vscode_latest_version(channel="stable"):
    """Retrieve current VSCode version from Windows download link."""

    url = f"https://code.visualstudio.com/sha/download?build={channel}&os=win32-x64-archive"
    r = requests.get(url, allow_redirects=False)
    if r is None or r.status_code != 302:
        logging.fatal(f"request error {r}")
        exit(2)

    url = r.headers["Location"]
    m = re.search(r"/(\w+)/([a-f0-9]{40})/VSCode-win32-x64-([\d.]+).zip", url)
    if not m or m[1] != channel:
        logging.fatal(f"cannot extract vscode version from url {url}")
        exit(2)

    _, commit_id, version = m.groups()
    return version, commit_id


def compare_local(extension_ids: t.Iterable[str]):
    """
    Compare the list of desired extensions with the list of locally installed extensions.
    """
    set_installed = set(subprocess.check_output(["code", "--list-extensions"]).decode().split())
    set_wanted = set(extension_ids)

    # check the case
    set_installed_lowercase = set(map(str.lower, set_installed))
    for i in set_wanted:
        if i in set_installed:
            continue
        if i.casefold() in set_installed_lowercase:
            for j in set_installed:
                if j.casefold() == i.casefold():
                    logging.warning(f"Upper/lower case problem with {i}, should be {j}")
            return 2

    set3 = set_wanted.union(set_installed)
    color_wanted = "93"
    color_installed = "95"
    extension, color, a, b = "extension", "37", "config", "local"
    print(f"\033[1;3;{color}m{extension:<55}\033[{color_wanted}m{a:^9}\033[{color_installed}m{b:^9}\033[0m")

    for extension in sorted(set3):
        a = extension in set_wanted
        b = extension in set_installed
        color = "37"
        if not a and b:
            color = color_installed
        if a and not b:
            color = color_wanted
        a = "❌✅"[a]
        b = "❌✅"[b]

        # see explaination here:
        # https://wezfurlong.org/wezterm/hyperlinks.html#explicit-hyperlinks
        link = f"\033]8;;https://marketplace.visualstudio.com/items?itemName={extension}\033\\{extension}\033]8;;\033\\"
        link += " " * (55 - len(extension))

        print(f"\033[{color}m{link}\033[0m{a:^9}{b:^9}")

    return 0


def read_config(config_file: Path) -> defaultdict[str, set]:

    sections = defaultdict(set)

    if config_file and config_file.is_file():
        files = config_file.read_text()
        for section, extension_list in re.findall(r"(\w+_extensions)=\((.+?)\)", files, flags=re.DOTALL):
            for name in extension_list.splitlines():
                name = name.strip()
                if not name or name.startswith("#"):
                    continue

                # remove platform
                name = name.replace("-${arch}", "")
                for platform in PLATFORMS.keys():
                    name = name.replace(f"-{platform}", "")

                # remove version
                name = re.sub(r"\-(\d+)\.(\d+)\.(\d+)\.vsix$", "", name)

                # lower case
                name = name.casefold()

                sections[section].add(name)

    return sections


def write_assets_file(assets_file: Path, sections: defaultdict[str, set], assets: t.List[Asset]):

    group_by_platform=False

    def make_section(vsix: str) -> str:

        with StringIO() as f:
            extension_list = sections.get(vsix)
            if extension_list:
                print(f"{vsix}=(", file=f)

                for name in sorted(extension_list, key=str.casefold):

                    # all target platforms may not be in same version
                    all_platforms_same_version = 1 == len(
                        set(
                            asset.version
                            for asset in assets
                            if asset.platform and not asset.ignore and name.casefold() == asset.name.casefold()
                        )
                    )

                    for asset in sorted(assets, key=lambda asset: str(asset.platform)):
                        if asset.ignore or name.casefold() != asset.name.casefold():
                            continue

                        vsix = asset.vsix
                        if asset.platform and all_platforms_same_version and group_by_platform:
                            vsix = vsix.replace(asset.platform, "${arch}")
                            print(f"  {vsix}", file=f)
                            break
                        else:
                            print(f"  {vsix}", file=f)

                print(")", file=f, end="")

            return f.getvalue()

    if assets_file.is_file():
        inventory = re.sub(r"\b\w+_extensions=\((?:.+?)\)", "", assets_file.read_text(), flags=re.DOTALL)
        inventory = inventory.strip() + "\n\n"
    else:
        inventory = ""

    inventory += "\n\n".join(make_section(section) for section in sections) + "\n"

    # old = assets_file.with_suffix(".old")
    # if old.is_file():
    #     old.unlink()
    # if assets_file.is_file():
    #     assets_file.rename(old)

    assets_file.write_text(inventory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", help="verbose and debug info", action="store_true")
    parser.add_argument("-d", "--dest-dir", help="output dir", type=Path, default="latest")
    parser.add_argument("-e", "--engine", help="engine version", default="current")
    parser.add_argument("-c", "--config", help="configuration file", type=Path)
    parser.add_argument("--local", help="from local VS Code", action="store_true")
    parser.add_argument("--compare-local", action="store_true")
    parser.add_argument("-p", "--prune", help="prune old and unwanted extensions", action="store_true")
    parser.add_argument("ID", help="extension identifier", nargs="*")
    args = parser.parse_args()

    BRIGHT_GREEN, FADE, GREEN, RESET = (
        ("\033[1;32m", "\033[2m", "\033[32m", "\033[0m") if sys.stdout.isatty() else ("", "", "", "")
    )
    format = f"{GREEN}%(asctime)s{RESET}{FADE} - %(levelname)s - %(message)s{RESET}"
    datefmt = None  # "%H:%M:%S"
    if args.verbose:
        logging.basicConfig(format=format, datefmt=datefmt, level=logging.DEBUG)
    else:
        logging.basicConfig(format=format, datefmt=datefmt, level=logging.INFO)

    # get extension list from configuration file
    sections = read_config(args.config)

    # all extension identfiers we want to download
    extension_ids = set(args.ID)

    if args.local:
        extension_ids.update(subprocess.check_output(["code", "--list-extensions"]).decode().splitlines())

    for name, v in sections.items():
        extension_ids.update(v)
    sections["all_extensions"] = extension_ids

    if args.compare_local:
        exit(compare_local(extension_ids))

    if args.engine == "latest":
        args.engine, _ = vscode_latest_version()
        print(f"Using Visual Studio Code {BRIGHT_GREEN}{args.engine}{RESET} (latest)")

    elif args.engine == "current":
        f = args.dest_dir / "files"
        if not f.is_file():
            logging.error(f"Missing version file: {f}")
            exit(1)
        version = f.read_text()
        m = re.search(r"\bversion=(.+)\b", version)
        if not m:
            logging.error(f"Version not found in {f}")
            exit(1)
        args.engine = m[1].strip()
        print(f"Using Visual Studio Code {BRIGHT_GREEN}{args.engine}{RESET}")

    dest_dir = args.dest_dir
    dest_dir.mkdir(exist_ok=True, parents=True)

    exts = Extensions(args.engine, dest_dir, args.verbose)
    exts.run(extension_ids)

    if args.prune:
        exts.prune()

    write_assets_file(dest_dir / "files", sections, exts.assets())


if __name__ == "__main__":
    main()
