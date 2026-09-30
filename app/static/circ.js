/* Circulation desk. USB scanner guns behave as a keyboard: they type the code and press Enter. */
(function () {
  const I = window.I18N;
  const $ = (id) => document.getElementById(id);
  let patron = null, mode = 'out';

  function beep(ok) {
    try {
      const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain();
      o.frequency.value = ok ? 1100 : 220; o.type = ok ? 'sine' : 'square'; g.gain.value = 0.08;
      o.connect(g); g.connect(a.destination); o.start(); o.stop(a.currentTime + (ok ? 0.09 : 0.35));
    } catch (e) {}
  }
  function el(tag, text, cls) { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; }
  function log(ok, text) {
    const d = el('div', text, 'flash ' + (ok ? 'ok' : 'err'));
    const box = $('log'); box.prepend(d); while (box.children.length > 8) box.lastChild.remove();
    beep(ok);
  }
  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin', body: JSON.stringify(body) });
    if (r.status === 401 || r.redirected) { location.reload(); return { ok: false, message: 'Session expired' }; }
    return r.json();
  }
  function focusActive() {
    const id = mode === 'in' ? 'inScan' : (patron ? 'copyScan' : 'patronScan');
    const i = $(id); if (i) { i.focus(); i.select(); }
  }
  function renderPatron() {
    const box = $('patronBox'); box.replaceChildren();
    $('copyRow').hidden = !patron;
    $('patronRow').hidden = !!patron;
    if (!patron) { focusActive(); return; }
    const head = el('div', null, 'between');
    const h = el('div'); h.append(el('h3', patron.name), el('small', [patron.student_no, patron.klass].filter(Boolean).join(' · ')));
    const nb = el('button', I.next_patron, 'btn ghost sm'); nb.onclick = () => { patron = null; renderPatron(); };
    head.append(h, nb); box.append(head);
    if (patron.fines > 0) box.append(el('div', I.unpaid_fines + ': ' + patron.fines, 'flash err'));
    (patron.ready_holds || []).forEach((h) => box.append(el('div', I.ready_holds + ': ' + h.title + (h.where ? ' — ' + h.where : '') + (h.barcode ? ' [' + h.barcode + ']' : ''), 'flash ok')));
    box.append(el('strong', I.open_loans + ' (' + patron.loans.length + ')'));
    const wrap = el('div', null, 'tablewrap'), tb = el('table');
    patron.loans.forEach((l) => {
      const tr = el('tr'), td1 = el('td', l.title), td2 = el('td', I.due + ' ' + l.due + (l.overdue ? ' — ' + I.overdue : ''));
      if (l.overdue) td2.style.color = '#b3261e';
      const td3 = el('td'), b = el('button', I.renew, 'btn sm ghost');
      b.onclick = async () => { const r = await post('/api/circ/renew', { loan_id: l.id }); log(r.ok, r.message); if (r.ok) { patron = r.patron; renderPatron(); } };
      td3.append(b); tr.append(td1, td2, td3); tb.append(tr);
    });
    wrap.append(tb); box.append(wrap); focusActive();
  }

  async function onPatron(code) {
    const r = await post('/api/circ/patron', { code });
    if (!r.ok) return log(false, r.message);
    patron = r.patron; beep(true); renderPatron();
  }
  async function onCopyOut(code) {
    const r = await post('/api/circ/checkout', { patron_id: patron.id, code });
    log(r.ok, r.message); if (r.ok) { patron = r.patron; renderPatron(); }
  }
  async function onCheckin(code) {
    const r = await post('/api/circ/checkin', { code }); log(r.ok, r.message);
  }

  const handlers = { patronScan: onPatron, copyScan: onCopyOut, inScan: onCheckin };
  Object.keys(handlers).forEach((id) => {
    $(id).addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      const v = e.target.value.trim(); e.target.value = '';
      if (v) await handlers[id](v);
      focusActive();
    });
  });
  document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => {
    mode = b.dataset.mode;
    document.querySelectorAll('[data-mode]').forEach((x) => x.classList.toggle('ghost', x !== b));
    $('outPane').hidden = mode !== 'out'; $('inPane').hidden = mode !== 'in'; focusActive();
  }));
  // keep the cursor in the scan field so the gun always types into it
  document.addEventListener('click', (e) => { if (!e.target.closest('input,select,button,a,textarea')) focusActive(); });

  // optional camera scanning where the browser supports BarcodeDetector (Chrome / Android)
  const camBtn = $('camBtn');
  if (camBtn && 'BarcodeDetector' in window) {
    camBtn.hidden = false;
    camBtn.addEventListener('click', async () => {
      const video = $('cam'), det = new BarcodeDetector({ formats: ['code_39', 'code_128', 'ean_13'] });
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      video.srcObject = stream; video.hidden = false; await video.play();
      let last = '', t = 0;
      const tick = async () => {
        if (video.hidden) return;
        try {
          const codes = await det.detect(video);
          if (codes.length && (codes[0].rawValue !== last || Date.now() - t > 2500)) {
            last = codes[0].rawValue; t = Date.now();
            const id = mode === 'in' ? 'inScan' : (patron ? 'copyScan' : 'patronScan');
            await handlers[id](last);
          }
        } catch (e) {}
        requestAnimationFrame(tick);
      };
      tick();
      $('camStop').onclick = () => { video.hidden = true; stream.getTracks().forEach((s) => s.stop()); };
    });
  }
  renderPatron();
})();
