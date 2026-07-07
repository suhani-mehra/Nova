/* ===================== Nova — manager views ===================== */
const { useState:useStateM } = React;

const tierByKey = k => NOVA.TIERS.find(t=>t.key===k);
const trendIco = d => d==='up'?Icons.up({size:14,sw:2.4}):d==='down'?Icons.down({size:14,sw:2.4}):Icons.flat({size:14,sw:2.4});

/* ---------------- OVERVIEW ---------------- */
function MgrOverview(){
  const M=NOVA.manager;
  const kpiIc = {spark:Icons.spark, users:Icons.users, shield:Icons.shield, alert:Icons.alert};
  const [chartTab,setChartTab]=useStateM('trend');   // 'trend' (line) | 'region' (bars)
  const region=M.proficiencyByRegion;

  const trendView = [
    h(LineChart,{key:'lc', months:M.months, proficiency:M.series.proficiency, active:M.series.active, target:M.target, total:M.total}),
    h('div',{key:'lg',className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
      h('div',{className:'it'}, h('span',{className:'sw',style:{background:'linear-gradient(90deg,#2ACCFF,#A634FF,#FF4398)'}}),'% AI-proficient employees'),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#2ACCFF'}}),'% active learners'),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#FF4398'}}),`Target ${M.target}%`)),
  ];

  const regionView = [
    h(RegionProficiencyChart,{key:'rc', data:region}),
    region && region.regions && h('div',{key:'rg',className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
      region.regions.map(r=>h('div',{className:'it',key:r.key},
        h('span',{style:{width:13,height:13,borderRadius:3,background:r.color,display:'inline-block'}}),
        `${r.label} · ${r.total.toLocaleString()}`)),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#3a3d57'}}),'Goal per level'),
      h('div',{className:'it'}, h('span',{style:{width:16,height:2,background:'#7a7d96',display:'inline-block'}}),'Company-wide actual')),
  ];

  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},'Learning Overview'),
      h('div',{className:'sub'},`Company-wide progress toward AI proficiency · ${M.total.toLocaleString()} employees`)),
    h('div',{className:'kpi-grid', style:{marginBottom:22}},
      M.kpis.map((k,i)=>h('div',{className:'kpi-card',key:i},
        h('div',{className:'top'},
          h('div',{className:'ic',style:{background:k.tint,color:k.col}}, (kpiIc[k.ic]||Icons.spark)({size:22,color:k.col})),
          h('div',{className:`trend ${k.dir}${k.badWhenUp?' invert':''}`}, trendIco(k.dir), k.trend)),
        h('div',{className:'num'},k.num),
        h('div',{className:'lab',dangerouslySetInnerHTML:{__html:k.lab}})))
    ),
    h('div',{className:'card', style:{marginBottom:22}},
      h('div',{className:'card-head-row', style:{marginBottom:16}},
        h('div',null,
          h('div',{className:'card-title'}, chartTab==='trend'?'AI Proficiency Trend':'AI Proficiency by Region'),
          h('div',{className:'card-sub'}, chartTab==='trend'
            ? '% of all employees with AI proficiency ≥ 30%, measured at each quarter end.'
            : "Each region's own % proficient at each level, vs. the company-wide actual and goal.")),
        h('div',{className:'seg'},
          h('button',{className:chartTab==='trend'?'on':'', onClick:()=>setChartTab('trend')},'Trend'),
          h('button',{className:chartTab==='region'?'on':'', onClick:()=>setChartTab('region')},'By region'))),
      chartTab==='trend' ? trendView : regionView
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
          h('div',{className:'tmeta'}, `Trend ${t.trend} vs last month`)),
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
  const [searchQ,setSearchQ]=useStateM('');
  const [searchResults,setSearchResults]=useStateM(null);
  const [searching,setSearching]=useStateM(false);
  const [searchScope,setSearchScope]=useStateM('');
  const debounceRef=React.useRef(null);

  const filtered = M.people.filter(p=>{
    if(filter==='all') return true;
    if(filter==='on_track') return p.status==='ok';
    if(filter==='risk') return p.status==='risk';
    return true;
  });
  const riskCount=M.people.filter(p=>p.status==='risk').length;

  React.useEffect(()=>{
    if(debounceRef.current) clearTimeout(debounceRef.current);
    if(!searchQ.trim()){
      setSearchResults(null);
      setSearchScope('');
      return;
    }
    debounceRef.current=setTimeout(async ()=>{
      setSearching(true);
      try{
        const res=await apiGet('/api/manager/people/search?q='+encodeURIComponent(searchQ.trim()));
        if(res){
          setSearchResults(res.employees||[]);
          setSearchScope(res.search_scope||'');
        }
      }catch(e){
        console.warn('[Nova] search failed:',e);
      }finally{
        setSearching(false);
      }
    },350);
  },[searchQ]);

  const isSearching=searchQ.trim().length>0;
  const displayList=isSearching?(searchResults||[]):filtered;

  return h('div',{className:'page'},
    h('div',{className:'page-head', style:{display:'flex',alignItems:'flex-end',justifyContent:'space-between',gap:16,flexWrap:'wrap'}},
      h('div',null,
        h('h1',{className:'greeting'},'Individual Progress'),
        h('div',{className:'sub'},
          isSearching
            ? `${displayList.length} result${displayList.length===1?'':'s'} for "${searchQ}"`
            : 'Who is moving forward — and who needs a nudge.'
        )),
      !isSearching && h('div',{className:'seg'},
        h('button',{className:filter==='all'?'on':'', onClick:()=>setFilter('all')},'All'),
        h('button',{className:filter==='on_track'?'on':'', onClick:()=>setFilter('on_track')},'On track'),
        h('button',{className:filter==='risk'?'on':'', onClick:()=>setFilter('risk')},`At risk (${riskCount})`))),

    h('div',{className:'people-search-wrap'},
      h('input',{
        type:'text',
        placeholder:'Search employees…',
        value:searchQ,
        onChange:e=>setSearchQ(e.target.value),
        style:{
          width:'100%',
          padding:'10px 16px 10px 40px',
          borderRadius:12,
          border:'1.5px solid var(--line)',
          background:'var(--card)',
          fontSize:14,
          fontWeight:600,
          color:'var(--ink)',
          outline:'none',
          boxSizing:'border-box',
          transition:'border-color .15s',
        }
      }),
      h('span',{style:{
        position:'absolute',left:13,top:'50%',
        transform:'translateY(-50%)',
        color:'var(--muted)',
        pointerEvents:'none',
        display:'flex',
      }}, Icons.search ? Icons.search({size:16}) : h('span',null,'🔍')),
      searchQ && h('button',{
        onClick:()=>{setSearchQ('');setSearchResults(null);},
        style:{
          position:'absolute',right:12,top:'50%',
          transform:'translateY(-50%)',
          background:'none',border:0,
          color:'var(--muted)',fontSize:18,
          cursor:'pointer',lineHeight:1,
          padding:'0 2px',
        }
      },'\xD7')
    ),

    h('div',{className:'card'},
      searching && h('div',{style:{padding:'12px 16px',color:'var(--muted)',fontSize:14,fontWeight:600}},'Searching…'),
      isSearching && !searching && searchScope==='recursive' &&
        h('div',{className:'search-result-note'},
          h('b',null,'Extended view:'),' showing all levels below you'),
      isSearching && !searching && searchScope==='company' &&
        h('div',{className:'search-result-note'},
          h('b',null,'Exec view:'),' searching company-wide'),
      isSearching && !searching && searchResults && searchResults.length===0 &&
        h('div',{style:{padding:'24px 16px',color:'var(--muted)',fontSize:14,fontWeight:600,textAlign:'center'}},
          'No employees found for "',searchQ,'"'),
      h('div',{className:'ppl-head'},
        h('div',null,'Employee'), h('div',null,'Tier'),
        h('div',null,'AI proficiency'),
        h('div',{style:{textAlign:'right'}},'Status')),
      displayList.map((p,i)=>{
        const tierKey=(p.tier&&p.tier!=='—')?p.tier:'starter';
        const t=tierByKey(tierKey)||tierByKey('starter');
        const isSearch=isSearching;
        // prof comes from mapped people (p.prof) or raw search results (p.ai_proficiency).
        // Status is purely proficiency-based: < 20% = at risk, otherwise on track.
        const prof=(p.prof!=null)?p.prof:Math.round(p.ai_proficiency||0);
        const atRisk=prof<20;
        return h('div',{className:'ppl-row',key:p.user_id||i},
          h('div',{className:'who'},
            h(Avatar,{name:p.name, grad:p.av||['#A634FF','#FF4398'], size:'s'}),
            h('div',{style:{minWidth:0}},
              h('div',{className:'nm'},p.name),
              h('div',{className:'rl'},isSearch
                ? (p.department||'')+(p.designation?(' · '+p.designation):'')
                : `${p.role||p.department||''} · ${p.team||p.department||''}`))),
          h('div',null, t ? h('span',{className:'tier-pill'},
            h(Hex,{color:t.color, glyph:tierHexInner(tierKey), active:false, size:24}),
            h('span',{style:{color:t.color}}, p.tier==='—'?'—':t.name))
            : h('span',null,'—')),
          h('div',{style:{display:'flex',alignItems:'center',gap:12}},
            h('div',{className:'bar-wide',style:{flex:1,maxWidth:160}}, h('i',{style:{width:prof+'%',
              background: atRisk?'linear-gradient(90deg,#E23D6E,#FF6B88)':'linear-gradient(90deg,#2ACCFF,#A634FF)'}})),
            h('div',{className:'pct-strong',style:{width:42}}, prof+'%')),
          h('div',{style:{textAlign:'right'}},
            h('span',{className:`status ${atRisk?'risk':'ok'}`},
              h('span',{className:'dot'}),
              atRisk?'At risk':'On track')));
      })
    )
  );
}

Object.assign(window,{MgrOverview, MgrTeams, MgrPeople});
