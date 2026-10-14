"""Verifica el código del EXE desde un controlador externo; no se distribuye."""
import importlib.abc
import importlib.util
import marshal
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import Mock, patch

from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'dist'/'final'/'LimpiadorDeDescargas'
EXE=PACKAGE/'LimpiadorDeDescargas.exe'
archive=CArchiveReader(str(EXE))
pyz=archive.open_embedded_archive('PYZ.pyz')
NAMES={'gui','analyzer','hasher','presentation','selection','reporter','recycler','windows_recycler','explorer'}
for name in NAMES-{'gui'}:
    assert name in pyz.toc,name
assert not any(name=='tests' or name.startswith('tests.') or name in ('demo_gui','unittest') for name in pyz.toc)
assert not any(path.suffix.lower()=='.csv' or path.name in ('tests','reportes','demo_gui.py') for path in PACKAGE.rglob('*'))

class BundleLoader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def find_spec(self,fullname,path=None,target=None):
        if fullname in NAMES:return importlib.util.spec_from_loader(fullname,self)
    def create_module(self,spec):return None
    def exec_module(self,module):
        code=marshal.loads(archive.extract('gui')) if module.__name__=='gui' else pyz.extract(module.__name__)
        module.__file__=str(PACKAGE/'_internal'/(module.__name__+'.pyc'))
        exec(code,module.__dict__)

sys.meta_path.insert(0,BundleLoader())
sys.path.insert(0,str(ROOT))
import gui,recycler,reporter
from demo_gui import create_fixture,verify
from explorer import LocationRequest,locate_file
from analyzer import analyze_directory,capture_analysis_content
assert gui.Application.__module__=='gui'
if '--explorer-ficticio' in sys.argv:
    import json
    state=json.loads((ROOT/'build'/'verification-state.json').read_text(encoding='utf-8'))
    source=Path(state['source'])
    assert source.parent.name.startswith('LimpiadorEXE_ficticio_')
    before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in source.iterdir()}
    result=analyze_directory(source)
    capture_analysis_content(result,source)
    record=next(item for item in result.files if item.path.endswith('copia_a.txt'))
    request=LocationRequest(str(source),result.source_identity,record,result.content_hashes[record.path])
    print(locate_file(request))
    after={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in source.iterdir()}
    assert before==after
    print('OK: navegación nativa con código del paquete y archivo exclusivamente ficticio; sin limpieza')
    sys.exit(0)
with tempfile.TemporaryDirectory(dir=ROOT/'build',prefix='verificacion_bundle_') as directory:
    source=Path(directory)/'ficticios';output=Path(directory)/'csv';output.mkdir()
    create_fixture(source)
    before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in source.iterdir()}
    for guard in (recycler.require_real_backend_enabled,):
        try:guard()
        except recycler.BackendDisabled:pass
        else:raise AssertionError('Backend habilitado')
    with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(EXE)),patch.object(sys,'_MEIPASS',str(PACKAGE/'_internal'),create=True):
        try:reporter.validate_report_destination(PACKAGE/'_internal')
        except ValueError:pass
        else:raise AssertionError('Salida interna permitida')
        root=tk.Tk();app=gui.Application(root,explorer_backend=Mock())
        assert str(app.recycle_button['state'])=='disabled'
        app.set_source(str(source))
        original_update=app.update_selection_controls
        def protected_controls():
            original_update()
            assert str(app.recycle_button['state'])=='disabled'
        app.update_selection_controls=protected_controls
        verify(app,root,output)
    after={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in source.iterdir()}
    assert before==after
print('OK: código del archivo EXE, nueve módulos necesarios y sin pruebas/CSV empaquetados')
print('OK: barrera real y exportación interna rechazadas en el código distribuido')
print('ALCANCE: controlador Python externo; no equivale a automatizar el proceso EXE completo')
