/* ===================== Nova — app shell ===================== */
const { useState, useEffect, useRef } = React;

function LogoMark(){
  return h('svg',{className:'mark', viewBox:'0 0 48 48', fill:'none'},
    h('defs',null,
      h('linearGradient',{id:'lg',x1:'0',y1:'0',x2:'1',y2:'1'},
        h('stop',{offset:'0%',stopColor:'#2ACCFF'}),
        h('stop',{offset:'52%',stopColor:'#A634FF'}),
        h('stop',{offset:'100%',stopColor:'#FF4398'}))),
    h('polygon',{points:'24,3 40,12.5 40,35.5 24,45 8,35.5 8,12.5', fill:'none', stroke:'url(#lg)', strokeWidth:3, strokeLinejoin:'round'}),
    h('path',{d:'M24 13 L27 21 L35 24 L27 27 L24 35 L21 27 L13 24 L21 21 Z', fill:'url(#lg)'})
  );
}

function OILogo(){
  // Two files, swapped by theme via CSS (.oi-light shows in light mode, .oi-dark in dark).
  return h(React.Fragment,null,
    h('img',{className:'oi-mark oi-light', src:'/logo-light.svg', alt:'OI Polaris'}),
    h('img',{className:'oi-mark oi-dark',  src:'/logo-dark.svg',  alt:'OI Polaris'})
  );
}

function ProfileMenu({account, onSwitch}){
  const [open,setOpen]=useState(false);
  const [theme,setTheme]=useState((document.documentElement.dataset.theme==='dark')?'dark':'light');
  const ref=useRef(null);
  const a = NOVA.accounts[account] || NOVA.accounts.current || {};
  const kind = account;

  useEffect(()=>{
    const onDoc=e=>{ if(ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown',onDoc); return ()=>document.removeEventListener('mousedown',onDoc);
  },[]);

  const toggleTheme=()=>{
    const next = theme==='dark' ? 'light' : 'dark';
    setTheme(next);
    if(typeof applyTheme==='function') applyTheme(next);            // stamp <html> + cache
    if(NOVA.accounts) {                                             // keep account objects in sync
      NOVA.accounts.colorMode=next;
      ['employee','manager','current'].forEach(k=>{ if(NOVA.accounts[k]) NOVA.accounts[k].colorMode=next; });
    }
    if(typeof saveColorMode==='function') saveColorMode(next);      // persist to backend
  };

  // Only show switch-account option when there's a distinct other account
  const otherKind = kind === 'employee' ? 'manager' : kind === 'manager' ? 'employee' : null;
  const otherA = otherKind ? NOVA.accounts[otherKind] : null;

  const badgeLabel = kind === 'manager' ? 'MANAGER ACCOUNT' : 'EMPLOYEE ACCOUNT';
  const badgeCls   = kind === 'manager' ? 'mgr' : 'emp';

  return h('div',{className:'profile', ref},
    h('button',{className:'profile-btn'+(open?' open':''), onClick:()=>setOpen(o=>!o)},
      h(Avatar,{name:a.name||'?', grad:a.av||['#A634FF','#FF4398'], size:'s'}),
      h('div',{className:'meta'}, h('div',{className:'nm'},a.name)),
      h('span',{className:'chev'}, Icons.chevron({size:16}))),
    open && h('div',{className:'menu'},
      h('div',{className:'who'},
        h(Avatar,{name:a.name||'?', grad:a.av||['#A634FF','#FF4398'], size:'m'}),
        h('div',{className:'meta'},
          h('div',{className:'nm'},a.name),
          h('div',{className:'em'},a.email),
          h('span',{className:`role-chip ${badgeCls}`,style:{marginTop:6}}, badgeLabel))),
      h('div',{className:'sep'}),
      h('button',{className:'menu-item', onClick:toggleTheme},
        h('span',{className:'ic'}, (theme==='dark'?Icons.sun:Icons.moon)({size:17})),
        theme==='dark' ? 'Switch to light mode' : 'Switch to dark mode'),
      h('button',{className:'menu-item', onClick:()=>typeof novaSignOut === 'function' && novaSignOut()}, h('span',{className:'ic'},Icons.logout({size:17})),'Sign out'),
      otherA && h(React.Fragment,null,
        h('div',{className:'sep'}),
        h('div',{className:'demo-note'},'Switch view'),
        h('button',{className:'menu-item', onClick:()=>{onSwitch(otherKind); setOpen(false);}},
          h('span',{className:'ic'},Icons.switch({size:17})),
          otherKind === 'manager' ? 'Switch to Manager view' : 'Switch to Employee view')
      )
    )
  );
}

const TABS = {
  employee: [
    {id:'employee', label:'My Learning'},
  ],
  manager: [
    {id:'overview',  label:'Overview'},
    {id:'yourteam',  label:'Your Team'},
  ],
};

// Overview is exec-manager only; everyone else sees just "Your Team".
function managerTabList() {
  const isExec = !!(window.NOVA && window.NOVA.accounts && window.NOVA.accounts.isExecManager);
  return TABS.manager.filter(t => t.id !== 'overview' || isExec);
}

function tabsForAccount(account) {
  return account === 'manager' ? managerTabList() : (TABS[account] || TABS.employee);
}

function ExecDevPanel({ myId }) {
  const myIdNum = myId ? parseInt(myId, 10) : null;

  const currentTarget = sessionStorage.getItem('nova_impersonate_id');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef(null);
  const panelRef = useRef(null);

  useEffect(() => {
    const handler = e => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const search = (q) => {
    setQuery(q);
    clearTimeout(debounceRef.current);
    if (!q.trim()) { setResults([]); setOpen(false); return; }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      const data = await apiGet('/api/manager/people/search?scope=impersonate&q=' + encodeURIComponent(q));
      setLoading(false);
      if (data && data.employees) {
        setResults(data.employees.slice(0, 8));
        setOpen(true);
      }
    }, 250);
  };

  const switchTo = (emp) => {
    sessionStorage.setItem('nova_impersonate_id', String(emp.user_id));
    setOpen(false);
    setQuery('');
    window.location.reload();
  };

  const clear = () => {
    sessionStorage.removeItem('nova_impersonate_id');
    window.location.reload();
  };

  if (!NOVA.DEV_USER_IDS.has(myIdNum)) return null;

  return h('div', { className: 'exec-dev-panel' + (currentTarget ? ' active' : ''), ref: panelRef },
    h('span', { className: 'exec-dev-label' }, '⚡'),
    currentTarget && h('span', { className: 'exec-dev-viewing' }, `User ${currentTarget}`),
    h('div', { className: 'exec-dev-search' },
      h('input', {
        type: 'text',
        placeholder: currentTarget ? 'Switch to…' : 'Search name…',
        value: query,
        onChange: e => search(e.target.value),
        onFocus: () => results.length && setOpen(true),
      }),
      loading && h('span', { className: 'exec-dev-spinner' }, '…'),
      open && results.length > 0 && h('div', { className: 'exec-dev-dropdown' },
        results.map(emp =>
          h('div', {
            key: emp.user_id,
            className: 'exec-dev-result',
            onMouseDown: () => switchTo(emp),
          },
            h('span', { className: 'exec-dev-result-name' }, emp.name),
            h('span', { className: 'exec-dev-result-meta' }, emp.department || emp.designation || `#${emp.user_id}`)
          )
        )
      )
    ),
    currentTarget && h('button', { className: 'exec-dev-clear', onClick: clear }, '✕')
  );
}

function TopKPI({account}){
  if(account==='employee'){
    const E = window.NOVA && window.NOVA.employee;
    if(!E) return null;
    return h('div',{className:'streak-topbar'},
      h('div',{className:'streak-topbar-flame'},'🔥'),
      h('div',{className:'streak-topbar-num'},E.streak),
      h('div',{className:'streak-topbar-lab'},'day streak')
    );
  }
  // Manager topbar carries no KPI pill — Active learners moved into the
  // Overview page hero card.
  return null;
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": ["#A634FF","#FF4398"],
  "glow": "subtle",
  "corners": "rounded"
}/*EDITMODE-END*/;

function App(){
  const initKind = (NOVA.accounts.current && NOVA.accounts.current.kind) || 'employee';
  const [account,setAccount]=useState(initKind);
  const [tab,setTab]=useState(tabsForAccount(initKind)[0].id);
  const [t,setTweak]=useTweaks(TWEAK_DEFAULTS);
  const [,forceUpdate]=useState(0);

  // Re-render when slow manager data arrives after the initial render.
  useEffect(()=>{
    const onMgrReady=()=>forceUpdate(n=>n+1);
    window.addEventListener('nova-manager-ready',onMgrReady);
    return ()=>window.removeEventListener('nova-manager-ready',onMgrReady);
  },[]);

  useEffect(()=>{
    const r=document.documentElement.style;
    r.setProperty('--grad-pill', `linear-gradient(135deg,${t.accent[0]} 0%,${t.accent[1]} 100%)`);
    r.setProperty('--purple', t.accent[0]);
    r.setProperty('--fuchsia', t.accent[1]);
    const rad = t.corners==='sharp'?'8px':t.corners==='soft'?'14px':'22px';
    r.setProperty('--radius', rad);
    r.setProperty('--radius-sm', t.corners==='sharp'?'7px':t.corners==='soft'?'10px':'14px');
    document.body.classList.toggle('glow-off', t.glow==='off');
    document.body.classList.toggle('glow-vivid', t.glow==='vivid');
  },[t.accent,t.glow,t.corners]);

  const switchAccount=(role)=>{
    if(NOVA.accounts[role]) NOVA.accounts.current = NOVA.accounts[role];
    setAccount(role);
    setTab(tabsForAccount(role)[0].id);
  };

  const render=()=>{
    if (tab === 'employee') return h(MyEmployee);
    if (tab === 'overview') return h(MgrOverview);
    return h(MgrYourTeam);
  };

  const tabs = tabsForAccount(account);

  return h(React.Fragment,null,
    h('header',{className:'topbar'},
      h('div',{className:'brand'},
        h(OILogo)),
      h('div',{className:'tabs-wrap'},
        tabs.length > 1 && h('nav',{className:'tabs'},
          tabs.map(t=>h('button',{key:t.id, className:'tab'+(tab===t.id?' active':''),
            onClick:()=>setTab(t.id)}, t.label)))),
      h('div',{className:'right'},
        h(TopKPI,{account}),
        h(ProfileMenu,{account, onSwitch:switchAccount}))
    ),
    h('main',{key:account+tab}, render()),
    h(TweaksPanel,null,
      h(TweakSection,{label:'Accent'}),
      h(TweakColor,{label:'Gradient', value:t.accent,
        options:[['#A634FF','#FF4398'],['#2ACCFF','#5400DC'],['#FF4398','#A634FF'],['#5400DC','#FF6B88']],
        onChange:v=>setTweak('accent',v)}),
      h(TweakSection,{label:'Surface'}),
      h(TweakRadio,{label:'Ambient glow', value:t.glow, options:['off','subtle','vivid'],
        onChange:v=>setTweak('glow',v)}),
      h(TweakRadio,{label:'Corners', value:t.corners, options:['sharp','soft','rounded'],
        onChange:v=>setTweak('corners',v)})
    ),
    h(ExecDevPanel, { myId: sessionStorage.getItem('nova_dev_uid') || null })
  );
}

function LoadingScreen() {
  return h('div', { className: 'nova-fullscreen-state' },
    h('div', { className: 'nova-state-card' },
      h(LogoMark),
      h('div', { className: 'nova-state-title' }, 'Loading your data…'),
      h('div', { className: 'nova-state-sub' }, 'Connecting to Classmate'),
      h('div', { className: 'nova-spinner' })
    )
  );
}

function ErrorScreen() {
  const retry = () => window.location.reload();
  return h('div', { className: 'nova-fullscreen-state' },
    h('div', { className: 'nova-state-card' },
      h(LogoMark),
      h('div', { className: 'nova-state-title' }, 'Could not load data'),
      h('div', { className: 'nova-state-sub' }, 'Could not connect to the server. Try again in a moment.'),
      h('button', { className: 'nova-state-retry', onClick: retry }, 'Retry')
    )
  );
}

function SignInPageGate() {
  return h(SignInPage, {
    onSignIn: async () => {
      await novaSignIn();
      window.location.reload();
    },
    onDevSignIn: (uid) => {
      sessionStorage.setItem('nova_dev_uid', String(uid));
      window.location.reload();
    },
  });
}

// True only when every NOVA data field required for the signed-in role is populated.
function novaDataComplete() {
  const acc = window.NOVA && window.NOVA.accounts;
  if (!acc || !acc.current) return false;
  const role = acc.role;
  if (role === 'manager') return !!window.NOVA.manager;
  if (role === 'both')    return !!(window.NOVA.employee && window.NOVA.team && window.NOVA.manager);
  // employee (default)
  return !!(window.NOVA.employee && window.NOVA.team);
}

function AppRoot() {
  const [phase, setPhase] = useState('loading'); // 'loading' | 'ready' | 'error' | 'signin'

  useEffect(() => {
    const msalAccount = (typeof msal !== 'undefined' && typeof novaGetAccount === 'function')
      ? novaGetAccount() : null;
    const devUid = sessionStorage.getItem('nova_dev_uid');

    if (!msalAccount && !devUid) {
      setPhase('signin');
      return;
    }

    // Wait for the real data-ready signal — initNova() always resolves
    // __novaDataReady when it finishes (success or caught error). Whenever it
    // resolves, decide ready vs. error based on whether the data actually loaded.
    // The timeout is only a safety net for a genuinely hung request (e.g. the
    // backend never responds at all), so it's generous.
    let settled = false;
    window.__novaDataReady.then(() => {
      if (settled) return;
      settled = true;
      setPhase(novaDataComplete() ? 'ready' : 'error');
    });
    setTimeout(() => {
      if (settled) return;
      settled = true;
      setPhase(novaDataComplete() ? 'ready' : 'error');
    }, 120000); // 2 min hard cap
  }, []);

  if (phase === 'loading') return h(LoadingScreen);
  if (phase === 'signin') return h(SignInPageGate);
  if (phase === 'error') return h(ErrorScreen);
  return h(App);
}

document.getElementById('nova-loading').style.display = 'none';
ReactDOM.createRoot(document.getElementById('root')).render(h(AppRoot));
