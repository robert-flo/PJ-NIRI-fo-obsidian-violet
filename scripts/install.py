#!/usr/bin/env python3
"""Install Violet and its opt-in-by-command Linux/niri native glass integration."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
ASAR = PROJECT / 'node_modules/.bin/asar'
IDENT = r'[A-Za-z_$][\w$]*'
# Identify the main-window options and their constructor, not every BrowserWindow.
WINDOW = re.compile(
    rf'(?P<options>{IDENT})=\{{width:800,height:600,minWidth:200,minHeight:150,'
    rf'backgroundColor:"#00000000",trafficLightPosition:\{{x:19,y:12\}},show:!1,'
    rf'frame:{IDENT},titleBarStyle:{IDENT},webPreferences:\{{contextIsolation:!1,'
    rf'nodeIntegration:!0,nodeIntegrationInWorker:!0,spellcheck:!0,webviewTag:!0,'
    rf'devTools:{IDENT}\.devTools\}},\.\.\.{IDENT}\({IDENT}\)'
    rf'(?P<flag>,transparent:(?:!0|true))?(?P<end>\}}),{IDENT}=new {IDENT}\.BrowserWindow\((?P=options)\)'
)


def patch_main(source):
    matches = list(WINDOW.finditer(source))
    if len(matches) != 1:
        raise ValueError('Main-window patch location missing or ambiguous; update the adapter for this Obsidian version')
    match = matches[0]
    if match['flag']:
        return source
    return source[:match.start('end')] + ',transparent:!0' + source[match.start('end'):]


def version_key(path):
    return tuple(int(x) for x in path.stem.removeprefix('obsidian-').split('.'))


def patch_host(config, bundled=Path('/opt/Obsidian/resources/obsidian.asar')):
    user_dir = config / 'obsidian'
    packages = [p for p in user_dir.glob('obsidian-*.asar')
                if re.fullmatch(r'obsidian-\d+\.\d+\.\d+\.asar', p.name)]
    source = max(packages, key=version_key) if packages else bundled
    with tempfile.TemporaryDirectory(prefix='violet-install-') as temp:
        temp = Path(temp)
        unpacked = temp / 'app'
        subprocess.run([str(ASAR), 'extract', str(source), str(unpacked)], check=True)
        package = json.loads((unpacked / 'package.json').read_text())
        version = package['version']
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            raise ValueError(f'Unsupported application version: {version}')
        target = source if packages else user_dir / f'obsidian-{version}.asar'
        main = unpacked / 'main.js'
        before = main.read_text()
        after = patch_main(before)
        if before == after and packages:
            return target, 'already-transparent'
        main.write_text(after)
        packed = temp / 'patched.asar'
        subprocess.run([str(ASAR), 'pack', str(unpacked), str(packed)], check=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stage alongside the target so replacement stays on the same filesystem.
        with tempfile.TemporaryDirectory(prefix='.violet-install-', dir=target.parent) as staging:
            staged = Path(staging) / target.name
            shutil.copyfile(packed, staged)
            os.replace(staged, target)
    return target, 'patched'


# KDL tokenization is only used to find top-level blur/include nodes, including
# included files. Strings and comments cannot accidentally count as active rules.
TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|r(?P<hash>\#*)".*?"(?P=hash)|"(?:\\.|[^"\\])*"|[{};\n]|[^\s{};"/]+|/', re.S)


def top_nodes(text):
    depth = 0
    node = []
    for match in TOKENS.finditer(text):
        token = match[0]
        if token.startswith(('//', '/*')):
            continue
        if token == '{':
            if depth == 0 and node:
                yield node
                node = []
            depth += 1
        elif token == '}':
            depth -= 1
        elif depth == 0:
            if token in ('\n', ';'):
                if node:
                    yield node
                    node = []
            else:
                node.append(token)
    if node:
        yield node


def include_target(node, parent):
    if node[0] != 'include':
        return None
    quoted = next((t for t in node[1:] if t.startswith('"')), None)
    return (parent / json.loads(quoted)).resolve() if quoted else None


def niri_nodes(path, seen=None):
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen or not path.is_file():
        return
    seen.add(path)
    for node in top_nodes(path.read_text()):
        yield path, node
        child = include_target(node, path.parent)
        if child:
            yield from niri_nodes(child, seen)


def install_niri(config):
    directory = config / 'niri'
    path = directory / 'config.kdl'
    if not path.is_file():
        raise ValueError(f'Existing niri configuration required: {path}')
    text = path.read_text()
    additions = ''
    nodes = list(niri_nodes(path))
    if not any(node[0] == 'blur' for _, node in nodes):
        additions += '\n// Violet: compositor blur parameters\nblur {\n    passes 3\n    offset 3\n    noise 0.02\n    saturation 1.5\n}\n'
    included = any(include_target(node, origin.parent) == (directory / 'violet.kdl').resolve()
                   for origin, node in nodes)
    if not included:
        additions += '\ninclude "violet.kdl"\n'
    shutil.copyfile(PROJECT / 'integration/niri.kdl', directory / 'violet.kdl')
    if additions:
        path.write_text(text.rstrip() + '\n' + additions)
    return directory / 'violet.kdl'


def install_theme(vault):
    obsidian = vault / '.obsidian'
    appearance = obsidian / 'appearance.json'
    settings = json.loads(appearance.read_text()) if appearance.exists() else {}
    if not isinstance(settings, dict) or not isinstance(settings.get('enabledCssSnippets', []), list):
        raise ValueError(f'Invalid appearance settings: {appearance}')
    settings['cssTheme'] = 'Violet'
    snippets = settings.setdefault('enabledCssSnippets', [])
    if 'violet-native-glass' not in snippets:
        snippets.append('violet-native-glass')
    target = obsidian / 'themes/Violet'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('manifest.json', 'theme.css', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copyfile(PROJECT / name, target / name)
    (target / 'licenses').mkdir(exist_ok=True)
    shutil.copyfile(PROJECT / 'licenses/MACTAHOE.txt', target / 'licenses/MACTAHOE.txt')
    (obsidian / 'snippets').mkdir(exist_ok=True)
    shutil.copyfile(PROJECT / 'integration/native-glass.css', obsidian / 'snippets/violet-native-glass.css')
    appearance.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n')
    return target


LEGACY_UNITS = {
    'violet-obsidian-glass.path': """[Unit]
Description=Watch Obsidian application update packages

[Path]
PathChanged=%h/.config/obsidian
Unit=violet-obsidian-glass.service

[Install]
WantedBy=default.target
""",
    'violet-obsidian-glass.timer': """[Unit]
Description=Retry Obsidian transparency adaptation after missed or incomplete updates

[Timer]
OnStartupSec=30s
OnUnitInactiveSec=5min
Unit=violet-obsidian-glass.service

[Install]
WantedBy=timers.target
""",
    'violet-obsidian-glass.service': """[Unit]
Description=Restore native transparency in compatible Obsidian updates

[Service]
Type=oneshot
ExecStartPre=/usr/bin/sleep 2
ExecStart=/usr/bin/python3 %h/.local/share/violet/native-glass/adapt.py --watch
TimeoutStartSec=120
""",
}
LEGACY_LAUNCH = '''#!/bin/sh
# Ensure downloaded updates are adapted before the official launcher loads them.
/usr/bin/python3 "$HOME/.local/share/violet/native-glass/adapt.py" --watch >> "$HOME/.local/state/violet/native-glass/launcher.log" 2>&1
exec /usr/bin/obsidian "$@"
'''
LEGACY_DESKTOP = """[Desktop Entry]
Name=Obsidian
Exec={home}/.local/share/violet/native-glass/launch %U
Terminal=false
Type=Application
Icon=obsidian
StartupWMClass=md.obsidian.Obsidian
Comment=Obsidian
MimeType=application/pdf;text/markdown;application/x-obsidian-canvas;application/x-obsidian-base;x-scheme-handler/obsidian;
Categories=Office;
"""


def migrate_legacy(config, home):
    unit_dir = config / 'systemd/user'
    removed = False
    for name, expected in LEGACY_UNITS.items():
        path = unit_dir / name
        if path.is_file() and path.read_text() == expected:
            if name.endswith(('.path', '.timer')):
                subprocess.run(['systemctl', '--user', 'disable', '--now', name], check=True)
            else:
                subprocess.run(['systemctl', '--user', 'stop', name], check=True)
            path.unlink()
            removed = True
    if removed:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    desktop = home / '.local/share/applications/md.obsidian.Obsidian.desktop'
    restored_desktop = LEGACY_DESKTOP.format(home=home).replace(
        f'Exec={home}/.local/share/violet/native-glass/launch %U', 'Exec=/usr/bin/obsidian %U')
    if desktop.is_file() and desktop.read_text() == LEGACY_DESKTOP.format(home=home):
        desktop.write_text(restored_desktop)
        if shutil.which('update-desktop-database'):
            subprocess.run(['update-desktop-database', str(desktop.parent)], check=True)
    launch = home / '.local/share/violet/native-glass/launch'
    if (not desktop.exists() or desktop.read_text() == restored_desktop) and launch.is_file() and launch.read_text() == LEGACY_LAUNCH:
        launch.unlink()


def main():
    if len(sys.argv) != 2:
        print('Usage: ./install.sh "/absolute/path/to/Obsidian Vault"', file=sys.stderr)
        return 1
    vault = Path(sys.argv[1])
    config = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    step = 'Read installation paths'
    try:
        if not vault.is_absolute() or not vault.is_dir():
            raise ValueError('Vault must be an existing absolute directory')
        if not (config / 'niri/config.kdl').is_file():
            raise ValueError(f'Existing niri configuration required: {config / "niri/config.kdl"}')
        step = 'Remove known legacy background maintenance'
        if config == Path.home() / '.config':
            migrate_legacy(config, Path.home())
        step = 'Apply Electron transparency patch'
        host, status = patch_host(config)
        print(f'Obsidian: {host} ({status})')
        step = 'Install Violet theme and enable glass snippet'
        theme = install_theme(vault)
        print(f'Theme: {theme}')
        step = 'Install niri glass rules'
        rules = install_niri(config)
        print(f'niri: {rules}')
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        print(f'{step} failed: {exc}', file=sys.stderr)
        return 1
    print('Installed Violet with native glass. Reopen Obsidian. After an app update, close it and run this command again.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
