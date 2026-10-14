"""Presentación pura: no accede a archivos ni depende de Tkinter."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Row:
    name: str
    extension: str
    size: int
    modified: float = 0
    path: str = ''
    group: int = 0
    count: int = 0
    digest: str = ''


def format_size(size):
    value = float(size)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024 or unit == 'TiB':
            return f'{value:.2f} {unit}' if unit != 'B' else f'{size} B'
        value /= 1024


def format_date(timestamp):
    try:
        return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OSError, OverflowError):
        return 'Fecha fuera de rango'


def make_rows(result):
    def row(item, group=0, digest=''):
        return Row(Path(item.path).name, item.extension, item.size,
                   item.modified, item.path, group, digest=digest)
    return {
        'largest': tuple(row(item) for item in result.largest),
        'old': tuple(row(item) for item in result.old),
        'duplicates': tuple(row(item, index, digest)
                            for index, (digest, group) in enumerate(result.duplicates, 1)
                            for item in group),
        'extensions': tuple(Row(ext, ext, data['bytes'], count=data['count'])
                            for ext, data in result.extensions.items()),
    }


def filter_rows(rows, name='', extension=''):
    """Nombre por coincidencia parcial; extensión exacta con o sin punto."""
    name = name.strip().casefold()
    extension = extension.strip().casefold()
    if extension and not extension.startswith(('.', '(')):
        extension = '.' + extension
    return [row for row in rows if name in row.name.casefold()
            and (not extension or row.extension.casefold() == extension)]


def sort_rows(rows, column, descending=False):
    if column not in Row.__dataclass_fields__:
        raise ValueError('Columna desconocida')
    def key(row):
        value = getattr(row, column)
        return value.casefold() if isinstance(value, str) else value
    return sorted(rows, key=key, reverse=descending)


def page_rows(rows, page=0, page_size=500):
    if page_size < 1:
        raise ValueError('Tamaño de página inválido')
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    return rows[page * page_size:(page + 1) * page_size], page, pages
