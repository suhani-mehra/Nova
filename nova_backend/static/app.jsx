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

function ProfileMenu({account, onSwitch}){
  const [open,setOpen]=useState(false);
  const ref=useRef(null);
  const a = NOVA.accounts[account] || NOVA.accounts.current || {};
  const kind = account;

  useEffect(()=>{
    const onDoc=e=>{ if(ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown',onDoc); return ()=>document.removeEventListener('mousedown',onDoc);
  },[]);

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
      h('button',{className:'menu-item'}, h('span',{className:'ic'},Icons.settings({size:17})),'Account settings'),
      h('button',{className:'menu-item'}, h('span',{className:'ic'},Icons.logout({size:17})),'Sign out'),
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
    {id:'progress', label:'My Progress'},
    {id:'team',     label:'My Team'},
  ],
  manager: [
    {id:'overview', label:'Overview'},
    {id:'teams',    label:'Teams'},
    {id:'people',   label:'People'},
  ],
};

function TopKPI({account}){
  if(account==='employee') return null;
  return h('div',{className:'kpi-pill'},
    h('div',{className:'ic',style:{background:'rgba(42,204,255,.12)',color:'#0f8fc4'}}, Icons.users({size:19})),
    h('div',null, h('div',{className:'lab'},'Active Learners This Week'), h('div',{className:'val'},(window.NOVA?.manager?.kpis?.[1]?.num)||'—')));
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": ["#A634FF","#FF4398"],
  "glow": "subtle",
  "corners": "rounded"
}/*EDITMODE-END*/;

function App(){
  const initKind = (NOVA.accounts.current && NOVA.accounts.current.kind) || 'employee';
  const [account,setAccount]=useState(initKind);
  const [tab,setTab]=useState((TABS[initKind]||TABS.employee)[0].id);
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
    setTab((TABS[role]||TABS.employee)[0].id);
  };

  const render=()=>{
    if(account==='employee'){
      return tab==='progress' ? h(MyProgress) : h(MyTeam);
    }
    return tab==='overview' ? h(MgrOverview) : tab==='teams' ? h(MgrTeams) : h(MgrPeople);
  };

  const tabs = TABS[account] || TABS.employee;

  return h(React.Fragment,null,
    h('header',{className:'topbar'},
      h('div',{className:'brand'},
        h(LogoMark),
        h('div',null,
          h('div',{className:'word'},'Nova'),
          h('div',{className:'sub'},'by Orion'))),
      h('div',{className:'tabs-wrap'},
        h('nav',{className:'tabs'},
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
    )
  );
}

(async function() {
  if (window.__novaDataReady) {
    await Promise.race([
      window.__novaDataReady,
      new Promise(function(resolve) { setTimeout(resolve, 10000); }),
    ]);
  }
  ReactDOM.createRoot(document.getElementById('root')).render(h(App));
  document.getElementById('nova-loading').style.display = 'none';
})();
