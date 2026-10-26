#!/usr/bin/env python3
"""Build a self-contained macOS app from installed or source-built upstream binaries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# Ghostty chord, Herdr action, Kitty code point, Kitty modifier (1 + modifier bits).
KEYS = [
    ("super+t", "new_tab", ord("t"), 9),
    ("super+d", "split_vertical", ord("d"), 9),
    ("super+shift+d", "split_horizontal", ord("d"), 10),
    ("super+w", "close_pane", ord("w"), 9),
    ("super+shift+w", "close_tab", ord("w"), 10),
    ("super+enter", "zoom", 13, 9),
    ("super+b", "toggle_sidebar", ord("b"), 9),
    ("super+p", "goto", ord("p"), 9),
    ("super+comma", "settings", 44, 9),
    ("super+shift+n", "new_workspace", ord("n"), 10),
    ("super+shift+r", "rename_tab", ord("r"), 10),
    ("super+shift+slash", "help", 47, 10),
    ("super+left", "previous_tab", 57350, 9),
    ("super+right", "next_tab", 57351, 9),
    ("super+up", "previous_workspace", 57352, 9),
    ("super+down", "next_workspace", 57353, 9),
    ("super+alt+left", "focus_pane_left", 57350, 11),
    ("super+alt+right", "focus_pane_right", 57351, 11),
    ("super+alt+up", "focus_pane_up", 57352, 11),
    ("super+alt+down", "focus_pane_down", 57353, 11),
    ("super+f", "copy_mode", ord("f"), 9),
]


def profiles():
    ghostty = [
        "# Generated from scripts/build.py; key transport is Kitty CSI-u, never prefix injection.",
        "keybind = clear", "shell-integration = none", "window-save-state = never",
        "quit-after-last-window-closed = true", "confirm-close-surface = false",
        "auto-update = off", "title = Herdr Native", "font-size = 14",
        "unfocused-split-opacity = 1",
        "window-width = 140", "window-height = 42", "window-padding-x = 8",
        "window-padding-y = 6", "macos-option-as-alt = true",
        "keybind = super+c=copy_to_clipboard", "keybind = super+v=paste_from_clipboard",
        "keybind = super+q=quit", "keybind = super+h=toggle_visibility", "keybind = super+m=ignore",
        "keybind = super+equal=increase_font_size:1", "keybind = super+minus=decrease_font_size:1",
        "keybind = super+zero=reset_font_size", "keybind = super+ctrl+f=toggle_fullscreen",
        "keybind = super+shift+a=csi:97;10u",
    ]
    herdr = ["# App-specific direct shortcuts. Ctrl+B remains an optional fallback.", "onboarding = false", "[keys]"]
    aliases = {"comma": ",", "slash": "/", "left_bracket": "[", "right_bracket": "]"}
    for chord, action, code, modifiers in KEYS:
        arrows = {57350: "D", 57351: "C", 57352: "A", 57353: "B"}
        sequence = f"1;{modifiers}{arrows[code]}" if code in arrows else f"{code};{modifiers}u"
        ghostty.append(f"keybind = {chord}=csi:{sequence}")
        parts = chord.split("+")
        parts[-1] = aliases.get(parts[-1], parts[-1])
        herdr.append(f'{action} = "{"+".join(parts)}"')
    herdr.append('switch_tab = "super+1..9"')
    for number in range(1, 10):
        ghostty.append(f"keybind = super+{number}=csi:{ord(str(number))};9u")
    herdr.extend(['[[keys.command]]', 'key = "super+shift+a"', 'command = "herdr-native:new-agent"',
                  'type = "shell"', 'description = "New remote agent"'])
    herdr.extend(['[ui]', 'sidebar_collapsed_mode = "hidden"', '# Run once in each new tab; empty disables it. Example: new_tab_command = "pi"',
                  'new_tab_command = "pi"', 'prompt_new_tab_command = true'])
    return "\n".join(ghostty) + "\n", "\n".join(herdr) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ghostty-app", type=Path, default=Path("/Applications/Ghostty.app"))
    parser.add_argument("--herdr", type=Path, default=ROOT / "herdr/target/release/herdr")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/Herdr Native.app")
    parser.add_argument("--source-patch", type=Path, default=ROOT / "patches/herdr-native-complete.patch")
    args = parser.parse_args()
    if not (args.ghostty_app / "Contents/MacOS/ghostty").is_file() or not args.herdr.is_file():
        parser.error("Provide a built Ghostty.app and herdr executable")
    if args.output.exists():
        parser.error(f"Output already exists: {args.output}. Move it aside before rebuilding.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ditto", str(args.ghostty_app), str(args.output)], check=True)
    contents = args.output / "Contents"
    resources = contents / "Resources"
    shutil.copy2(args.herdr, contents / "MacOS/herdr")
    shutil.copytree(ROOT / "remote", resources / "remote", ignore=shutil.ignore_patterns("tests", "__pycache__"))
    shutil.copy2(ROOT / "scripts/remote.py", contents / "MacOS/herdr-remote")
    (contents / "MacOS/herdr-remote").chmod(0o755)
    ghostty_config, herdr_config = profiles()
    (resources / "ghostty.conf").write_text(ghostty_config)
    (resources / "herdr.toml").write_text(herdr_config)
    subprocess.run([str(args.herdr), "config", "check"], check=True,
                   env={**os.environ, "HERDR_CONFIG_PATH": str(resources / "herdr.toml")})
    subprocess.run([str(args.ghostty_app / "Contents/MacOS/ghostty"), "+validate-config",
                    f"--config-file={resources / 'ghostty.conf'}"], check=True)
    subprocess.run(["xcrun", "swiftc", str(ROOT / "native/Launcher.swift"), "-O", "-o",
                    str(contents / "MacOS/HerdrNative"), "-framework", "AppKit"], check=True)
    info_path = contents / "Info.plist"
    with info_path.open("rb") as file:
        info = plistlib.load(file)
    info.update(CFBundleExecutable="HerdrNative", CFBundleIdentifier="dev.herdr.native.local",
                CFBundleName="Herdr Native", CFBundleDisplayName="Herdr Native",
                CFBundleShortVersionString="0.4.0", CFBundleVersion="4")
    for key in ("CFBundleDocumentTypes", "CFBundleURLTypes", "SUFeedURL", "SUPublicEDKey", "SUEnableAutomaticChecks"):
        info.pop(key, None)
    with info_path.open("wb") as file:
        plistlib.dump(info, file)
    licenses = resources / "Upstream Licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("herdr", "ghostty"):
        shutil.copy2(ROOT / name / "LICENSE", licenses / f"{name}.txt")
    shutil.copy2(ROOT / "patches/herdr-native-agent-menu.patch", resources / "herdr-client.patch")
    shutil.copy2(ROOT / "patches/herdr-native-new-tab-command.patch", resources / "herdr-new-tab-command.patch")
    shutil.copy2(args.source_patch, resources / "herdr-complete.patch")
    provenance = {
        "herdr_client_source": subprocess.check_output(["git", "-C", str(ROOT / "herdr"), "rev-parse", "HEAD"], text=True).strip(),
        "herdr_client_patch_sha256": hashlib.sha256((resources / "herdr-client.patch").read_bytes()).hexdigest(),
        "herdr_new_tab_command_patch_sha256": hashlib.sha256((resources / "herdr-new-tab-command.patch").read_bytes()).hexdigest(),
        "herdr_complete_patch_sha256": hashlib.sha256((resources / "herdr-complete.patch").read_bytes()).hexdigest(),
        "herdr_binary_sha256": hashlib.sha256(args.herdr.read_bytes()).hexdigest(),
        "herdr": subprocess.check_output([str(args.herdr), "--version"], text=True).strip(),
        "ghostty": subprocess.check_output([str(args.ghostty_app / "Contents/MacOS/ghostty"), "+version"], text=True).strip(),
        "note": "Bundled binaries may differ from the reference source checkouts.",
    }
    (resources / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(args.output)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.output)], check=True)
    print(f"Built: {args.output}")


if __name__ == "__main__":
    main()
