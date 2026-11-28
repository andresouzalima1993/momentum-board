"""Meme Desk tab (paper simulation). Static shell; data comes client-side from the meme-desk-sim runner's desk.json
(same github.io origin), so the two runners never write to the same repo. Rendered with textContent only (token names are untrusted)."""

MEME_SECTION = r'''
<style>
.md-banner{background:#0f1f4b;color:#fff;border-radius:10px;padding:10px 14px;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:0 0 16px}
.md-p{color:#128a4b}.md-n{color:#c63b3b}.md-m{color:#6b7289}
.md-report{background:#0f1f4b;color:#e9edf7;border-radius:8px;padding:14px 16px;font-size:12.5px;white-space:pre-wrap;margin:0;font-family:ui-monospace,Menlo,Consolas,monospace}
.md-funnel{display:flex;flex-wrap:wrap;gap:8px;margin:6px 0 10px}.md-step{flex:1;min-width:110px;border:1px solid #e5e8ef;border-radius:8px;padding:8px 10px;background:#fff}
.md-step .k{font-size:11px;color:#6b7289;text-transform:uppercase;letter-spacing:.05em}.md-step .v{font-size:18px;font-weight:650}
#md-root table td,#md-root table th{white-space:nowrap}
</style>
<div id="md-root">
<div class="md-banner">Paper simulation · no wallet, no keys, no real orders · Solana memecoins · public read-only data</div>
<section class="intro"><h2>Meme desk: on-chain memecoins, four paper books</h2><p>A desk of coded "seats" scans new Solana pools every 5 minutes (SEARCH), checks contract and rug risk (RISK, WHALE),
measures order flow and pool depth (FLOW, LIQUIDITY), and a JUDGE answers four closed questions with a fixed buy gate. The same buy signals feed four separate paper books:
<b>MOMO</b> enters right after the verdict, <b>DIP</b> waits for a 15% pullback; <b>CONS</b> starts with US$ 1,000 and risks 1.5% per trade, <b>LADDER</b> starts with US$ 100,
bets big while small and de-risks as it grows. Goal of LADDER: a high multiple on a small bank, accepting a real chance of losing it.</p>
<p class="section-sub" id="md-status">Loading live data…</p></section>
<section class="panel"><h2>Books</h2><p class="section-sub">Multiple = (trading bank + locked profits) ÷ start. Unrealized P&amp;L is at liquidation value (after price impact, DEX fee and network fee). Drawdown is peak-to-trough of the total bank.</p><div class="table-wrap"><table id="md-books"></table></div></section>
<section class="panel"><h2>Growth, ruin and sample size</h2><p class="section-sub">How likely each book is to multiply or go to zero, from a bootstrap of its own closed trades through its sizing rules (100 trades). Median vs mean shows how much a few big winners carry the average. Under 30 trades the numbers are noise.</p><div class="table-wrap"><table id="md-growth"></table></div></section>
<section class="panel"><h2>De-risking ladder</h2><p class="section-sub">LADDER books: 30% of the bank per entry up to 3x, tapering to 6% by 10x, then the conservative rules. At each doubling 33% of the new gains are locked and never traded.</p><div class="table-wrap"><table id="md-ladder"></table></div><p class="section-sub" id="md-rungs"></p></section>
<section class="panel"><h2>Open positions</h2><p class="section-sub">Exits run on 1-minute bars: stop first if a bar hits both, gap-through fills, 1% slippage plus pool impact; liquidity gone = −100%; a top holder selling &gt;8% of supply in ~5 min triggers a whale exit.</p><div class="table-wrap"><table id="md-open"></table></div></section>
<section class="panel"><h2>Today's desk report</h2><p class="section-sub">The BRT day closes at midnight.</p><pre class="md-report" id="md-report"></pre></section>
<section class="panel"><h2>Leads funnel</h2><p class="section-sub">Today (BRT) and since the start of this version. Every lead needs an on-chain reason; one RISK hard fail kills it.</p><div class="md-funnel" id="md-f-today"></div><div class="md-funnel" id="md-f-all"></div></section>
<section class="panel"><h2>Calibration summary</h2><p class="section-sub">Buy confidence buckets vs results, and what happened to the leads the desk turned down (24h shadow trade with the same exit rules).</p><div class="table-wrap"><table id="md-cal"></table></div><div class="table-wrap"><table id="md-groups"></table></div></section>
<section class="panel"><h2>Recent trades</h2><p class="section-sub">Closed paper trades, after all modeled costs.</p><div class="table-wrap"><table id="md-trades"></table></div></section>
<p class="section-sub">Full desk with the verdict log: <a href="https://andresouzalima1993.github.io/meme-desk-sim/">meme-desk-sim</a>. Times in BRT.</p>
</div>
<script>
(function(){
  var SRC='/meme-desk-sim/desk.json';
  function el(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!==undefined&&x!==null)e.textContent=String(x);return e;}
  function n2(x,d){if(x===null||x===undefined||isNaN(x))return '–';return Number(x).toLocaleString('en-US',{minimumFractionDigits:d===undefined?2:d,maximumFractionDigits:d===undefined?2:d});}
  function usd(x,s){if(x===null||x===undefined)return '–';var v=Number(x);return (s&&v>0?'+':'')+(v<0?'−':'')+'US$ '+n2(Math.abs(v));}
  function pc(x,s){if(x===null||x===undefined)return '–';var v=Number(x);return (s!==false&&v>0?'+':'')+n2(v,1)+'%';}
  function tone(v){return v>0?'md-p':(v<0?'md-n':'');}
  function fill(id,head,rows,empty){var t=document.getElementById(id);t.textContent='';var th=el('thead'),tr=el('tr');
    head.forEach(function(h,i){var c=el('th',i===0?'l':'',h);tr.appendChild(c);});th.appendChild(tr);t.appendChild(th);var tb=el('tbody');
    if(!rows.length){var r=el('tr','empty-row'),c=el('td','',empty||'None yet');c.colSpan=head.length;r.appendChild(c);tb.appendChild(r);}
    rows.forEach(function(r){var tr=el('tr');r.forEach(function(c,i){var cell=(typeof c==='object'&&c!==null)?c:{v:c};tr.appendChild(el('td',(i===0?'l ':'')+(cell.c||''),cell.v));});tb.appendChild(tr);});t.appendChild(tb);}
  function funnel(id,arr){var d=document.getElementById(id);d.textContent='';arr.forEach(function(p){var s=el('div','md-step');s.appendChild(el('div','k',p[0]));s.appendChild(el('div','v',n2(p[1],0)));d.appendChild(s);});}
  fetch(SRC+'?t='+Date.now(),{cache:'no-store'}).then(function(r){return r.json();}).then(function(J){
    document.getElementById('md-status').textContent=(J.healthy?'Runner healthy':'Runner stale')+' · data updated '+J.generated_brt+' BRT · last full cycle '+(J.last_cycle_brt||'–')+' BRT · running since '+J.started_brt+' BRT · refreshed every 10 min';
    fill('md-books',['Book','Start','Trading bank','Locked','Multiple','Max multiple','Return','Realized P&L','Unrealized P&L','Max drawdown','Trades','Win rate','Open','Sizing now'],
      J.books.map(function(b){var s=b.stats;return [b.name,usd(b.start),usd(b.trading),usd(b.locked),{v:n2(b.multiple)+'x',c:tone(b.multiple-1)},n2(b.max_multiple)+'x',{v:pc(b.return_pct),c:tone(b.return_pct)},
        {v:usd(b.realized,true),c:tone(b.realized)},{v:usd(b.unrealized,true),c:tone(b.unrealized)},{v:(b.max_dd_pct?'−':'')+n2(b.max_dd_pct,1)+'%',c:b.max_dd_pct?'md-n':''},s.n,
        s.n?n2(100*s.win_rate,0)+'%':'–',b.open+'/'+b.max_open+(b.pending?' (+'+b.pending+' waiting)':''),b.rung+' · '+n2(b.size_now_pct,1)+'% ('+usd(b.size_now_usd)+')'+(b.halted?' · HALTED':'')+(b.ruined?' · RUINED':'')];}));
    fill('md-growth',['Book','Closed','Mean trade','Median trade','Top-3 share of profit','90% CI mean P&L/trade','P(ruin)','Median multiple','Mean multiple','P(≥10x)','Read'],
      J.books.map(function(b){var s=b.stats,r=b.ruin;return [b.name,s.n,pc(s.mean_ret_pct),pc(s.median_ret_pct),s.top3_share!=null&&s.top3_share!==undefined?n2(100*s.top3_share,0)+'%':'–',
        s.n?usd(s.ci90_mean_pnl[0],true)+' … '+usd(s.ci90_mean_pnl[1],true):'–',r.ok?n2(100*r.p_ruin,0)+'%':'–',r.ok?n2(r.median_x)+'x':'–',r.ok?n2(r.mean_x)+'x':'–',r.ok?n2(100*r.p_10x,0)+'%':'–',s.n?s.verdict:(r.why||'no closed trades yet')];}));
    fill('md-ladder',['Rung','Multiple','Size per entry','Max open','Daily loss limit','Exits'],J.ladder.rungs.map(function(r){return [r.name,r.from_x+'x – '+(r.to_x?r.to_x+'x':'∞'),
        r.frac_start!==undefined?n2(100*r.frac_start,0)+'% → '+n2(100*r.frac_end,0)+'% of trading bank':'1.5% risk ÷ 30% stop (≈5% of bank)',r.max_open,n2(100*r.daily_loss,0)+'%',r.exits==='RUNNER'?'runner (25% at 3x, trail 40%, 15% moonbag)':'conservative'];}));
    document.getElementById('md-rungs').textContent=J.books.filter(function(b){return b.profile==='LADDER';}).map(function(b){return b.name+': '+b.rung+' at '+n2(b.multiple)+'x, trading '+usd(b.trading)+', locked '+usd(b.locked)+', next lock at '+usd(b.next_lock);}).join(' · ');
    fill('md-open',['Book','Token','Entry','Size','Return','Liq. value','Unrealized P&L','Stage'],J.open_positions.map(function(p){return [p.book,p.symbol,p.entry_brt,usd(p.size),{v:pc(p.ret_pct),c:tone(p.ret_pct)},usd(p.liq_value),{v:usd(p.unrealized,true),c:tone(p.unrealized)},p.stage];}),'No open positions');
    document.getElementById('md-report').textContent=J.report;
    funnel('md-f-today',J.funnel_today);funnel('md-f-all',J.funnel_all);
    fill('md-cal',['Buy p bucket','Buy signals','Closed (MOMO-CONS)','Win rate','24h checked','Shadow green'],J.calibration.buckets.map(function(r){return [r.bucket,r.signals,r.closed,r.closed?n2(100*r.wins/r.closed,0)+'%':'–',r.o24,r.o24?n2(100*r.green24/r.o24,0)+'%':'–'];}));
    var G=J.calibration.groups;fill('md-groups',['Turned-down group','Resolved','Shadow green','Dead in 24h','Median shadow'],Object.keys(G).map(function(k){var g=G[k];return [k,g.n,g.n?n2(100*g.green/g.n,0)+'%':'–',g.n?n2(100*g.dead/g.n,0)+'%':'–',pc(g.med)];}));
    fill('md-trades',['Book','Token','Exit','Reason','Cost','Return','Realized P&L'],J.recent_trades.map(function(t){return [t.book,t.symbol,t.exit_brt,t.reason,usd(t.cost),{v:pc(t.ret_pct),c:tone(t.ret_pct)},{v:usd(t.pnl,true),c:tone(t.pnl)}];}),'No closed trades yet');
  }).catch(function(){document.getElementById('md-status').textContent='Live data unavailable right now (the desk runner publishes every 10 minutes).';});
})();
</script>
'''
