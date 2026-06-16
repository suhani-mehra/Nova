/* ===================== Nova — manager views ===================== */
const { useState:useStateM } = React;

const tierByKey = k => NOVA.TIERS.find(t=>t.key===k);
const trendIco = d => d==='up'?Icons.up({size:14,sw:2.4}):d==='down'?Icons.down({size:14,sw:2.4}):Icons.flat({size:14,sw:2.4});

/* ---------------- OVERVIEW ---------------- */
function MgrOverview(){
  const M=NOVA.manager;
  const kpiIc = {spark:Icons.spark, users:Icons.users, shield:Icons.shield, alert:Icons.alert};
  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},'Learning Overview'),
      h('div',{className:'sub'},`Company-wide progress toward AI proficiency · ${M.total.toLocaleString()} employees`)),
    // KPI row
    h('div',{className:'kpi-grid', style:{marginBottom:22}},
      M.kpis.map((k,i)=>h('div',{className:'kpi-card',key:i},
        h('div',{className:'top'},
          h('div',{className:'ic',style:{background:k.tint,color:k.col}}, (kpiIc[k.ic]||Icons.spark)({size:22,color:k.col})),
          h('div',{className:`trend ${k.dir}`}, trendIco(k.dir), k.trend)),
        h('div',{className:'num'},k.num),
        h('div',{className:'lab',dangerouslySetInnerHTML:{__html:k.lab}})))
    ),
    // line chart
    h('div',{className:'card', style:{marginBottom:22}},
      h('div',{className:'card-head-row', style:{marginBottom:16}},
        h('div',null,
          h('div',{className:'card-title'},'Progress Toward the Goal'),
          h('div',{className:'card-sub'},'Share of all employees who are AI-proficient, vs learning retention.')),
        h('div',{className:'goal-banner'}, Icons.target({size:16,color:'#A634FF'}),
          h('span',null,'Goal: ', h('b',null,'every employee AI-proficient')))),
      h(LineChart,{months:M.months, proficiency:M.series.proficiency, retention:M.series.retention, target:M.target, total:M.total}),
      h('div',{className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
        h('div',{className:'it'}, h('span',{className:'sw',style:{background:'linear-gradient(90deg,#2ACCFF,#A634FF,#FF4398)'}}),'AI-proficient employees'),
        h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#2ACCFF'}}),'Retention rate'),
        h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#FF4398'}}),`Target ${M.target}%`))
    )
  );
}

/* ---------------- TEAMS ---------------- */
function MgrTeams(){
  const M=NOVA.manager;
  const sorted=[...M.teams].sort((a,b)=>b.prof-a.prof);
  const stLabel={ok:'On track', warn:'Needs focus', risk:'Falling behind'};
  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},'Team Progress'),
      h('div',{className:'sub'},'How much progress each team is making toward AI proficiency.')),
    h('div',{className:'card'},
      h('div',{className:'team-head'},
        h('div',null,'Team'), h('div',null,'Members'),
        h('div',null,'AI proficiency'), h('div',{style:{textAlign:'right'}},'Status')),
      sorted.map((t,i)=>h('div',{className:'team-row',key:i},
        h('div',null,
          h('div',{className:'tname'}, t.name),
          h('div',{className:'tmeta'}, `Trend ${t.trend} this quarter`)),
        h('div',{style:{fontWeight:700,color:'var(--ink-soft)'}}, t.members.toLocaleString()),
        h('div',{style:{display:'flex',alignItems:'center',gap:14}},
          h('div',{className:'bar-wide',style:{flex:1}}, h('i',{style:{width:t.prof+'%', background:`linear-gradient(90deg,${t.col}bb,${t.col})`}})),
          h('div',{className:'pct-strong',style:{width:46,textAlign:'right'}}, t.prof+'%')),
        h('div',{style:{textAlign:'right'}},
          h('span',{className:`status ${t.status}`}, h('span',{className:'dot'}), stLabel[t.status]))))
    )
  );
}

/* ---------------- PEOPLE ---------------- */
function MgrPeople(){
  const M=NOVA.manager;
  const [filter,setFilter]=useStateM('all');
  const stLabel={ok:'Thriving', warn:'On track', risk:'At risk'};
  const filtered = M.people.filter(p=>{
    if(filter==='all') return true;
    if(filter==='thriving') return p.status==='ok';
    if(filter==='risk') return p.status==='risk';
    return true;
  });
  const riskCount=M.people.filter(p=>p.status==='risk').length;
  return h('div',{className:'page'},
    h('div',{className:'page-head', style:{display:'flex',alignItems:'flex-end',justifyContent:'space-between',gap:16,flexWrap:'wrap'}},
      h('div',null,
        h('h1',{className:'greeting'},'Individual Progress'),
        h('div',{className:'sub'},'Who is moving forward — and who needs a nudge.')),
      h('div',{className:'seg'},
        h('button',{className:filter==='all'?'on':'', onClick:()=>setFilter('all')},'All'),
        h('button',{className:filter==='thriving'?'on':'', onClick:()=>setFilter('thriving')},'Thriving'),
        h('button',{className:filter==='risk'?'on':'', onClick:()=>setFilter('risk')},`At risk (${riskCount})`))),
    h('div',{className:'card'},
      h('div',{className:'ppl-head'},
        h('div',null,'Employee'), h('div',null,'Tier'),
        h('div',null,'AI proficiency'), h('div',null,'Trend'),
        h('div',{style:{textAlign:'right'}},'Status')),
      filtered.map((p,i)=>{
        const t=tierByKey(p.tier);
        return h('div',{className:'ppl-row',key:i},
          h('div',{className:'who'},
            h(Avatar,{name:p.name, grad:p.av, size:'s'}),
            h('div',{style:{minWidth:0}},
              h('div',{className:'nm'},p.name),
              h('div',{className:'rl'},`${p.role} · ${p.team}`))),
          h('div',null, h('span',{className:'tier-pill'},
            h(Hex,{color:t.color, glyph:tierHexInner(p.tier), active:false, size:24}),
            h('span',{style:{color:t.color}}, t.name))),
          h('div',{style:{display:'flex',alignItems:'center',gap:12}},
            h('div',{className:'bar-wide',style:{flex:1,maxWidth:160}}, h('i',{style:{width:p.prof+'%',
              background: p.status==='risk'?'linear-gradient(90deg,#E23D6E,#FF6B88)':'linear-gradient(90deg,#2ACCFF,#A634FF)'}})),
            h('div',{className:'pct-strong',style:{width:42}}, p.prof+'%')),
          h('div',null, h('span',{className:`trend-cell ${p.dir}`}, trendIco(p.dir), p.trend)),
          h('div',{style:{textAlign:'right'}},
            h('span',{className:`status ${p.status}`}, h('span',{className:'dot'}), stLabel[p.status])));
      })
    )
  );
}

Object.assign(window,{MgrOverview, MgrTeams, MgrPeople});
