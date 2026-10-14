"""Demostración exclusivamente ficticia y verificación de widgets Tk reales."""
import argparse
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import patch, Mock

from gui import Application


def create_fixture(source):
    source.mkdir()
    old = time.time() - 120 * 86400
    for index in range(510):
        path = source / f'informe_{index:03}.txt'
        path.write_bytes(f'Dato ficticio {index}'.encode())
        os.utime(path, (old, old))
    (source / 'copia_a.bin').write_bytes(b'contenido duplicado ficticio')
    (source / 'copia_b.bin').write_bytes(b'contenido duplicado ficticio')
    (source / 'imagen_ficticia.dat').write_bytes(b'x' * (1024 * 1024))


def verify(app, root, output):
    """Acciona botones sobre el temporal. Nunca recibe un origen personal."""
    failures = []
    steps = []
    notices = []
    start = time.monotonic()
    phase = 0
    heartbeat = [0]

    def tick():
        heartbeat[0] += 1
        if not app.closed:
            root.after(25, tick)

    def check():
        nonlocal phase
        try:
            if time.monotonic() - start > 25:
                raise AssertionError('Tiempo de espera excedido en demostración')
            if phase == 0:
                assert app.result is None and not app.busy
                assert str(app.analyze_button['state']) == 'normal'
                app.analyze_button.invoke()
                assert app.busy and str(app.export_button['state']) == 'disabled'
                steps.append('Sin análisis automático; botón inicia trabajo en segundo plano')
                phase = 1
            elif phase == 1 and not app.busy and len(app.filtered['old']) == 510:
                assert len(app.result.files) == 513
                assert len(app.result.duplicates) == 1
                assert len(app.tables['old'].get_children()) == 500
                assert app.metrics[0].get() == '513'
                assert app.selection.count == 0
                old_tree = app.tables['old']
                old_iid = old_tree.get_children()[0]
                old_tree.selection_set(old_iid)
                app.selection_action('old', 'mark')
                assert app.selection.count == 1
                assert old_tree.set(old_iid, 'review') == 'Marcado'
                duplicate_tree = app.tables['duplicates']
                copies = duplicate_tree.get_children()
                duplicate_tree.selection_set(copies[0])
                app.selection_action('duplicates', 'keep')
                assert duplicate_tree.set(copies[0], 'review') == 'Conservar'
                duplicate_tree.selection_set(copies[1])
                app.selection_action('duplicates', 'mark')
                assert app.selection.count == 2
                # Intento explícito de marcar también la copia protegida.
                duplicate_tree.selection_set(copies[0])
                app.selection_action('duplicates', 'mark')
                assert app.selection.count == 2
                app.change_page('old', 1)
                assert len(app.tables['old'].get_children()) == 10
                app.sort_table('largest', 'name')
                app.name_filter.set('copia')
                app.extension_filter.set('bin')
                phase = 2
            elif phase == 2 and len(app.filtered['duplicates']) == 2 and len(app.filtered['old']) == 0:
                assert len(app.tables['duplicates'].get_children()) == 2
                assert app.selection.count == 2
                marked = [iid for iid in app.tables['duplicates'].get_children()
                          if app.tables['duplicates'].set(iid, 'review') == 'Marcado']
                assert len(marked) == 1
                app.preview_button.invoke()
                assert app.preview_window is not None
                assert len(app.preview_tree.get_children()) == 2
                old_iid = next(iid for iid, path in app.preview_paths.items() if path.endswith('.txt'))
                app.preview_tree.selection_set(old_iid)
                app.preview_remove.invoke()
                assert app.selection.count == 1
                assert len(app.preview_tree.get_children()) == 1
                assert app.selection.total_bytes == len(b'contenido duplicado ficticio')
                steps.append('Marcas persistentes entre filtros y páginas; copia conservada protegida')
                steps.append('Vista previa real y retirada individual sin modificar archivos')
                steps.append('Indicadores, cuatro pestañas, paginación, ordenación y filtros')
                app.start_export(app.source.get())
                phase = 3
            elif phase == 3 and not app.busy:
                assert any(kind == 'error' for kind, _ in notices)
                assert not list(Path(app.source.get()).glob('*.csv'))
                steps.append('Salida dentro del origen rechazada')
                app.start_export(str(output))
                phase = 4
            elif phase == 4 and not app.busy:
                assert len(list(output.glob('*.csv'))) == 4
                app.start_export(str(output))
                phase = 5
            elif phase == 5 and not app.busy:
                assert len(list(output.glob('*.csv'))) == 8
                assert heartbeat[0] > 3
                steps.append('Exportación real, sin sobrescrituras y bucle Tk activo')
                app.preview_tree.selection_set(app.preview_tree.get_children()[0])
                app.preview_location.invoke()
                assert app.selection.count == 1
                phase = 50
            elif phase == 50 and not app.busy:
                app.explorer_backend.select_file.assert_called_once()
                tree = app.tables['duplicates']
                tree.selection_set(tree.get_children()[0])
                app.open_table_location('duplicates')
                phase = 51
            elif phase == 51 and not app.busy:
                assert app.explorer_backend.select_file.call_count == 2
                assert app.selection.count == 1
                steps.append('Ubicación simulada sin abrir Explorador ni alterar marcas')
                app.refresh_button.invoke()
                assert app.selection.count == 0
                assert app.preview_window is None
                assert not app.selection.retained
                phase = 6
            elif phase == 6 and not app.busy and app.result is not None:
                assert app.selection.count == 0
                assert not app.selection.retained
                assert str(app.preview_button['state']) == 'disabled'
                steps.append('Nuevo análisis invalida selección y cierra vista previa anterior')
                app.close()
                return
        except Exception as error:
            failures.append(str(error) or type(error).__name__)
            app.busy = False
            app.close()
            return
        root.after(50, check)

    def notice(kind):
        def record(title, text, **kwargs):
            notices.append((kind, text))
        return record

    with patch('gui.messagebox.showinfo', side_effect=notice('info')), \
         patch('gui.messagebox.showwarning', side_effect=notice('warning')), \
         patch('gui.messagebox.showerror', side_effect=notice('error')):
        root.after(25, tick)
        root.after(150, check)
        root.mainloop()
    if failures:
        raise AssertionError('; '.join(failures))
    for step in steps:
        print('OK:', step)


def main():
    parser = argparse.ArgumentParser(description='Demostración con datos ficticios, sin acceder a Descargas.')
    parser.add_argument('--verify', action='store_true', help='Verificación automática de la interfaz real con datos ficticios')
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='demo_ficticia_', dir=workspace) as directory:
        source = Path(directory) / 'archivos_ficticios'
        output = Path(directory) / 'reportes_ficticios'
        output.mkdir()
        create_fixture(source)
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}
        root = tk.Tk()
        explorer = Mock()
        app = Application(root, explorer_backend=explorer)
        app.set_source(str(source))
        if args.verify:
            verify(app, root, output)
        else:
            app.status.set('DEMOSTRACIÓN: Explorador simulado; 513 archivos ficticios. Pulsa Analizar archivos. Los datos temporales se retiran al cerrar.')
            root.mainloop()
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in source.iterdir()}
        assert before == after, 'Los archivos ficticios cambiaron'
    print('Demostración finalizada; archivos ficticios intactos y temporal retirado.')


if __name__ == '__main__':
    main()
