"""Reproducible, dependency-contained payload for remote Claude input."""
import base64
import io
from pathlib import Path
import zipfile


def bundle(directory):
    directory = Path(directory)
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        paths = [directory / 'claude-auto.py', directory / 'claude-editor.py']
        paths += sorted((directory / 'claude-vendor').rglob('*'))
        for path in paths:
            if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                entry = zipfile.ZipInfo(str(path.relative_to(directory)))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o600 << 16
                archive.writestr(entry, path.read_bytes())
    return base64.b64encode(data.getvalue()).decode()
