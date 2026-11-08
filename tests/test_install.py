"""Installer behavior tests using real ASAR archives and isolated directories."""
import importlib.util
import json
import os
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('violet_install', Path(__file__).parents[1] / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
MAIN = 'let h={width:800,height:600,minWidth:200,minHeight:150,backgroundColor:"#00000000",trafficLightPosition:{x:19,y:12},show:!1,frame:a,titleBarStyle:b,webPreferences:{contextIsolation:!1,nodeIntegration:!0,nodeIntegrationInWorker:!0,spellcheck:!0,webviewTag:!0,devTools:c.devTools},...geometry(s)},f=new e.BrowserWindow(h);'


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='violet test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.vault = self.root / 'Vault with spaces'
        self.vault.mkdir()
        (self.config / 'niri').mkdir()
        (self.config / 'niri/config.kdl').write_text('// existing config\n')

    def archive(self, target, version='1.15.0', main=MAIN):
        source = self.root / 'source'
        source.mkdir(exist_ok=True)
        (source / 'package.json').write_text(json.dumps({'name': 'obsidian-dev', 'version': version}))
        (source / 'main.js').write_text(main)
        (source / 'untouched.txt').write_text('preserve this')
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(installer.ASAR), 'pack', str(source), str(target)], check=True, capture_output=True)
        return target

    def unpack(self, target):
        out = self.root / 'result'
        if out.exists():
            shutil.rmtree(out)
        subprocess.run([str(installer.ASAR), 'extract', str(target), str(out)], check=True, capture_output=True)
        self.assertEqual((out / 'untouched.txt').read_text(), 'preserve this')
        return (out / 'main.js').read_text()

    def test_first_install_from_system_copy_and_repeat(self):
        bundled = self.archive(self.root / 'system/obsidian.asar')
        original = bundled.read_bytes()
        target, status = installer.patch_host(self.config, bundled)
        self.assertEqual(status, 'patched')
        self.assertEqual(target, self.config / 'obsidian/obsidian-1.15.0.asar')
        self.assertEqual(self.unpack(target), installer.patch_main(MAIN))
        self.assertEqual(bundled.read_bytes(), original)
        installed = target.read_bytes()
        self.assertEqual(installer.patch_host(self.config, bundled)[1], 'already-transparent')
        self.assertEqual(target.read_bytes(), installed)
        self.assertFalse(list(self.root.rglob('*backup*')))

    def test_select_numeric_latest_and_reapply_after_update(self):
        older = self.archive(self.config / 'obsidian/obsidian-1.9.0.asar', '1.9.0')
        older_bytes = older.read_bytes()
        latest = self.archive(self.config / 'obsidian/obsidian-1.15.0.asar')
        self.assertEqual(installer.patch_host(self.config)[0], latest)
        self.assertEqual(older.read_bytes(), older_bytes)
        self.archive(latest)
        self.assertEqual(installer.patch_host(self.config)[1], 'patched')
        self.assertIn('transparent:!0', self.unpack(latest))

    def test_unknown_or_ambiguous_window_keeps_original(self):
        for source in ('new e.BrowserWindow({});', MAIN + MAIN):
            with self.subTest(source=source):
                target = self.archive(self.config / 'obsidian/obsidian-1.15.0.asar', main=source)
                before = target.read_bytes()
                with self.assertRaisesRegex(ValueError, 'missing or ambiguous'):
                    installer.patch_host(self.config)
                self.assertEqual(target.read_bytes(), before)

    def test_failed_install_returns_nonzero_without_enabling_glass(self):
        self.archive(self.config / 'obsidian/obsidian-1.15.0.asar', main='unknown window code')
        result = subprocess.run(
            [sys.executable, str(installer.PROJECT / 'scripts/install.py'), str(self.vault)],
            env=dict(os.environ, XDG_CONFIG_HOME=str(self.config)), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Apply Electron transparency patch failed', result.stderr)
        self.assertFalse((self.vault / '.obsidian').exists())
        self.assertFalse((self.config / 'niri/violet.kdl').exists())

    def test_enable_theme_preserving_other_preferences(self):
        obsidian = self.vault / '.obsidian'
        obsidian.mkdir()
        appearance = obsidian / 'appearance.json'
        appearance.write_text(json.dumps({'baseFontSize': 17, 'enabledCssSnippets': ['personal']}))
        installer.install_theme(self.vault)
        first = appearance.read_bytes()
        installer.install_theme(self.vault)
        self.assertEqual(first, appearance.read_bytes())
        settings = json.loads(first)
        self.assertEqual(settings['baseFontSize'], 17)
        self.assertEqual(settings['cssTheme'], 'Violet')
        self.assertEqual(settings['enabledCssSnippets'], ['personal', 'violet-native-glass'])
        self.assertEqual((obsidian / 'themes/Violet/theme.css').read_bytes(), (installer.PROJECT / 'theme.css').read_bytes())

    def test_add_niri_blur_and_include_once(self):
        installer.install_niri(self.config)
        path = self.config / 'niri/config.kdl'
        first = path.read_text()
        installer.install_niri(self.config)
        self.assertEqual(first, path.read_text())
        self.assertEqual(first.count('include "violet.kdl"'), 1)
        self.assertEqual(first.count('blur {'), 1)

    def test_preserve_existing_blur_in_included_file(self):
        directory = self.config / 'niri'
        (directory / 'effects.kdl').write_text('blur {\n passes 7\n}\n')
        path = directory / 'config.kdl'
        path.write_text('include optional=true "effects.kdl"\ninclude "violet.kdl"\nwindow-rule {\n blur {\n }\n}\n')
        before = path.read_text()
        installer.install_niri(self.config)
        self.assertEqual(path.read_text(), before)
        self.assertIn('passes 7', (directory / 'effects.kdl').read_text())

    def test_absolute_or_indirect_include_is_not_duplicated(self):
        directory = self.config / 'niri'
        rules = directory / 'rules.kdl'
        rules.write_text('include ' + json.dumps(str(directory / 'violet.kdl')) + '\n')
        config = directory / 'config.kdl'
        config.write_text('include "rules.kdl"\nblur { passes 3; }\n')
        before = config.read_text()
        installer.install_niri(self.config)
        self.assertEqual(config.read_text(), before)

    def test_comments_and_nested_blur_are_not_global_blur(self):
        path = self.config / 'niri/config.kdl'
        path.write_text('// blur { }\n/* blur { } */\nwindow-rule {\n blur { }\n}\n')
        installer.install_niri(self.config)
        self.assertEqual(sum(n[0] == 'blur' for n in installer.top_nodes(path.read_text())), 1)

    def test_migrate_only_matching_legacy_files(self):
        home = self.root / 'home'
        units = self.config / 'systemd/user'
        units.mkdir(parents=True)
        for name, content in installer.LEGACY_UNITS.items():
            (units / name).write_text(content)
        desktop = home / '.local/share/applications/md.obsidian.Obsidian.desktop'
        desktop.parent.mkdir(parents=True)
        desktop.write_text(installer.LEGACY_DESKTOP.format(home=home))
        launch = home / '.local/share/violet/native-glass/launch'
        launch.parent.mkdir(parents=True)
        launch.write_text(installer.LEGACY_LAUNCH)
        backup = home / '.local/state/violet/native-glass/backups/original.asar'
        backup.parent.mkdir(parents=True)
        backup.write_text('historical backup')
        with patch.object(installer.subprocess, 'run') as run:
            installer.migrate_legacy(self.config, home)
        self.assertFalse(any(units.iterdir()))
        self.assertFalse(launch.exists())
        self.assertIn('Exec=/usr/bin/obsidian %U', desktop.read_text())
        self.assertTrue(backup.exists())
        self.assertTrue(any('disable' in c.args[0] for c in run.call_args_list))
        for name in installer.LEGACY_UNITS:
            (units / name).write_text('user-customized unit')
        desktop.write_text('user-customized desktop')
        launch.write_text('user-customized launcher')
        with patch.object(installer.subprocess, 'run') as run:
            installer.migrate_legacy(self.config, home)
            run.assert_not_called()
        self.assertEqual(desktop.read_text(), 'user-customized desktop')
        self.assertEqual(launch.read_text(), 'user-customized launcher')
        launch.write_text(installer.LEGACY_LAUNCH)
        with patch.object(installer.subprocess, 'run'):
            installer.migrate_legacy(self.config, home)
        self.assertTrue(launch.exists(), 'Preserve launcher when the desktop entry was customized')


if __name__ == '__main__':
    unittest.main()
