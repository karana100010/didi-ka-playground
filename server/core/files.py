"""File storage for uploaded registers (and, later, generated workbooks and the demo cache).

Vercel Blob when BLOB_READ_WRITE_TOKEN is set. Locally, without it, files go under data/local/uploads/.
Bundled mock files ship with the deployment and are only read.
A stored file is described by a small dict kept in the core store: {'blob': url} | {'local': path} | {'bundled': name}.
"""
import re
import tempfile
from pathlib import Path

import settings


class FileError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


def safe_name(name):
    return _SAFE.sub('_', Path(name or 'file').name)[:80] or 'file'


def kind():
    return 'blob' if settings.BLOB_TOKEN else ('missing' if settings.HOSTED else 'local')


def put(key, data, content_type):
    if settings.BLOB_TOKEN:
        from vercel import blob
        try:
            result = blob.put(key, data, access=settings.BLOB_ACCESS, content_type=content_type, overwrite=True,
                              token=settings.BLOB_TOKEN)
        except blob.BlobError as error:
            raise FileError(f'Vercel Blob rejected the upload: {error}', 502) from error
        return {'blob': result.url}
    if settings.HOSTED:
        raise FileError('BLOB_READ_WRITE_TOKEN is not set. Add a Blob store to the Vercel project.', 503)
    path = settings.LOCAL / 'uploads' / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {'local': str(path.relative_to(settings.LOCAL))}


def read(ref):
    if 'bundled' in ref:
        return (settings.MOCK / safe_name(ref['bundled'])).read_bytes()
    if 'local' in ref:
        return (settings.LOCAL / ref['local']).read_bytes()
    if 'blob' in ref:
        from vercel import blob
        try:
            return blob.get(ref['blob'], access=settings.BLOB_ACCESS, token=settings.BLOB_TOKEN).content
        except blob.BlobError as error:
            raise FileError(f'Could not read the file from Vercel Blob: {error}', 502) from error
    raise FileError('Unknown file reference.', 500)


def local_path(ref, cache_key):
    """A filesystem path for the file, for libraries that need one (SQLite). Downloads Blob files to /tmp."""
    if 'bundled' in ref:
        return settings.MOCK / safe_name(ref['bundled'])
    if 'local' in ref:
        return settings.LOCAL / ref['local']
    path = Path(tempfile.gettempdir()) / 'didi-playground' / safe_name(cache_key)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(read(ref))
    return path


def delete(ref):
    if 'blob' in ref:
        from vercel import blob
        try:
            blob.delete(ref['blob'], token=settings.BLOB_TOKEN)
        except blob.BlobError:
            pass
    elif 'local' in ref:
        (settings.LOCAL / ref['local']).unlink(missing_ok=True)
