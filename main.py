"""CLI con carpeta explícita; no hay origen personal predeterminado."""
import argparse

from analyzer import analyze_directory, validate_directories
from reporter import write_reports


def main(argv=None):
    parser = argparse.ArgumentParser(description='Analizador local de archivos en modo solo lectura.')
    parser.add_argument('directory', help='Carpeta que se desea analizar explícitamente')
    parser.add_argument('--output', required=True, help='Carpeta externa para los CSV')
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument('--top', type=int, default=20)
    args = parser.parse_args(argv)
    try:
        source, output = validate_directories(args.directory, args.output)
        result = analyze_directory(source, days=args.days, top=args.top)
        reports = write_reports(result, source, output)
    except (OSError, ValueError):
        print('No se pudo completar: comprueba carpetas, permisos y parámetros.')
        return 1
    print(f'Archivos: {len(result.files)} | Bytes: {result.total_bytes}')
    print(f'Antiguos: {len(result.old)} | Grupos duplicados: {len(result.duplicates)}')
    print(f'Omitidos: {result.skipped} | Errores: {result.errors} | CSV: {len(reports)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
