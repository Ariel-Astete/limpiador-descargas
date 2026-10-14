"""SHA-256 local con bloques de 1 MiB y controles de lectura."""
import hashlib
import os
import stat

BLOCK_SIZE = 1024 * 1024


def is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0)
        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def validate_path(filepath):
    """Rechaza redirecciones en los componentes existentes, sin resolverlas."""
    path = os.path.abspath(filepath)
    current = path
    while True:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            pass
        else:
            if is_reparse(info):
                raise ValueError('No se permiten puntos de redirección.')
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return path


def signature(info):
    # En Windows ctime puede variar por metadatos diferidos al abrir.
    stable = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
              getattr(info, 'st_birthtime_ns', None))
    return stable if os.name == 'nt' else stable + (info.st_ctime_ns,)


def calculate_sha256(filepath):
    """Devuelve None ante errores, redirecciones o cambios durante la lectura."""
    try:
        filepath = validate_path(filepath)
        before = os.lstat(filepath)
        if not stat.S_ISREG(before.st_mode):
            return None
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(filepath, flags)
        with os.fdopen(fd, 'rb') as handle:
            if signature(before) != signature(os.fstat(handle.fileno())):
                return None
            for block in iter(lambda: handle.read(BLOCK_SIZE), b''):
                digest.update(block)
            if signature(before) != signature(os.fstat(handle.fileno())):
                return None
        validate_path(filepath)
        if signature(before) != signature(os.lstat(filepath)):
            return None
        return digest.hexdigest()
    except (OSError, ValueError):
        return None
