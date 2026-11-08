from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('terminal_kit',ROOT/'scripts/build-terminal-kit.py')
kit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)

class TerminalKitTests(unittest.TestCase):
    def test_archive_is_reproducible_and_manifest_matches_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=kit.build(Path(temp)/'one')
            second=kit.build(Path(temp)/'two')
            self.assertEqual(archive.read_bytes(),second.read_bytes())
            manifest=json.loads((archive.parent/'manifest.json').read_text())
            self.assertEqual(manifest['sha256'],hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertFalse(manifest['compiled_binaries'])
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(set(bundle.namelist()),set(kit.FILES))
                for name in kit.FILES:
                    self.assertEqual(bundle.read(name),(ROOT/'connectors/terminal'/name).read_bytes())

    def test_linux_installer_preserves_existing_source_and_handles_spaces(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'Wine terminal data'
            experts=folder/'MQL5/Experts'
            experts.mkdir(parents=True)
            script=ROOT/'connectors/terminal/install-terminal.sh'
            args=['bash',str(script),'MT5',str(folder)]
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            target=experts/'COREIOSMini/COREIOSMiniTerminalCheck.mq5'
            target.write_text('preserve user source')
            result=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(next(target.parent.glob('*.backup.*')).read_text(),'preserve user source')
            self.assertEqual(target.read_bytes(),(ROOT/'connectors/terminal/COREIOSMiniTerminalCheck.mq5').read_bytes())
            result=subprocess.run(['bash',str(script),'MT4',str(folder)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((folder/'MQL4').exists())

if __name__=='__main__': unittest.main()
