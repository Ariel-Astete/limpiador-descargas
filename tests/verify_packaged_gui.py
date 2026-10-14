"""Controlador externo de prueba manual del EXE; no se incluye en el paquete."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'build'/'verification-state.json'
u=ctypes.WinDLL('user32',use_last_error=True)
CALLBACK=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
u.EnumWindows.argtypes=[CALLBACK,w.LPARAM];u.EnumWindows.restype=w.BOOL
u.EnumChildWindows.argtypes=[w.HWND,CALLBACK,w.LPARAM];u.EnumChildWindows.restype=w.BOOL
u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int];u.GetWindowTextW.restype=ctypes.c_int
u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,ctypes.c_int];u.GetClassNameW.restype=ctypes.c_int
u.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)];u.GetWindowThreadProcessId.restype=w.DWORD
u.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)];u.GetWindowRect.restype=w.BOOL
u.IsWindowVisible.argtypes=[w.HWND];u.IsWindowVisible.restype=w.BOOL
u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.SendMessageW.restype=w.LPARAM
u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.PostMessageW.restype=w.BOOL

def describe(hwnd):
    pid=w.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    text=ctypes.create_unicode_buffer(1024);cls=ctypes.create_unicode_buffer(100)
    u.GetWindowTextW(hwnd,text,len(text));u.GetClassNameW(hwnd,cls,len(cls))
    rect=w.RECT();u.GetWindowRect(hwnd,ctypes.byref(rect))
    return dict(hwnd=int(hwnd),pid=pid.value,text=text.value,cls=cls.value,rect=[rect.left,rect.top,rect.right,rect.bottom],visible=bool(u.IsWindowVisible(hwnd)))

def windows(pid):
    found=[]
    @CALLBACK
    def collect(hwnd,_):
        item=describe(hwnd)
        if item['pid']==pid and item['visible']:
            found.append(item)
            @CALLBACK
            def child(ch,_):
                ci=describe(ch)
                if ci['visible']:found.append(ci)
                return True
            u.EnumChildWindows(hwnd,child,0)
        return True
    u.EnumWindows(collect,0)
    return found

if __name__=='__main__':
    if sys.argv[1]=='start':
        temp=Path(tempfile.mkdtemp(prefix='LimpiadorEXE_ficticio_'))
        package=temp/'aplicacion'
        shutil.copytree(ROOT/'dist'/'final'/'LimpiadorDeDescargas',package)
        source=temp/'ficticios';source.mkdir()
        for name,data in [('grande.dat',b'x'*1048576),('copia_a.txt',b'duplicado ficticio'),('copia_b.txt',b'duplicado ficticio')]:
            path=source/name;path.write_bytes(data);old=time.time()-120*86400;os.utime(path,(old,old))
        output=temp/'csv';output.mkdir();working=temp/'cwd_vacio';working.mkdir()
        env=os.environ.copy()
        for key in ('VIRTUAL_ENV','PYTHONHOME','PYTHONPATH','TCL_LIBRARY','TK_LIBRARY'):env.pop(key,None)
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        process=subprocess.Popen([str(package/'LimpiadorDeDescargas.exe')],cwd=working,env=env)
        state=dict(pid=process.pid,package=str(package),source=str(source),output=str(output),working=str(working))
        STATE.write_text(json.dumps(state),encoding='utf-8')
        time.sleep(3)
    state=json.loads(STATE.read_text(encoding='utf-8'))
    print(json.dumps(windows(state['pid']),ensure_ascii=True,indent=2))
