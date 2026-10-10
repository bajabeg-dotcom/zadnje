/* Baja Music PA800 Manager · local-first UI, no external scripts. */
(() => {
  'use strict';
  const VIEWS = {overview:'Pregled',styles:'Style Library',performances:'Performance',pads:'PAD kolekcija',sounds:'Sound mapa',setup:'Setup & mape',validation:'Validacija'};
  const FAMILY = {styles:'Style',performances:'Performance',pads:'PAD'};
  const state = {view:'overview', workspace:null, file:null, resource:null, activeFileId:null,
    activeResourceId:null, element:0, search:'', soundSearch:'', sounds:[], soundTotal:0,
    soundStart:0, soundTab:'catalog', audit:null, busy:false, fileCache:new Map()};
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt = n => typeof n === 'number' ? n.toLocaleString('bs-BA') : '—';
  const bytes = n => n == null ? '—' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(2)} MB`;
  const icon = k => ({Style:'ST',Performance:'PF',PAD:'PD',Sound:'PC',Global:'GB',Mape:'TB'})[k] || 'PA';
  const notice = (txt, type='') => `<div class="notice ${type}">${esc(txt)}</div>`;
  let toastTimer;
  function toast(msg, error=false) {
    const item=el('toast'); item.textContent=msg; item.className=(error?'error ':'')+'show';
    clearTimeout(toastTimer); toastTimer=setTimeout(()=>item.className='',4200);
  }
  async function request(url, init={}) {
    const r=await fetch(url, {cache:'no-store',...init});
    const ct=r.headers.get('content-type')||'';
    const data=ct.includes('json') ? await r.json() : await r.text();
    if (!r.ok) throw new Error(data.error || r.statusText || 'Neuspješan zahtjev');
    return data;
  }
  function setBusy(on, msg='Obrađujem PA800 fajl…') {
    state.busy=on;
    if (on) el('view').innerHTML=`<div class="loading">${esc(msg)}</div>`;
  }
  function navUpdate() {
    document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===state.view));
    el('crumb-title').textContent=VIEWS[state.view]||VIEWS.overview;
  }
  function render() {
    navUpdate();
    if (!state.workspace) { el('view').innerHTML='<div class="loading">Učitavam radni prostor…</div>'; return; }
    const fn={overview:renderOverview,styles:()=>renderExplorer('styles'),performances:()=>renderExplorer('performances'),pads:()=>renderExplorer('pads'),sounds:renderSounds,setup:renderSetup,validation:renderValidation}[state.view];
    el('view').innerHTML=fn();
  }
  function pageTitle(kicker,title,subtitle,tools=''){
    return `<div class="page-top"><div><span class="eyebrow">${esc(kicker)}</span><h1>${esc(title)}</h1><p>${esc(subtitle)}</p></div><div class="page-controls">${tools}</div></div>`;
  }
  function fileOptions(kind, selected=state.activeFileId){
    const files=(state.workspace?.files||[]).filter(f=>f.kind===kind && !f.error);
    if (!files.length) return '';
    return `<select class="select" data-change="file" aria-label="Odaberi banku">${files.map(f=>`<option value="${f.id}" ${f.id===selected?'selected':''}>${esc(f.path)} (${fmt(f.resources)})</option>`).join('')}</select>`;
  }
  function emptyModule(label){
    return `<div class="panel"><div class="empty"><strong>Nema ${esc(label)} u ovom radnom prostoru</strong>Uvezi odgovarajući PA800 fajl ili ZIP sa SET/ALL folderom.<br><br><button class="button button-primary" data-action="upload">＋ Uvezi fajl</button> <button class="button" data-action="demo">Vrati demo</button></div></div>`;
  }
  function findFirstFile(kind){return state.workspace?.files.find(f=>f.kind===kind && !f.error)?.id ?? null;}
  function moduleKind(){return ({styles:'Style',performances:'Performance',pads:'PAD',sounds:'Sound',setup:'Global'})[state.view];}
  async function activateView(view){
    state.view=view;state.search='';state.element=0;navUpdate();
    if (view==='sounds' && state.soundTab==='catalog') {
      render(); if (!state.sounds.length) await loadSounds(); return;
    }
    const kind=moduleKind();
    if (kind){
      const desired=(state.file?.file?.kind===kind) ? state.activeFileId : findFirstFile(kind);
      if (desired!=null){await loadFile(desired);return;}
    }
    render();
  }
  async function loadFile(id){
    if (id==null){state.file=null;state.resource=null;state.activeFileId=null;render();return;}
    try{
      setBusy(true,'Čitam banku i provjeravam indeks…');
      state.activeFileId=Number(id); state.file=state.fileCache.get(Number(id));
      if (!state.file){state.file=await request(`/api/file?id=${encodeURIComponent(id)}`);state.fileCache.set(Number(id),state.file);}
      state.resource=null;state.activeResourceId=null;state.element=0;
      const family=FAMILY[state.view];
      const list=state.file.resources||[];
      const selected=family ? list.find(r=>r.family===family) :
        (state.view==='sounds' && state.soundTab==='pcg' ? list[0] : state.view==='setup' ? list[0] : null);
      if(selected) await loadResource(selected.index,false);
      render();
    }catch(e){toast(e.message,true);render();}
    finally{setBusy(false);}
  }
  async function loadResource(index, repaint=true){
    try{
      if(repaint)setBusy(true,'Čitam resurs i njegovu strukturu…');
      state.resource=await request(`/api/resource?file=${state.activeFileId}&resource=${Number(index)}`);
      state.activeResourceId=Number(index);state.element=0;
      if(repaint)render();
    }catch(e){state.resource=null;toast(e.message,true);if(repaint)render();}
    finally{if(repaint)setBusy(false);}
  }
  function workspaceStats(){
    const c=state.workspace.counts;
    const items=[
      {title:'Style resursi',value:c.styles,caption:'native STY banke',symbol:'▦',view:'styles'},
      {title:'Performance',value:c.performances,caption:'PRF objekti',symbol:'♫',view:'performances'},
      {title:'PAD fraze',value:c.pads,caption:'pattern & track sloj',symbol:'▤',view:'pads'},
      {title:'Factory Sound',value:state.workspace.soundCatalogCount,caption:'CC00 · CC32 · PC veze',symbol:'◎',view:'sounds'}];
    return `<div class="stats">${items.map(i=>`<div class="stat-card" data-view="${i.view}" role="button" tabindex="0"><span class="stat-label">${esc(i.title)}</span><span class="stat-ico">${i.symbol}</span><div class="stat-value">${fmt(i.value)}</div><div class="stat-caption">${esc(i.caption)}</div></div>`).join('')}</div>`;
  }
  function renderOverview(){
    const w=state.workspace, c=w.counts;
    const fileRows=w.files.slice(0,8).map(f=>`<button class="file-row" data-action="select-file" data-id="${f.id}"><span class="file-mark ${f.error?'':'gold'}">${esc(icon(f.kind))}</span><span class="file-meta"><b>${esc(f.path)}</b><small>${esc(f.kind)} · ${bytes(f.bytes)}${f.error?' · problem sa strukturom':''}</small></span><span class="file-count">${fmt(f.resources)} obj.</span><span class="row-arrow">›</span></button>`).join('');
    return `<div class="hero"><div class="hero-copy"><span class="eyebrow">BAJA MUSIC / DIGITALNA RADIONICA</span><h1>Tvoj PA800.<br><em>Svaki resurs na svom mjestu.</em></h1><p>Otvaraj stvarne STY, PRF, PAD, PCG i Global banke. Pregledaj strukturu, sound adrese i status validacije — bez diranja originalnih podataka.</p><div class="hero-actions"><button class="button button-primary" data-action="upload">＋ Uvezi SET / ALL ZIP</button><button class="button" data-view="styles">Istraži Style banke <span>↗</span></button></div></div><div class="hero-graphic"><div class="disk"><span class="orbit-dot"></span><div class="disk-inner"><b>800</b><small>ARRANGER ENGINE</small></div><span class="disk-code mono">OC20 · KBEG/KEND · OC31/32</span></div></div></div>
    ${workspaceStats()}
    <div class="section-heading"><div><h2>Radni prostor</h2><p>${esc(w.label)} · ${fmt(c.validFiles)} / ${fmt(c.files)} banki strukturno otvoreno</p></div><button class="text-action" data-action="demo">Vrati factory demo ↗</button></div>
    <div class="layout-two"><div class="panel"><div class="panel-header"><span class="panel-title">Učitani fajlovi</span><span class="panel-sub">Izvor neizmijenjen</span></div>${fileRows||'<div class="empty">Uvezi fajl za početak.</div>'}${w.files.length>8?`<div class="panel-body muted">Još ${fmt(w.files.length-8)} fajlova u ZIP paketu. Izaberi modul lijevo za pregled.</div>`:''}</div>
    <div class="panel"><div class="panel-header"><span class="panel-title">Šta je potvrđeno</span><span class="badge">EVIDENCE FIRST</span></div><div class="checkpoint"><div class="checkpoint-row"><span class="check-circle">✓</span><div><b>Resource bank reader</b><small>OC20 stranice, KBEG/KEND i 24-byte katalog</small></div></div><div class="checkpoint-row"><span class="check-circle">✓</span><div><b>Style · PAD · Performance</b><small>CV/PatternInfo, TrackInfo i release-2/3 geometrija</small></div></div><div class="checkpoint-row"><span class="check-circle">✓</span><div><b>Factory sound identiteti</b><small>1.003 tačne adrese iz dokaznog kataloga</small></div></div><div class="checkpoint-row"><span class="check-circle pending">!</span><div><b>Native snimanje na PA800</b><small>Za sada zaključano; OC20 footer i hardverski test su otvoreni.</small></div></div></div></div></div>
    <div class="section-heading"><div><h2>Metod rada</h2><p>Pouzdano razdvajamo potvrđene bajtove od otvorenih hipoteza.</p></div><button class="text-action" data-view="validation">Otvori validaciju ↗</button></div>
    <div class="notice blue">${esc(w.notice)} Fajlove koje uvezeš aplikacija zadržava samo u memoriji aktivne sesije. ${esc(w.note||'')}</div>`;
  }
  function resourcesFor(family){return (state.file?.resources||[]).filter(r=>r.family===family);}
  function resourceList(list){
    const q=state.search.trim().toLowerCase();
    const filtered=list.filter(r=>!q || r.name.toLowerCase().includes(q) || String(r.index+1)===q);
    const visible=filtered.slice(0,240);
    return `<div class="panel"><div class="panel-header"><div><span class="panel-title">Resursi</span><span class="panel-sub"> · ${fmt(filtered.length)} pronađeno</span></div><span class="badge gray">${fmt(list.length)}</span></div><div class="resource-list">${visible.map((r,n)=>`<button type="button" class="resource-item ${r.index===state.activeResourceId?'active':''}" data-action="resource" data-id="${r.index}"><span class="resource-number">${String(r.index+1).padStart(2,'0')}</span><span class="resource-desc"><b>${esc(r.name)}</b><small>${esc(r.codec)} · ${bytes(r.bytes)} · ${esc(r.family)}</small></span><span class="resource-chevron">›</span></button>`).join('')||'<div class="empty">Nema podudaranja.</div>'}</div>${filtered.length>visible.length?`<div class="panel-body subtle">Prikazano prvih ${visible.length} resursa. Suzi pretragu.</div>`:''}</div>`;
  }
  function tiny(k,v){return `<span class="tiny-info">${esc(k)} <strong>${esc(v)}</strong></span>`;}
  function trackTable(tracks){
    return `<div class="table-wrap"><table class="data-table"><thead><tr><th>Trag</th><th>Zvuk / identitet</th><th>CC00</th><th>CC32</th><th>PC</th><th>Glasnoća</th><th>Raspon</th></tr></thead><tbody>${(tracks||[]).map(t=>`<tr><td class="focus">${esc(t.role)}</td><td>${t.sound?esc(t.sound):'<span class="subtle">nije u Factory listi</span>'}</td><td class="mono">${t.msb}</td><td class="mono">${t.lsb}</td><td class="mono">${t.pc}</td><td>${t.level}</td><td class="mono small">${t.low}–${t.high}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function styleDetail(d){
    const elm=(d.elements||[])[Math.min(state.element,(d.elements||[]).length-1)];
    return `<div class="detail-crumb">${esc(d.family)} / IZVORNI OBJEKT #${d.index+1}</div><div class="detail-head"><div><h2>${esc(d.name)}</h2><p>${esc(d.objectName||'')} · ${esc(d.codec)} · ${bytes(d.decodedBytes)} nakon dekodiranja</p></div><span class="badge">STRUKTURA OK</span></div>
    <div class="summary-line">${tiny('Patterni',fmt(d.patterns))}${tiny('CV',fmt(d.cvCount))}${tiny('Elementi',fmt(d.elementCount))}${tiny('Asocijacije',fmt(d.associations))}</div>
    ${(d.warnings||[]).map(s=>notice(s)).join('')}
    <div class="detail-label">Style elementi ${d.family==='PAD'?'· zajednička struktura sa Style':''}</div><div class="pills">${(d.elements||[]).map((e,i)=>`<button class="pill ${i===state.element?'active':''}" data-action="element" data-id="${i}">${esc(e.name)} <span class="subtle">· ${e.cvs.length} CV</span></button>`).join('')}</div>
    ${elm?`<div class="detail-label">${esc(elm.name)} · Sound adrese 8 tragova</div>${trackTable(elm.tracks)}<div class="detail-label">Chord Variation zapisi</div><div class="cv-grid">${elm.cvs.map(c=>`<span class="cv-item mono">CV ${c.number} · ${fmt(c.ticks)} tick · ${c.references} ref</span>`).join('')||'<span class="subtle">Nema CV zapisa.</span>'}</div>`:''}
    <div class="mini-sep"></div><div class="notice blue">Sound naziv se prikazuje samo kad CC00/CC32/PC tačno odgovara Factory listi. Nepoznata adresa ostaje nepoznata. Original i neimenovani listovi nisu izmijenjeni.</div>
    <div style="margin-top:14px"><a class="button" href="/api/original?id=${state.activeFileId}">↓ Preuzmi neizmijenjeni ${esc(state.file?.file?.name||'fajl')}</a></div>`;
  }
  function performanceDetail(d){
    return `<div class="detail-crumb">PERFORMANCE / PRF OBJEKT #${d.index+1}</div><div class="detail-head"><div><h2>${esc(d.name)}</h2><p>${esc(d.codec)} · ${bytes(d.decodedBytes)} nakon dekodiranja</p></div><span class="badge ${d.variant?.includes('Drawbar')?'gold':''}">${esc(d.variant||'Samo čitanje')}</span></div>
    <div class="summary-line">${tiny('Veličina slota',`${d.slotBytes??'—'} B`)}${tiny('Sekcije',(d.sectionBytes||[]).join(' / ')+' B')}${tiny('Mixer zapisi',d.mixerRecords??'—')}${tiny('Release 3 dodatak',`${d.slotExtensionBytes??0} B`)}</div>
    <div class="detail-label">Sirovi 10-bajtni assignment zapisi</div><div class="code-block">${(d.mixerHex||[]).map((row,i)=>`TRACK ${i+1}  ${esc(row)}`).join('<br>')||'Ova PRF varijanta ostaje preserve-only.'}</div>
    ${d.drawbarCandidates?`<div class="detail-label">Drawbar · devet kandidatskih vrijednosti</div><div class="pills">${d.drawbarCandidates.map((v,i)=>`<span class="pill mono">#${i+1} · ${v}</span>`).join('')}</div><p class="muted" style="font-size:11px">${esc(d.drawbarCaution)}</p>`:''}
    <div class="detail-label">Politika za nepoznate podatke</div>${notice(d.preservation||d.note||'Sve neimenovane vrijednosti sačuvane kao sirovi podaci.', 'blue')}
    <div style="margin-top:18px"><a class="button" href="/api/original?id=${state.activeFileId}">↓ Preuzmi originalni PRF bank</a></div>`;
  }
  function explorerTools(kind){
    return `${fileOptions(kind)}<label class="search"><span>⌕</span><input type="search" data-search="resources" placeholder="Pretraži resurse…" value="${esc(state.search)}"></label>`;
  }
  function renderExplorer(view){
    const family=FAMILY[view];const w=state.workspace;
    const meta={styles:['NATIVE STYLE / STY','Style Library','Banke, elementi, osam tragova i Chord Variations.'],performances:['PERFORMANCE / PRF','Performance browser','Version-aware prikaz, uz čuvanje nepoznatih polja.'],pads:['PAD / ARRANGER CORE','PAD kolekcija','Dijeljena PatternInfo, TrackInfo i CV arhitektura.']}[view];
    const head=pageTitle(...meta,explorerTools(({styles:'Style',performances:'Performance',pads:'PAD'})[view]));
    const selected=state.file?.file?.kind===({styles:'Style',performances:'Performance',pads:'PAD'})[view];
    if (!selected) return head+emptyModule(family+' banki');
    const list=resourcesFor(family);const d=state.resource?.family===family?state.resource:null;
    return head+`<div class="notice green" style="margin-bottom:16px">${esc(state.file.file.name)} · ${fmt(list.length)} ${esc(family)} resursa · ${state.file.file.pages} OC20 stranica · KBEG/KEND indeks potvrđen.</div><div class="explorer">${resourceList(list)}<div class="panel detail-pane">${d?(view==='performances'?performanceDetail(d):styleDetail(d)):'<div class="empty"><strong>Izaberi resurs</strong>Odaberi stavku iz liste lijevo za detaljni pregled.</div>'}</div></div>`;
  }
  async function loadSounds(){
    try{
      const d=await request(`/api/sounds?q=${encodeURIComponent(state.soundSearch)}&start=${state.soundStart}`);
      if (state.soundStart===0)state.sounds=d.items;else state.sounds.push(...d.items);
      state.soundTotal=d.total; render();
    }catch(e){toast(e.message,true);}
  }
  function renderSounds(){
    const nav=`<div class="tabs"><button class="tab ${state.soundTab==='catalog'?'active':''}" data-action="sound-tab" data-id="catalog">Factory selector katalog <span class="subtle">· 1.003</span></button><button class="tab ${state.soundTab==='pcg'?'active':''}" data-action="sound-tab" data-id="pcg">PCG resursi</button></div>`;
    const top=pageTitle('SOUND / CC00 · CC32 · PC','Sound mapa','Potvrđeni Factory identiteti; korisničke banke bez automatskog nagađanja.',state.soundTab==='pcg'?`${fileOptions('Sound')}<label class="search"><span>⌕</span><input data-search="resources" type="search" placeholder="Pretraži PCG…" value="${esc(state.search)}"></label>`:`<label class="search"><span>⌕</span><input data-search="sounds" type="search" placeholder="Ime ili 121.0.0…" value="${esc(state.soundSearch)}"></label>`);
    if (state.soundTab==='pcg'){
      if(state.file?.file?.kind!=='Sound')return top+nav+emptyModule('PCG banki');
      const r=state.file.resources||[];const d=state.resource;
      return top+nav+`<div class="notice blue" style="margin-bottom:15px">${esc(state.file.file.name)} · ${fmt(r.length)} native resursa; neimenovani binarni listovi ostaju zaključani za upis.</div><div class="explorer">${resourceList(r)}<div class="panel detail-pane">${d?`<div class="detail-crumb">PCG / OBJEKT #${d.index+1}</div><div class="detail-head"><div><h2>${esc(d.name)}</h2><p>${esc(d.codec)} · ${bytes(d.decodedBytes)}</p></div><span class="badge blue">PRESERVE ONLY</span></div><div class="summary-line">${tiny('Tag',d.tag)}${tiny('SHA-256',d.decodedSha256?.slice(0,16)+'…')}</div><div class="detail-label">Početak dekodiranog objekta · 64 B</div><div class="code-block">${esc(d.previewHex)}</div><div class="detail-label">Sigurnost</div>${notice(d.note||'Nepoznato polje ostaje netaknuto.','blue')}<div style="margin-top:14px"><a class="button" href="/api/original?id=${state.activeFileId}">↓ Preuzmi originalni PCG</a></div>`:'<div class="empty">Izaberi PCG resurs.</div>'}</div></div>`;
    }
    return top+nav+`<div class="notice blue">Lista sadrži ${fmt(state.soundTotal||1003)} dokaznih veza naziva i adrese; sam naziv ne dokazuje da je korisnički Sound prisutan u konkretnom SET-u.</div><div class="section-heading"><div><h2>Adresar zvukova</h2><p>Program Change je prikazan 0–127, kao u izvornom dokaznom katalogu.</p></div><span class="badge blue">CC00 . CC32 . PC</span></div><div class="sound-grid">${state.sounds.map(s=>`<div class="sound-card"><span class="sound-address">${s.msb}.${s.lsb}.${s.pc}</span><strong title="${esc(s.name)}">${esc(s.name)}</strong><small>FACTORY SOUND · PA800</small></div>`).join('')||'<div class="empty">Nema podudaranja.</div>'}</div>${state.sounds.length<state.soundTotal?`<div class="right"><button class="button" data-action="sound-more">Prikaži još 80 →</button></div>`:''}`;
  }
  function renderSetup(){
    const tools=`<select class="select" data-change="setup-file" aria-label="Izaberi Global ili Program mapu">${(state.workspace.files||[]).filter(f=>['Global','Mape'].includes(f.kind)&&!f.error).map(f=>`<option value="${f.id}" ${f.id===state.activeFileId?'selected':''}>${esc(f.path)}</option>`).join('')}</select><label class="search"><span>⌕</span><input data-search="resources" type="search" placeholder="Pretraži…" value="${esc(state.search)}"></label>`;
    const top=pageTitle('GLOBAL / PRGMAPS','Setup & mape','Inspekcija globalnih resursa i Sound map tabela. Bez upisa neimenovanih parametara.',tools);
    if (!['Global','Mape'].includes(state.file?.file?.kind))return top+emptyModule('GBL / TBL');
    const d=state.resource; const list=state.file.resources||[];
    return top+`<div class="notice blue" style="margin-bottom:16px">${esc(state.file.file.name)} · ${fmt(list.length)} indeksiranih objekata. Sirovi parametri nemaju automatski dodijeljene korisničke nazive.</div><div class="explorer">${resourceList(list)}<div class="panel detail-pane">${d?`<div class="detail-crumb">${esc(d.family)} / OBJEKT #${d.index+1}</div><div class="detail-head"><div><h2>${esc(d.name)}</h2><p>${esc(d.codec)} · ${bytes(d.decodedBytes)}</p></div><span class="badge blue">READ ONLY</span></div><div class="summary-line">${tiny('Tag',d.tag)}${tiny('Sirovo',bytes(d.bytes))}${tiny('Dekodirano',bytes(d.decodedBytes))}</div><div class="detail-label">Početnih 64 dekodiranih bajtova</div><div class="code-block">${esc(d.previewHex)}</div><div class="detail-label">Granica dokaza</div>${notice(d.note||'Nepoznati listovi se ne imenuju i ne pišu.','blue')}<div style="margin-top:14px"><a href="/api/original?id=${state.activeFileId}" class="button">↓ Preuzmi original</a></div>`:'<div class="empty">Izaberi objekat.</div>'}</div></div>`;
  }
  function renderValidation(){
    const a=state.audit;
    const top=pageTitle('EVIDENCE / INTEGRITET','Validacija','Jasno odvojeno: softverska provjera ≠ uspješan test na fizičkom PA800.',`<button class="button button-green" data-action="audit">◇ ${a?'Ponovi provjeru':'Pokreni provjeru'}</button><a class="button" href="/api/report">↓ JSON izvještaj</a>`);
    return top+`<div class="validation-grid"><div class="validate-card"><div class="top"><b>KBEG/KEND indeks</b><span class="badge">SOFTVERSKI</span></div><p>Pročitan root, type-05 katalog i apsolutne FE adrese. ${fmt(state.workspace.counts.validFiles)} / ${fmt(state.workspace.counts.files)} otvorenih banki.</p></div><div class="validate-card"><div class="top"><b>OC31 / OC32 XOR</b><span class="badge ${a?'':'gray'}">${a?fmt((a.codecs.OC31||0)+(a.codecs.OC32||0))+' PASS':'NIJE POKRENUTO'}</span></div><p>${a?'Potvrđen dekodirani sadržaj i XOR svih obrađenih omotača.':'Pokreni dubinsku provjeru svih učitanih objekata.'}</p></div><div class="validate-card"><div class="top"><b>STY / PAD / PRF struktura</b><span class="badge ${a?.failures?.length?'red':'blue'}">${a?a.failures.length+' grešaka':'DEEP CHECK'}</span></div><p>PatternInfo, CV reference, TrackInfo[8] i PRF Release-2/3 i Drawbar geometrija.</p></div><div class="validate-card"><div class="top"><b>Fizički PA800 test</b><span class="badge gold">ČEKA TEST</span></div><p>Za produkcijski upis potrebno: Save → Load → Select → Play → Save → Reload na tvojoj klavijaturi. Trenutni build ne pravi novi native fajl.</p></div></div>
    ${a?`<div class="section-heading"><div><h2>Rezultat provjere</h2><p>${esc(a.timestamp)} · ${esc(a.scope)}</p></div><span class="badge ${a.status==='PASS'?'':'red'}">${esc(a.status)}</span></div><div class="validation-results"><div><strong>${fmt(a.checkedResources)}</strong><small>objekata prošlo</small></div><div><strong>${fmt(a.codecs.OC31||0)}</strong><small>OC31</small></div><div><strong>${fmt(a.codecs.OC32||0)}</strong><small>OC32</small></div><div><strong>${fmt(a.codecs.RAW||0)}</strong><small>RAW</small></div><div><strong>${fmt(a.failures.length)}</strong><small>grešaka</small></div></div>${(a.failures||[]).slice(0,10).map(f=>notice(`${f.file} · ${f.resource||''} — ${f.reason}`,'red')).join('')}${(a.warnings||[]).length?`<div class="notice">${fmt(a.warnings.length)} upozorenja za neuobičajene reference. Izvorni bajtovi nisu promijenjeni.</div>`:''}`:''}
    <div class="section-heading"><div><h2>Sljedeće za kompletan Manager</h2><p>Implementacija po redoslijedu autoriteta, bez lažne oznake „100% završeno”.</p></div></div><div class="panel"><div class="checkpoint"><div class="checkpoint-row"><span class="check-circle">✓</span><div><b>Čitači i katalog</b><small>Factory korpus testiran: 435 Style, 411 PAD, 264 Performance resursa bez greške u parsiranju.</small></div></div><div class="checkpoint-row"><span class="check-circle pending">2</span><div><b>Native writer + re-open / semantic diff</b><small>Potrebno kompletno dokazati OC20 izlazni okvir i ponovo validirati sve izmijenjene banke.</small></div></div><div class="checkpoint-row"><span class="check-circle pending">3</span><div><b>SET / Sound dependency editor</b><small>Uređivanje s kontrolisanim referencama, STS preserve slojem, undo i backupom.</small></div></div><div class="checkpoint-row"><span class="check-circle pending">4</span><div><b>Test na fizičkom instrumentu</b><small>Pošalji vlastiti testni SET i povratnu informaciju o Load/Play/Save/Reload postupku.</small></div></div></div></div>`;
  }
  async function importBrowserFile(file){
    if(!file)return;
    if(file.size>64*1024*1024){toast('Fajl je veći od 64 MiB.',true);return;}
    try{
      setBusy(true,`Učitavam ${file.name}…`);
      const b=await file.arrayBuffer();
      state.workspace=await request(`/api/open?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:b});
      state.fileCache.clear();state.file=null;state.resource=null;state.audit=null;state.activeFileId=null;
      state.view='overview';render();toast(`${file.name} učitan. Originalni bajtovi nisu izmijenjeni.`);
    }catch(e){toast(e.message,true);render();}
    finally{setBusy(false);el('file-input').value='';}
  }
  document.addEventListener('click',async ev=>{
    const view=ev.target.closest('[data-view]');
    if(view){await activateView(view.dataset.view);return;}
    const btn=ev.target.closest('[data-action]');if(!btn)return;
    const action=btn.dataset.action,id=btn.dataset.id;
    if(action==='upload'){el('file-input').click();return;}
    if(action==='demo'){
      if(state.workspace.source!=='demo' && !confirm('Zamijeniti trenutno učitani radni prostor demonstracijskim podacima?'))return;
      try{state.workspace=await request('/api/demo');state.fileCache.clear();state.file=null;state.resource=null;state.audit=null;state.activeFileId=null;state.view='overview';render();toast('Factory demo je ponovo učitan.');}catch(e){toast(e.message,true);}return;
    }
    if(action==='select-file'){
      const f=state.workspace.files.find(x=>x.id===Number(id));
      state.view=({Style:'styles',Performance:'performances',PAD:'pads',Sound:'sounds',Global:'setup',Mape:'setup'})[f.kind]||'overview';
      if(state.view==='sounds')state.soundTab='pcg';state.search=''; await loadFile(Number(id));return;
    }
    if(action==='resource'){await loadResource(Number(id));return;}
    if(action==='element'){state.element=Number(id);render();return;}
    if(action==='sound-tab'){
      state.soundTab=id;state.search='';
      if(id==='pcg'){const fileId=findFirstFile('Sound');if(fileId!=null){await loadFile(fileId);return;}}
      if(id==='catalog'&&!state.sounds.length){await loadSounds();return;}
      render();return;
    }
    if(action==='sound-more'){state.soundStart=state.sounds.length;await loadSounds();return;}
    if(action==='audit'){
      try{setBusy(true,'Provjeravam sve učitane resurse i kontrolne sume…');state.audit=await request('/api/audit');state.view='validation';render();toast(`${fmt(state.audit.checkedResources)} objekata provjereno. ${fmt(state.audit.failures.length)} grešaka.`);}catch(e){toast(e.message,true);render();}finally{setBusy(false);}return;
    }
  });
  document.addEventListener('change',async ev=>{
    if(ev.target.id==='file-input'){await importBrowserFile(ev.target.files?.[0]);return;}
    if(ev.target.dataset.change==='file'||ev.target.dataset.change==='setup-file'){state.search='';await loadFile(Number(ev.target.value));}
  });
  let searchTimer;
  document.addEventListener('input',ev=>{
    const kind=ev.target.dataset.search;if(!kind)return;
    const value=ev.target.value;
    if(kind==='resources')state.search=value;else{state.soundSearch=value;state.soundStart=0;}
    clearTimeout(searchTimer);
    searchTimer=setTimeout(async()=>{
      if(kind==='sounds')await loadSounds();else render();
      const inp=document.querySelector(`[data-search="${kind}"]`);
      if(inp){inp.focus();inp.setSelectionRange(value.length,value.length);}
    },kind==='sounds'?220:120);
  });
  document.addEventListener('keydown',e=>{
    if((e.key==='Enter'||e.key===' ')&&e.target.classList.contains('stat-card')){
      e.preventDefault();activateView(e.target.dataset.view);
    }
  });
  let dragCount=0;
  document.addEventListener('dragenter',ev=>{if(!ev.dataTransfer?.types?.includes('Files'))return;ev.preventDefault();dragCount++;el('drop-screen').classList.add('active');});
  document.addEventListener('dragleave',ev=>{if(!ev.dataTransfer?.types?.includes('Files'))return;ev.preventDefault();dragCount=Math.max(0,dragCount-1);if(!dragCount)el('drop-screen').classList.remove('active');});
  document.addEventListener('dragover',ev=>{if(ev.dataTransfer?.types?.includes('Files'))ev.preventDefault();});
  document.addEventListener('drop',async ev=>{ev.preventDefault();dragCount=0;el('drop-screen').classList.remove('active');const files=ev.dataTransfer?.files;if(files?.length>1)toast('Za više fajlova prvo ih spakuj u ZIP.',true);else await importBrowserFile(files?.[0]);});
  (async()=>{
    try{state.workspace=await request('/api/status');render();await loadSounds();}
    catch(e){el('view').innerHTML='<div class="empty"><strong>Server nije spreman</strong>Pokreni server.py i osvježi stranicu.</div>';toast(e.message,true);}
  })();
})();
