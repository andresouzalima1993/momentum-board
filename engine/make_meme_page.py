"""One-off generator for the static meme.html tab (same header/nav/style as the board, no run-time badges)."""
import os, re, time, site_build
HERE = os.path.dirname(os.path.abspath(__file__))
h = site_build.build(os.path.join(HERE, '..', 'data'), int(time.time() * 1000), None, None, 'meme')
h = re.sub(r'<span class="badge badge-(green|red|blue)">[^<]*</span><span class="clock">[^<]*</span>', '<span class="badge badge-navy">Meme desk · paper</span>', h)
open(os.path.join(HERE, '..', 'meme.html'), 'w').write(h)
print('meme.html written', len(h))
