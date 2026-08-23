import re

def parse(fmt, text):
    # Minimal compatibility shim sufficient for View8's fixed parser patterns.
    parts = fmt.split('{}')
    pat = '^' + '(.*?)'.join(re.escape(p) for p in parts) + '$'
    m = re.match(pat, text)
    return m.groups() if m else None
