"""CSV locales creados exclusivamente fuera del directorio analizado."""
import csv
from datetime import datetime, timezone
import os
import tempfile
import sys

from analyzer import validate_directories
from hasher import validate_path


def safe_cell(value):
    if isinstance(value, str) and value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')):
        return "'" + value
    return value


def file_row(item):
    return [item.path, item.size,
            datetime.fromtimestamp(item.modified, timezone.utc).isoformat(), item.extension]


def validate_report_destination(output):
    """Los datos del usuario deben permanecer fuera del paquete congelado."""
    if not getattr(sys, 'frozen', False):
        return
    destination = os.path.normcase(os.path.abspath(output))
    roots = [os.path.dirname(sys.executable)]
    internal = getattr(sys, '_MEIPASS', None)
    if internal:
        roots.append(internal)
    for root in roots:
        root = os.path.normcase(os.path.abspath(root))
        try:
            contained = os.path.commonpath((root, destination)) == root
        except ValueError:  # Unidades diferentes.
            contained = False
        if contained:
            raise ValueError('Selecciona una carpeta de reportes fuera de la aplicación y sus archivos internos.')


def write_reports(result, source, output):
    _, output = validate_directories(source, output)
    validate_report_destination(output)
    os.makedirs(output, exist_ok=True)
    validate_path(output)
    header = ['ruta', 'bytes', 'modificado_utc', 'extension']
    reports = {
        'pesados': (header, [file_row(item) for item in result.largest]),
        'antiguos': (header, [file_row(item) for item in result.old]),
        'duplicados': (['grupo', 'sha256'] + header,
            [[index, digest] + file_row(item)
             for index, (digest, group) in enumerate(result.duplicates, 1) for item in group]),
        'extensiones': (['extension', 'cantidad', 'bytes'],
            [[ext, data['count'], data['bytes']]
             for ext, data in sorted(result.extensions.items())]),
    }
    created = []
    for name, (columns, rows) in reports.items():
        validate_directories(source, output)
        # Creación exclusiva: nunca sobrescribe reportes ni enlaces preexistentes.
        fd, path = tempfile.mkstemp(prefix=name + '_', suffix='.csv', dir=output)
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows([safe_cell(cell) for cell in row] for row in rows)
        created.append(path)
    return created
