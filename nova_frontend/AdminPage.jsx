/* ===================== Nova — Admin page =====================
 *
 * Admin-only console (shown via the profile dropdown for the configured
 * admins). Lets admins:
 *   • override Classmate manager allocations (assign someone a new manager)
 *   • grant / revoke exec-manager status (target must already be a manager)
 *   • reset all manager-allocation overrides back to Classmate data
 *
 * All access is enforced server-side by routers/admin.py (ADMIN_USER_IDS);
 * this page is only a convenience UI and never a security boundary.
 *
 * Uses the shared global `h` (icons.jsx) and app helpers apiGet/novaGetToken.
 * React hooks are aliased (…A) to avoid colliding with the other scripts that
 * share this global scope.
 */
const { useState: useStateA, useEffect: useEffectA, useRef: useRefA } = React;

// POST helper that surfaces the HTTP status + parsed body so the page can tell
// a 400 "not a manager yet" apart from other failures. Mirrors apiPost's auth
// headers but never swallows the response.
async function _adminRequest(method, path, body) {
  const token = (typeof novaGetToken === 'function') ? await novaGetToken() : null;
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  try {
    const res = await fetch((window.NOVA_API_BASE || '') + path, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* empty/non-JSON body */ }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: null };
  }
}

const _adminStyles = {
  page:    { padding: '28px 32px', maxWidth: 900, margin: '0 auto' },
  head:    { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 },
  title:   { fontSize: 26, fontWeight: 800, color: 'var(--ink)' },
  sub:     { color: 'var(--muted)', fontSize: 14, marginTop: 4 },
  card:    { background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 22, marginBottom: 20 },
  cardH:   { fontSize: 17, fontWeight: 700, color: 'var(--ink)', marginBottom: 4 },
  cardSub: { fontSize: 13, color: 'var(--muted)', marginBottom: 16 },
  row:     { display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' },
  fieldLbl:{ fontSize: 12, fontWeight: 600, color: 'var(--ink-soft)', marginBottom: 6, display: 'block' },
  input:   { width: '100%', padding: '9px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--line)', background: 'var(--surface-2)', color: 'var(--ink)', fontSize: 14, boxSizing: 'border-box' },
  pickWrap:{ position: 'relative', flex: '1 1 220px', minWidth: 200 },
  drop:    { position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 20, marginTop: 4, background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 'var(--radius-sm)', boxShadow: 'var(--shadow)', maxHeight: 240, overflowY: 'auto' },
  dropItem:{ padding: '9px 12px', cursor: 'pointer', fontSize: 14, color: 'var(--ink)', borderBottom: '1px solid var(--line-soft)' },
  dropMeta:{ fontSize: 12, color: 'var(--muted)' },
  btn:     { padding: '9px 16px', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--grad-pill)', color: 'var(--on-accent)', fontSize: 14, fontWeight: 600, cursor: 'pointer' },
  btnGhost:{ padding: '9px 16px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--line)', background: 'transparent', color: 'var(--ink)', fontSize: 14, fontWeight: 600, cursor: 'pointer' },
  btnDanger:{ padding: '9px 16px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--risk)', background: 'transparent', color: 'var(--risk)', fontSize: 14, fontWeight: 600, cursor: 'pointer' },
  table:   { width: '100%', borderCollapse: 'collapse', marginTop: 16, fontSize: 14 },
  th:      { textAlign: 'left', padding: '8px 10px', color: 'var(--muted)', fontWeight: 600, borderBottom: '1px solid var(--line)', fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' },
  td:      { padding: '9px 10px', color: 'var(--ink)', borderBottom: '1px solid var(--line-soft)' },
  empty:   { padding: '14px 10px', color: 'var(--muted)', fontSize: 13, fontStyle: 'italic' },
  msgOk:   { padding: '10px 14px', borderRadius: 'var(--radius-sm)', background: 'var(--ok-bg)', color: 'var(--ok)', fontSize: 13, marginBottom: 16 },
  msgErr:  { padding: '10px 14px', borderRadius: 'var(--radius-sm)', background: 'var(--risk-bg)', color: 'var(--risk)', fontSize: 13, marginBottom: 16 },
};

// Debounced company-wide people search box with a results dropdown.
// `value` is the currently selected {user_id, name} (or null); onPick(emp|null).
function AdminPeoplePicker({ label, placeholder, value, onPick }) {
  const [q, setQ] = useStateA('');
  const [results, setResults] = useStateA([]);
  const [open, setOpen] = useStateA(false);
  const timerRef = useRefA(null);
  const boxRef = useRefA(null);

  useEffectA(() => {
    const onDoc = e => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const search = (text) => {
    setQ(text);
    if (value) onPick(null);           // typing clears a prior selection
    clearTimeout(timerRef.current);
    if (!text.trim()) { setResults([]); setOpen(false); return; }
    timerRef.current = setTimeout(async () => {
      const data = await apiGet('/api/admin/people/search?q=' + encodeURIComponent(text.trim()));
      setResults((data && data.employees) ? data.employees : []);
      setOpen(true);
    }, 250);
  };

  const pick = (emp) => { onPick(emp); setQ(emp.name); setOpen(false); };

  return h('div', { style: _adminStyles.pickWrap, ref: boxRef },
    h('label', { style: _adminStyles.fieldLbl }, label),
    h('input', {
      style: _adminStyles.input,
      type: 'text',
      placeholder: placeholder || 'Search name…',
      value: value ? value.name : q,
      onChange: e => search(e.target.value),
      onFocus: () => results.length && setOpen(true),
    }),
    open && results.length > 0 && h('div', { style: _adminStyles.drop },
      results.map(emp =>
        h('div', {
          key: emp.user_id,
          style: _adminStyles.dropItem,
          onMouseDown: () => pick(emp),
        },
          h('div', null, emp.name),
          h('div', { style: _adminStyles.dropMeta }, emp.department || emp.designation || ('#' + emp.user_id))
        )
      )
    )
  );
}

function AdminPage({ onClose }) {
  const [overrides, setOverrides] = useStateA({ manager_overrides: [], exec_overrides: [] });
  const [emp, setEmp] = useStateA(null);         // employee to reassign
  const [mgr, setMgr] = useStateA(null);         // new manager
  const [execTarget, setExecTarget] = useStateA(null);
  const [msg, setMsg] = useStateA(null);         // {ok:bool, text}
  const [busy, setBusy] = useStateA(false);

  const refresh = async () => {
    const data = await apiGet('/api/admin/overrides');
    if (data) setOverrides({
      manager_overrides: data.manager_overrides || [],
      exec_overrides: data.exec_overrides || [],
    });
  };

  useEffectA(() => { refresh(); }, []);

  const flash = (ok, text) => { setMsg({ ok, text }); };

  const assignManager = async () => {
    if (!emp || !mgr) { flash(false, 'Pick both an employee and a new manager.'); return; }
    if (emp.user_id === mgr.user_id) { flash(false, 'An employee cannot be their own manager.'); return; }
    setBusy(true);
    const r = await _adminRequest('POST', '/api/admin/manager-override',
      { user_id: emp.user_id, manager_user_id: mgr.user_id });
    setBusy(false);
    if (r.ok) {
      flash(true, `${emp.name} now reports to ${mgr.name}.`);
      setEmp(null); setMgr(null);
      refresh();
    } else {
      flash(false, (r.data && r.data.detail) || 'Could not save the manager override.');
    }
  };

  const setExec = async (userId, name, isExec) => {
    setBusy(true);
    const r = await _adminRequest('POST', '/api/admin/exec-status',
      { user_id: userId, is_exec: isExec });
    setBusy(false);
    if (r.ok) {
      flash(true, isExec ? `${name} granted exec status.` : `${name} exec status revoked.`);
      if (isExec) setExecTarget(null);
      refresh();
    } else if (r.status === 400) {
      // Server backstop for "not a manager yet".
      flash(false, (r.data && r.data.detail) ||
        'This person is not a manager yet — assign them as a manager first (Manager Allocations above).');
    } else {
      flash(false, (r.data && r.data.detail) || 'Could not update exec status.');
    }
  };

  const grantExec = () => {
    if (!execTarget) { flash(false, 'Pick a person to grant exec status.'); return; }
    setExec(execTarget.user_id, execTarget.name, true);
  };

  const resetAll = async () => {
    if (!window.confirm('Reset ALL manager-allocation overrides back to Classmate data? This cannot be undone.')) return;
    setBusy(true);
    const r = await _adminRequest('POST', '/api/admin/reset-overrides', {});
    setBusy(false);
    if (r.ok) { flash(true, `Reset complete — ${r.data ? r.data.cleared : 0} override(s) cleared.`); refresh(); }
    else flash(false, 'Reset failed.');
  };

  const mgrRows = overrides.manager_overrides || [];
  const execRows = overrides.exec_overrides || [];

  return h('div', { style: _adminStyles.page },
    h('div', { style: _adminStyles.head },
      h('div', null,
        h('div', { style: _adminStyles.title }, 'Admin'),
        h('div', { style: _adminStyles.sub }, 'Override manager allocations and exec status for testing.')),
      h('button', { style: _adminStyles.btnGhost, onClick: onClose }, 'Close')
    ),

    msg && h('div', { style: msg.ok ? _adminStyles.msgOk : _adminStyles.msgErr }, msg.text),

    // ── Manager Allocations ───────────────────────────────────────────────
    h('div', { style: _adminStyles.card },
      h('div', { style: _adminStyles.cardH }, 'Manager Allocations'),
      h('div', { style: _adminStyles.cardSub },
        'Reassign an employee to a different manager. Overrides take precedence over Classmate data and survive re-syncs until reset.'),
      h('div', { style: _adminStyles.row },
        h(AdminPeoplePicker, { label: 'Employee', placeholder: 'Who to reassign…', value: emp, onPick: setEmp }),
        h(AdminPeoplePicker, { label: 'New manager', placeholder: 'Assign to…', value: mgr, onPick: setMgr }),
        h('button', { style: _adminStyles.btn, disabled: busy, onClick: assignManager }, 'Assign')
      ),
      h('table', { style: _adminStyles.table },
        h('thead', null, h('tr', null,
          h('th', { style: _adminStyles.th }, 'Employee'),
          h('th', { style: _adminStyles.th }, 'Assigned manager'))),
        h('tbody', null,
          mgrRows.length === 0
            ? h('tr', null, h('td', { style: _adminStyles.empty, colSpan: 2 }, 'No manager overrides.'))
            : mgrRows.map(r => h('tr', { key: r.user_id },
                h('td', { style: _adminStyles.td }, r.name),
                h('td', { style: _adminStyles.td }, r.manager_name)))
        )
      ),
      h('div', { style: { marginTop: 16 } },
        h('button', { style: _adminStyles.btnDanger, disabled: busy, onClick: resetAll },
          'Reset all to Classmate data'))
    ),

    // ── Exec Status ───────────────────────────────────────────────────────
    h('div', { style: _adminStyles.card },
      h('div', { style: _adminStyles.cardH }, 'Exec Status'),
      h('div', { style: _adminStyles.cardSub },
        'Grant or revoke exec-manager status (company-wide Overview). The person must already be a manager.'),
      h('div', { style: _adminStyles.row },
        h(AdminPeoplePicker, { label: 'Manager', placeholder: 'Search a manager…', value: execTarget, onPick: setExecTarget }),
        h('button', { style: _adminStyles.btn, disabled: busy, onClick: grantExec }, 'Grant exec')
      ),
      h('table', { style: _adminStyles.table },
        h('thead', null, h('tr', null,
          h('th', { style: _adminStyles.th }, 'Name'),
          h('th', { style: _adminStyles.th }, 'Exec'),
          h('th', { style: _adminStyles.th }, ''))),
        h('tbody', null,
          execRows.length === 0
            ? h('tr', null, h('td', { style: _adminStyles.empty, colSpan: 3 }, 'No exec-status overrides.'))
            : execRows.map(r => h('tr', { key: r.user_id },
                h('td', { style: _adminStyles.td }, r.name),
                h('td', { style: _adminStyles.td }, r.is_exec ? 'Yes' : 'No'),
                h('td', { style: _adminStyles.td },
                  h('button', {
                    style: _adminStyles.btnGhost,
                    disabled: busy,
                    onClick: () => setExec(r.user_id, r.name, !r.is_exec),
                  }, r.is_exec ? 'Revoke' : 'Grant'))))
        )
      )
    )
  );
}

Object.assign(window, { AdminPage });
