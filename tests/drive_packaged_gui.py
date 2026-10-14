"""Inspección de ventanas del proceso ficticio; no envía clics ni teclas."""
import json
from verify_packaged_gui import windows, STATE
if __name__ == '__main__':
    state=json.loads(STATE.read_text(encoding='utf-8'))
    print(json.dumps([item for item in windows(state['pid']) if item['cls']!='TkChild'],ensure_ascii=True,indent=2))
