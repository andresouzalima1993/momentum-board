#!/usr/bin/env python3
"""Publish guard: refuses to publish forbidden words, personal data, secrets, or an incomplete page. Exit 1 on any hit."""
import os, re, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
BANNED = 'const' + 'ellation'
bad = []
def files():
    for d in ('engine', 'data', '.github'):
        for dp, _, fs in os.walk(os.path.join(ROOT, d)):
            if '__pycache__' in dp: continue
            for fn in fs: yield os.path.join(dp, fn)
    for fn in ('index.html', 'robots.txt', 'README.md'):
        p = os.path.join(ROOT, fn)
        if os.path.exists(p): yield p
for p in files():
    try: t = open(p, encoding='utf-8', errors='ignore').read()
    except Exception: continue
    rel = os.path.relpath(p, ROOT)
    if BANNED in t.lower(): bad.append(f'{rel}: forbidden word')
    if re.search(r'\bAPP\b', t) and rel.startswith(('data', 'index')): bad.append(f'{rel}: excluded ticker present')
    if rel.startswith(('data', 'index')) or rel == 'robots.txt':
        if re.search(r'andré de souza|andre de souza|andresouzalima1993@|hotmail\.com|gmail\.com|outlook\.com|yahoo\.com', t, re.I): bad.append(f'{rel}: name or e-mail')
        if re.search(r'gho_[A-Za-z0-9]+|ghp_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|BEGIN [A-Z ]*PRIVATE KEY|0x[0-9a-fA-F]{40}', t): bad.append(f'{rel}: token, key or wallet address')
idx = os.path.join(ROOT, 'index.html')
if os.path.exists(idx):
    h = open(idx, encoding='utf-8').read()
    if 'name="robots" content="noindex,nofollow"' not in h: bad.append('index.html: meta robots missing')
    if 'Strategy rules' not in h or 'Open positions' not in h: bad.append('index.html: page looks incomplete')
if bad:
    print('GUARD REFUSED:\n  ' + '\n  '.join(bad)); sys.exit(1)
print('guard ok')
