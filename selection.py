"""Selección de revisión en memoria; snapshots atómicos y bloqueo de mutaciones."""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import os
import threading
from types import MappingProxyType


def path_key(path):
    return os.path.normcase(os.path.abspath(path))


@dataclass(frozen=True)
class PreviewItem:
    path: str
    size: int
    reasons: tuple


@dataclass(frozen=True)
class SelectionSnapshot:
    revision: int
    selected: frozenset
    retained: tuple

    @property
    def stamp(self):
        return (self.revision, self.selected, self.retained)


class SelectionLockedError(ValueError):
    pass


def _mutation(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with self._lock:
            if self._frozen:
                raise SelectionLockedError('La selección está bloqueada durante la operación.')
            return function(self, *args, **kwargs)
    return call


class Selection:
    def __init__(self, result=None, source=None):
        self._lock = threading.RLock()
        self._frozen = False
        self.reset()
        if result is None or source is None:
            return
        root = path_key(source)
        for item in result.files:
            key = path_key(item.path)
            try:
                inside = os.path.commonpath([root, key]) == root and key != root
            except ValueError:
                inside = False
            if inside:
                self._files[key] = item
                self._reasons[key] = []
        for label, items in (('Entre los más pesados', result.largest),
                             ('Sin modificar durante más de 90 días; no indica importancia', result.old)):
            for item in items:
                key = path_key(item.path)
                if key in self._files:
                    self._reasons[key].append(label)
        for index, (_, members) in enumerate(result.duplicates, 1):
            all_keys = frozenset(path_key(item.path) for item in members)
            keys = all_keys & self._files.keys()
            if len(keys) < 2 or keys != all_keys:
                self._unsafe.update(keys)
                continue
            self.groups[index] = keys
            for key in keys:
                self._membership[key] = index
                self._reasons[key].append(f'Duplicado confirmado · Grupo {index}')

    @_mutation
    def reset(self):
        self._revision = getattr(self, '_revision', 0) + 1
        self._files = {}
        self._reasons = {}
        self._selected = set()
        self._membership = {}
        self._unsafe = set()
        self.groups = {}
        self._retained = {}

    @property
    def revision(self):
        with self._lock:
            return self._revision

    @property
    def selected(self):
        with self._lock:
            return frozenset(self._selected)

    @property
    def retained(self):
        with self._lock:
            return MappingProxyType(dict(self._retained))

    @property
    def count(self):
        with self._lock:
            return len(self._selected)

    @property
    def total_bytes(self):
        with self._lock:
            return sum(self._files[key].size for key in self._selected)

    def snapshot(self):
        with self._lock:
            return SelectionSnapshot(self._revision, frozenset(self._selected),
                                     tuple(sorted(self._retained.items())))

    @contextmanager
    def freeze(self):
        with self._lock:
            if self._frozen:
                raise SelectionLockedError('La selección ya está siendo utilizada.')
            snapshot = self.snapshot()
            self._frozen = True
        try:
            yield snapshot
        finally:
            with self._lock:
                self._frozen = False

    def is_selected(self, path):
        with self._lock:
            return path_key(path) in self._selected

    def is_retained(self, path):
        with self._lock:
            key = path_key(path)
            return self._retained.get(self._membership.get(key)) == key

    @_mutation
    def mark(self, paths):
        blocked = []
        for path in paths:
            key = path_key(path)
            if key not in self._files:
                blocked.append('Archivo fuera del inventario autorizado.')
                continue
            if key in self._unsafe:
                blocked.append('Grupo incompleto: vuelve a analizar antes de seleccionarlo.')
                continue
            if self.is_retained(path):
                blocked.append('Esta copia está elegida para conservar. Elige otra copia antes de marcarla.')
                continue
            group = self._membership.get(key)
            if group is not None and self.groups[group] <= self._selected | {key}:
                blocked.append(f'Grupo {group}: debe quedar al menos una copia sin marcar. Elige qué copia conservar.')
                continue
            if key not in self._selected:
                self._revision += 1
                self._selected.add(key)
        return blocked

    @_mutation
    def unmark(self, paths):
        for path in paths:
            key = path_key(path)
            if key in self._selected:
                self._revision += 1
                self._selected.discard(key)

    @_mutation
    def clear(self):
        self._revision += 1
        self._selected.clear()
        self._retained.clear()

    @_mutation
    def keep(self, path):
        key = path_key(path)
        group = self._membership.get(key)
        if group is None or key not in self._files:
            raise ValueError('Selecciona una única copia de un grupo duplicado válido.')
        self._revision += 1
        self._retained[group] = key
        self._selected.discard(key)

    def preview(self):
        with self._lock:
            return [PreviewItem(self._files[key].path, self._files[key].size,
                                tuple(self._reasons[key]) or ('Revisión manual',))
                    for key in sorted(self._selected)]
