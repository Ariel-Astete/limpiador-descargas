"""Inventario local de solo lectura."""
from collections import defaultdict
from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
import time

from hasher import calculate_sha256, is_reparse, signature, validate_path


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    modified: float
    extension: str
    identity: tuple = field(repr=False)


@dataclass
class Analysis:
    files: list = field(default_factory=list)
    largest: list = field(default_factory=list)
    old: list = field(default_factory=list)
    extensions: dict = field(default_factory=dict)
    duplicates: list = field(default_factory=list)
    errors: int = 0
    skipped: int = 0
    content_hashes: dict = field(default_factory=dict)
    source_path: str | None = None
    source_identity: tuple | None = None

    @property
    def total_bytes(self):
        return sum(item.size for item in self.files)


def validate_directories(source, output):
    source = validate_path(source)
    output = validate_path(output)
    if not stat.S_ISDIR(os.lstat(source).st_mode):
        raise ValueError('La carpeta de origen debe existir.')
    if os.path.exists(output) and not os.path.isdir(output):
        raise ValueError('La salida debe ser una carpeta.')
    try:
        inside = os.path.commonpath([os.path.normcase(source), os.path.normcase(output)]) == os.path.normcase(source)
    except ValueError:
        inside = False  # Unidades diferentes en Windows.
    if inside:
        raise ValueError('La salida debe estar fuera de la carpeta analizada.')
    return source, output


def analyze_directory(source, days=90, top=20, now=None):
    if days < 0 or top < 1:
        raise ValueError('Los días deben ser no negativos y el límite positivo.')
    source = validate_path(source)
    if not stat.S_ISDIR(os.lstat(source).st_mode):
        raise ValueError('El origen debe ser una carpeta.')
    info = os.lstat(source)
    result = Analysis(source_path=source, source_identity=(info.st_dev, info.st_ino,
                      getattr(info, 'st_birthtime_ns', None)))
    pending = [source]
    cutoff = (time.time() if now is None else now) - days * 86400
    while pending:
        directory = pending.pop()
        try:
            validate_path(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        info = os.lstat(entry.path)
                        if is_reparse(info):
                            result.skipped += 1
                        elif stat.S_ISDIR(info.st_mode):
                            pending.append(entry.path)
                        elif stat.S_ISREG(info.st_mode):
                            result.files.append(FileRecord(
                                entry.path, info.st_size, info.st_mtime,
                                Path(entry.name).suffix.lower() or '(sin extensión)',
                                signature(info)))
                        else:
                            result.skipped += 1
                    except OSError:
                        result.errors += 1
        except (OSError, ValueError):
            result.errors += 1
    result.files.sort(key=lambda item: item.path)
    result.largest = sorted(result.files, key=lambda item: (-item.size, item.path))[:top]
    result.old = [item for item in result.files if item.modified < cutoff]
    sizes = defaultdict(list)
    for item in result.files:
        stats = result.extensions.setdefault(item.extension, {'count': 0, 'bytes': 0})
        stats['count'] += 1
        stats['bytes'] += item.size
        sizes[item.size].append(item)
    for candidates in sizes.values():
        if len(candidates) < 2:
            continue
        hashes = defaultdict(list)
        for item in candidates:
            try:
                validate_path(item.path)
                if signature(os.lstat(item.path)) != item.identity:
                    result.errors += 1
                    continue
                digest = calculate_sha256(item.path)
                if digest is None or signature(os.lstat(item.path)) != item.identity:
                    result.errors += 1
                    continue
                hashes[digest].append(item)
            except (OSError, ValueError):
                result.errors += 1
        for digest, group in hashes.items():
            if len(group) > 1:
                result.duplicates.append((digest, group))
    return result


def capture_analysis_content(result, source):
    """Baselines SHA-256 antes de confirmar; se usa en el worker de GUI.

    No crea una instantánea atómica ni recupera bytes anteriores a la primera
    lectura. Las entradas fallidas se guardan como None para no reintentarlas.
    """
    root = os.path.normcase(os.path.abspath(source))
    for item in result.files:
        if item.path in result.content_hashes:
            continue
        result.content_hashes[item.path] = None
        try:
            path = os.path.normcase(os.path.abspath(item.path))
            if os.path.commonpath([root, path]) != root or path == root:
                continue
            validate_path(item.path)
            if signature(os.lstat(item.path)) != item.identity:
                continue
            digest = calculate_sha256(item.path)
            if digest is not None and signature(os.lstat(item.path)) == item.identity:
                result.content_hashes[item.path] = digest
        except (OSError, ValueError):
            continue
    return result
