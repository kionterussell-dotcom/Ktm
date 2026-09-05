let SPORT = 'nfl';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function tick(){
  $('clock').textContent = new Date().toLocaleString('en-US',
    {weekday:'short', hour:'2-digit', minute:'2-digit', timeZoneName:'short'});
}
setInterval(tick, 1000); tick();

function setSport(s){
  SPORT = s;
  $('t-nfl').className = s === 'nfl' ? 'on' : '';
  $('t-cfb').className = s === 'cfb' ? 'on' : '';
  load();
}

const kickoff = iso => new Date(iso).toLocaleTimeString('en-US',
  {hour:'numeric', minute:'2-digit'});

function pctCell(m){
  if(!m) return '<div class="cell"><span class="none">—</span></div>';
  const t = m.ticket == null ? null : Math.round(m.ticket);
  const h = m.handle == null ? null : Math.round(m.handle);
  const parts = [];
  if(t != null) parts.push(`<b>${t}%</b>`);
  if(h != null) parts.push(`<i>${h}%</i>`);
  return `<div class="cell">
    <div class="side">${esc(m.side)}</div>
    <div class="pcts">${parts.join(' / ') || '<span class="none">—</span>'}</div>
    <div class="pbars">
      <span class="pbar t"><i style="width:${t ?? 0}%"></i></span>
      <span class="pbar h"><i style="width:${h ?? 0}%"></i></span>
    </div></div>`;
}

function gameCard(g){
  const flagged = g.signals.some(s => s.strength >= 4);
  const rows = g.grid.map(b => `
    <tr class="${b.have ? '' : 'dim'}">
      <td class="bookname">${esc(b.book)}${b.paste_only ? '<span class="badge">PASTE</span>' : ''}</td>
      <td>${pctCell(b.markets.spread)}</td>
      <td>${pctCell(b.markets.total)}</td>
      <td>${pctCell(b.markets.moneyline)}</td>
    </tr>`).join('');

  const sigs = g.signals.length
    ? g.signals.map(s => `<div class="sig s${s.strength}">
        <span class="lab">${esc(s.label)}</span>
        <span class="mkt">${esc(s.market)}</span>
        <span class="txt">${esc(s.detail)}</span></div>`).join('')
    : `<div class="nosig">NO SIGNAL${g.has_any_splits ? '' : ' — no split data for this game'}</div>`;

  const notes = g.manual_notes.map(n => `<div class="sig s3">
      <span class="lab">${esc(n.source_account)}</span>
      <span class="txt">${esc(n.note)}</span></div>`).join('');

  return `<article class="game ${flagged ? 'flagged' : ''}">
    <div class="ghead">
      <span class="kick num">${kickoff(g.kickoff_utc)}</span>
      <span class="match">${esc(g.away)}<span class="at">@</span>${esc(g.home)}</span>
      <span class="venue">${esc(g.venue || '')}${g.is_dome ? ' · dome' : ''}</span>
      <span class="vstate ${g.verification}" title="${esc(g.verification_why)}">${g.verification}</span>
    </div>
    <table class="grid">
      <tr><th>Book</th><th class="m">Spread</th><th class="m">Total</th><th class="m">Moneyline</th></tr>
      ${rows}
    </table>
    <div class="legend"><b>gold = ticket %</b> &nbsp;·&nbsp; <i>blue = handle %</i>
      &nbsp;·&nbsp; per-book splits, never averaged across books</div>
    <div class="signals">${sigs}${notes}</div>
  </article>`;
}

function statusStrip(st){
  $('strip').innerHTML = st.sources.map(s => {
    const cls = s.status === 'OK' ? 'ok' : s.status === 'FAILED' ? 'failed'
              : s.status === 'EMPTY' ? 'empty' : 'notbuilt';
    const age = s.age == null ? '' : `<span class="age">${s.age}m</span>`;
    const cal = (s.built && !s.calibrated) ? ' ⚠' : '';
    return `<span class="chip ${cls}" title="${esc(s.detail || '')}">
      <span class="dot"></span>${esc(s.key)}${cal} ${age}</span>`;
  }).join('');

  $('banner').innerHTML = st.board_verified ? '' :
    `<div class="banner bad"><b>BOARD UNVERIFIED.</b>
      ${st.split_sources_live} of 2 required independent split sources returned.
      Per CLAUDE.md no signal may be flagged as a play until a second source confirms it.
      Everything below is shown for inspection only.</div>`;
}

function picks(p){
  const bets = p.bets.map(b => `<div class="pick">
      <div class="g">${esc(b.away)} @ ${esc(b.home)}
        <span class="tier ${b.bet_worthy ? '' : 'no'}">${esc(b.tier)}</span></div>
      <div class="line num">${esc(b.side)} ${b.number ?? ''} ${b.price} · edge ${b.edge > 0 ? '+' : ''}${b.edge}
        · breakeven ${b.breakeven}%</div>
      <div class="thr num">take at ${b.threshold} or better${b.bet_worthy ? ` · ${b.units}u` : ''}</div>
      ${b.bet_worthy ? '' : `<div class="rej">${esc(b.why_not)}</div>`}
    </div>`).join('');

  const flags = p.flags.map(f => `<div class="pick">
      <div class="g">${esc(f.away)} @ ${esc(f.home)}</div>
      <div class="s">${esc(f.signal.label)} · ${esc(f.signal.market)}</div>
    </div>`).join('');

  $('bets').innerHTML =
    (p.summary ? `<div class="hdr num">${esc(p.summary.headline)}${
      p.summary.exposure_units ? ` · ${p.summary.exposure_units}u exposure` : ''}</div>` : '') +
    bets +
    (p.bets_blocked ? `<div class="blocked"><b>NOT RANKED</b>${esc(p.bets_blocked)}</div>` : '') +
    flags;

  $('props').innerHTML = `<div class="blocked"><b>NOT BUILT</b>${esc(p.props_blocked)}
    <br><br>Odds floor when live: ${p.odds_floor} or better.</div>`;
}

async function load(){
  const d = $('date').value;
  const r = await fetch(`/api/slate?sport=${SPORT}${d ? '&date=' + d : ''}`).then(r => r.json());
  statusStrip(r.status);
  $('games').innerHTML = r.games.length ? r.games.map(gameCard).join('')
    : `<div class="empty"><h3>No games loaded for ${SPORT.toUpperCase()}${d ? ' on ' + d : ''}</h3>
       <p>Hit <b>Scan</b>, or run <code>python scripts/run_all.py ${SPORT} --fresh</code>.</p>
       <p>The slate comes from the schedule fetcher. If it shows FAILED above,
          nothing downstream can key to a game.</p></div>`;
  $('resources').innerHTML = r.resources.map(x =>
    `<div class="res"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}</a>
     <span class="tag">${esc(x.tag)}</span></div>`).join('');
  picks(await fetch(`/api/picks?sport=${SPORT}`).then(r => r.json()));
}

async function scan(){
  const btn = $('scanBtn');
  btn.disabled = true; btn.textContent = 'Scanning…';
  $('scanout').innerHTML = '';
  try{
    const d = $('date').value;
    const r = await fetch('/api/scan', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({sport: SPORT, date: d || null})}).then(r => r.json());
    $('scanout').innerHTML = `<h2 class="sec-title">Fetch log</h2>
      <pre class="out">${esc(r.stdout || r.stderr || 'no output')}</pre>`;
  } finally {
    btn.disabled = false; btn.textContent = 'Scan';
    load();
  }
}

load();
