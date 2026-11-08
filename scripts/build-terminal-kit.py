"""Create reproducible public installation assets from versioned terminal sources."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ('COREIOSMiniTerminalCheck.mq4', 'COREIOSMiniTerminalCheck.mq5',
         'install-terminal.sh', 'install-terminal.ps1', 'INSTALL.txt')


def build(destination=None):
    output = Path(destination) if destination else ROOT / 'ui/public/install'
    output.mkdir(parents=True, exist_ok=True)
    archive = output / 'corei-os-mini-terminal-kit-0.6.2.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as kit:
        for name in FILES:
            data = (ROOT / 'connectors/terminal' / name).read_bytes()
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            kit.writestr(info, data)
            (output / name).write_bytes(data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n')
    (output / 'manifest.json').write_text(json.dumps({
        'project_id': 'corei-os-mini', 'version': '0.6.2', 'status': 'LOCAL_DIAGNOSTICS_ONLY',
        'archive': archive.name, 'sha256': digest, 'files': list(FILES),
        'compiled_binaries': False, 'vps_bridge_available': False}, indent=2) + '\n')
    return archive


if __name__ == '__main__':
    print(f'Terminal source kit built: {build().name}')
