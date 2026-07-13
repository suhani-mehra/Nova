/* ===================== Nova — manager views ===================== */
const { useState:useStateM } = React;

const tierByKey = k => NOVA.TIERS.find(t=>t.key===k);

// Region labels/colors for the Overview "Teams" quadrant tab legend — constant,
// doesn't depend on props/state, so it's hoisted to module scope.
const REGION_LABELS={asia:'Asia', na:'North America', eu:'Europe'};
const REGION_COLS={asia:'#FF4398', na:'#2ACCFF', eu:'#A634FF'};

/* Overview "Trend" tab: AI-proficiency line chart + legend. */
function buildTrendView(overview){
  return [
    h(LineChart,{key:'lc', months:overview.months, proficiency:overview.series.proficiency, active:overview.series.active, target:overview.target, total:overview.total}),
    h('div',{key:'lg',className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
      h('div',{className:'it'}, h('span',{className:'sw',style:{background:'linear-gradient(90deg,#2ACCFF,#A634FF,#FF4398)'}}),'% AI-proficient employees'),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#2ACCFF'}}),'% active learners'),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#FF4398'}}),`Target ${overview.target}%`)),
  ];
}

/* Overview "By region" tab: regional proficiency bars + legend. */
function buildRegionView(region){
  return [
    h(RegionProficiencyChart,{key:'rc', data:region}),
    region && region.regions && h('div',{key:'rg',className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
      region.regions.map(r=>h('div',{className:'it',key:r.key},
        h('span',{style:{width:13,height:13,borderRadius:3,background:r.color,display:'inline-block'}}),
        `${r.label} · ${r.total.toLocaleString()}`)),
      h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#3a3d57'}}),'Goal per level'),
      h('div',{className:'it'}, h('span',{style:{width:16,height:2,background:'#7a7d96',display:'inline-block'}}),'Company-wide actual')),
  ];
}

/* Overview "Teams" tab: 4-quadrant scatter + legend. Interactive (highlight
   cross-linked with the Team Leaderboard), so it takes the highlight state
   and its setters as params rather than being a pure function of `quadrant`. */
function buildQuadrantView(quadrant, activeHl, onHover, onSelect){
  return quadrant ? [
    h(QuadrantChart,{key:'qc', data:quadrant, highlightIds:activeHl, onHover, onSelect}),
    h('div',{key:'qg',className:'chart-legend', style:{marginTop:14, justifyContent:'center'}},
      ['asia','na','eu'].map(k=>h('div',{className:'it',key:k},
        h('span',{style:{width:13,height:13,borderRadius:'50%',background:REGION_COLS[k],display:'inline-block'}}),
        REGION_LABELS[k]))),
  ] : [
    h('div',{key:'qe',style:{padding:'40px 6px',color:'var(--muted)',fontSize:13,fontWeight:600,textAlign:'center'}},'No team data yet.'),
  ];
}

// Lift the radar's visual floor so a 0 sits at the 25% ring instead of collapsing
// to the center — matches the employee Skill Growth radar (_radarShift).
const _mgrRadarShift = arr => (arr||[]).map(v => 25 + Math.round((v / 100) * 75));

/* ---------- streak pill (shared by people rows) ---------- */
function StreakPill({days}){
  if(!days || days<=0) return null;
  return h('span',{className:'streak-pill'}, '🔥', days);
}

/* ---------- Proficiency by Vertical (ranked bars, STATIC placeholder) ---------- */
/* TODO: replace with a real API once a business-vertical taxonomy exists. */
function VerticalBars({data}){
  const [hover,setHover]=useStateM(null);
  const plotH=230, labelH=40;   // fixed plot + label areas → bars share a baseline, titles align
  return h('div',{style:{position:'relative',width:'100%'}},
    // baseline the bars rest on
    h('div',{style:{position:'absolute',left:0,right:0,top:plotH,height:2,background:'var(--chart-grid)',borderRadius:2}}),
    h('div',{style:{display:'flex',alignItems:'flex-start',gap:16}},
      data.map((v,i)=>{
        const active=hover===i;
        return h('div',{key:i,onMouseEnter:()=>setHover(i),onMouseLeave:()=>setHover(null),
          style:{flex:1,minWidth:0,display:'flex',flexDirection:'column',alignItems:'center',cursor:'pointer'}},
          // plot area: fixed height, bar bottom-anchored so every bar rests on the same baseline
          h('div',{style:{height:plotH,width:'100%',display:'flex',flexDirection:'column',alignItems:'center',justifyContent:'flex-end'}},
            h('div',{style:{fontSize:14,fontWeight:800,color:v.top?'#FF4398':'var(--ink)',marginBottom:6}}, v.pct+'%'),
            h('div',{style:{width:'100%',maxWidth:46,height:(v.pct/100)*(plotH-28),borderRadius:'7px 7px 0 0',
              background:v.top?'linear-gradient(180deg,#FF4398,#C21178)':'linear-gradient(180deg,#B266FF,#7d1fd0)',
              filter:active?'brightness(1.08)':'none',transition:'filter .15s'}})),
          // label area: fixed height, top-aligned so all titles start on the same line
          h('div',{style:{height:labelH,marginTop:12,display:'flex',flexDirection:'column',alignItems:'center'}},
            h('div',{style:{fontSize:12,fontWeight:700,color:'var(--ink-soft)',textAlign:'center',lineHeight:1.2}}, v.name)));
      })),
    hover!==null && h('div',{className:'chart-tip',style:_tipStyle((hover+0.5)/data.length, 0)},
      h('div',{className:'t1'}, data[hover].name+' · '+data[hover].pct+'% proficient'),
      h('div',{style:{fontSize:11,color:'#b7b9cc',fontWeight:600}},
        (data[hover].earners!=null && data[hover].total!=null)
          ? _fmtNum(data[hover].earners)+' of '+_fmtNum(data[hover].total)+' proficient'
          : _fmtNum(data[hover].earners)+' earners'))
  );
}

/* ---------- Specialization Landscape (horizontal stacked, STATIC placeholder) ---------- */
/* TODO: replace with a real API once a specialization-track taxonomy exists. */
function SpecializationBar({data}){
  const [hover,setHover]=useStateM(null);
  return h('div',null,
    h('div',{style:{display:'flex',height:34,borderRadius:9,overflow:'hidden'}},
      data.map((t,i)=>h('div',{key:i,onMouseEnter:()=>setHover(i),onMouseLeave:()=>setHover(null),
        style:{width:t.pct+'%',background:t.col,display:'grid',placeItems:'center',
          borderRight:i<data.length-1?'2px solid var(--card)':'none',cursor:'pointer',
          filter:hover===i?'brightness(1.1)':'none',transition:'filter .15s'}},
        t.pct>=12?h('span',{style:{fontSize:11.5,fontWeight:800,color:'#fff'}}, t.pct+'%'):null))),
    h('div',{style:{display:'flex',flexDirection:'column',gap:9,marginTop:16}},
      data.map((t,i)=>h('div',{key:i,onMouseEnter:()=>setHover(i),onMouseLeave:()=>setHover(null),
        style:{display:'flex',alignItems:'center',gap:10,fontSize:12.5,cursor:'pointer',
          opacity:(hover===null||hover===i)?1:.5,transition:'opacity .15s'}},
        h('span',{style:{width:12,height:12,borderRadius:4,background:t.col,flex:'0 0 auto'}}),
        h('span',{style:{fontWeight:700,color:'var(--ink)',flex:1,minWidth:0}}, t.track),
        h('span',{style:{fontWeight:800,color:'var(--ink)'}}, t.pct+'%'))))
  );
}

/* ---------- Team Leaderboard (REAL per-dept proficiency: name + bar + %) ---------- */
// Client-side fuzzy match over already-loaded rows, mirroring the backend
// _fuzzy_filter in routers/manager.py: exact substring (ranked prefix →
// word-start → anywhere), then all-tokens match, then Levenshtein ≤ 2.
function _leaderboardFuzzy(rows, q){
  if(!q || !q.trim()) return rows;
  q = q.trim().toLowerCase();
  const tokens = q.split(/\s+/);
  const lev = (a,b)=>{
    if(a===b) return 0;
    if(!a) return b.length;
    if(!b) return a.length;
    let prev = Array.from({length:b.length+1},(_,i)=>i);
    for(let i=0;i<a.length;i++){
      const curr=[i+1];
      for(let j=0;j<b.length;j++){
        curr.push(Math.min(prev[j]+(a[i]===b[j]?0:1), curr[j]+1, prev[j+1]+1));
      }
      prev=curr;
    }
    return prev[b.length];
  };
  const pos = (name,query)=>{
    if(name.startsWith(query)) return 0;
    if(name.split(/\s+/).some(w=>w.startsWith(query))) return 1;
    return 2;
  };
  const exact=[], tok=[], fz=[];
  for(const r of rows){
    const name=(r.name||'').toLowerCase();
    if(!name) continue;
    if(name.includes(q)){
      exact.push([pos(name,q), r]);
    } else if(tokens.every(t=>name.includes(t))){
      tok.push([Math.min(...tokens.map(t=>pos(name,t))), r]);
    } else {
      const words=name.split(/\s+/);
      const hit=tokens.some(t=>words.some(w=>Math.abs(t.length-w.length)<=2 && lev(t,w)<=2));
      if(hit) fz.push(r);
    }
  }
  exact.sort((a,b)=>a[0]-b[0]);
  tok.sort((a,b)=>a[0]-b[0]);
  return [...exact.map(x=>x[1]), ...tok.map(x=>x[1]), ...fz];
}

function TeamLeaderboard({rows, highlightIds, onHoverRow, scrollToId}){
  const [asc,setAsc]=useStateM(false);   // false = High → Low (default)
  const [q,setQ]=useStateM('');
  const hl = new Set((highlightIds||[]).map(String));

  // When a team is selected on the quadrant chart, reveal + scroll its row.
  React.useEffect(()=>{
    if(scrollToId==null) return;
    // If an active search hides the target row, clear the search so it renders.
    if(q.trim() && !_leaderboardFuzzy(rows||[], q).some(t=>String(t.manager_id)===String(scrollToId))){
      setQ('');
    }
    const scroll=()=>{ const el=document.getElementById('lb-row-'+scrollToId);
      if(el){ el.scrollIntoView({behavior:'smooth',block:'center'}); return true; } return false; };
    if(scroll()) return;
    const t=setTimeout(scroll,90);
    return ()=>clearTimeout(t);
  },[scrollToId]);

  if(!rows || !rows.length){
    return h('div',{style:{padding:'24px 6px',color:'var(--muted)',fontSize:13,fontWeight:600}},'No team data yet.');
  }

  // True competitive rank (1 = highest score), stable regardless of the display
  // sort direction or an active search filter.
  const ranked = rows.slice().sort((a,b)=>b.prof-a.prof).map((t,i)=>Object.assign({}, t, {rank:i+1}));
  const isSearching = q.trim().length > 0;
  let list = _leaderboardFuzzy(ranked, q);
  // While searching, keep _leaderboardFuzzy's best-match-first order — the sort
  // toggle only applies to the full (unsearched) leaderboard.
  if(!isSearching) list = list.slice().sort((a,b)=> asc ? (a.prof-b.prof) : (b.prof-a.prof));

  return h('div',null,
    // controls: fuzzy search + sort-direction toggle
    h('div',{style:{display:'flex',gap:8,marginBottom:12,alignItems:'center'}},
      h('div',{style:{position:'relative',flex:1,minWidth:0}},
        h('input',{type:'text', placeholder:'Search manager…', value:q,
          onChange:e=>setQ(e.target.value),
          style:{width:'100%',padding:'8px 28px 8px 32px',borderRadius:10,border:'1.5px solid var(--line)',
            background:'var(--card)',fontSize:13,fontWeight:600,color:'var(--ink)',outline:'none',boxSizing:'border-box'}}),
        h('span',{style:{position:'absolute',left:10,top:'50%',transform:'translateY(-50%)',color:'var(--muted)',pointerEvents:'none',display:'flex'}},
          Icons.search ? Icons.search({size:14}) : h('span',{style:{fontSize:12}},'🔍')),
        q && h('button',{onClick:()=>setQ(''),
          style:{position:'absolute',right:7,top:'50%',transform:'translateY(-50%)',background:'none',border:0,
            color:'var(--muted)',fontSize:16,cursor:'pointer',lineHeight:1,padding:'0 2px'}},'\xD7')),
      h('button',{className:'lb-sort-btn', onClick:()=>setAsc(a=>!a), disabled:isSearching,
        title:isSearching?'Search results are ordered by best match':'Toggle sort order',
        style:{flex:'0 0 auto',display:'flex',alignItems:'center',gap:5,padding:'8px 11px',borderRadius:10,
          border:'1.5px solid var(--line)',background:'var(--card)',color:'var(--ink)',fontSize:12,fontWeight:700,
          cursor:isSearching?'not-allowed':'pointer',opacity:isSearching?.45:1,whiteSpace:'nowrap'}},
        isSearching ? 'Best match' : (asc ? 'Low → High' : 'High → Low'))),

    // scrollable ranked list — all teams, not just the top 6
    h('div',{className:'leader-scroll',style:{maxHeight:360,overflowY:'auto',scrollbarWidth:'thin'}},
      list.length===0
        ? h('div',{style:{padding:'20px 6px',color:'var(--muted)',fontSize:13,fontWeight:600}},'No managers match.')
        : list.map(t=>h('div',{key:t.rank, id:'lb-row-'+t.manager_id,
            className:'leader-row'+(hl.has(String(t.manager_id))?' highlight':''),
            onMouseEnter:()=>onHoverRow&&onHoverRow(t.manager_id),
            onMouseLeave:()=>onHoverRow&&onHoverRow(null)},
            h('div',{style:{width:34,fontSize:13,fontWeight:800,color:'var(--muted)'}}, '#'+t.rank),
            h('div',{style:{flex:1,minWidth:0}},
              h('div',{style:{fontWeight:800,fontSize:14,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}, t.name),
              h('div',{className:'bar-wide',style:{marginTop:6}}, h('i',{style:{width:t.prof+'%',background:'linear-gradient(90deg,#2ACCFF,#A634FF)'}}))),
            h('div',{style:{fontWeight:800,fontSize:15,width:44,textAlign:'right'}}, t.prof+'%'))))
  );
}

/* ---------------- OVERVIEW (exec managers only) ---------------- */
function MgrOverview(){
  const mgrData=NOVA.manager;
  const overview=mgrData && mgrData.overview;
  const staticData=(mgrData && mgrData.static) || {};
  const [chartTab,setChartTab]=useStateM('trend');   // 'trend' (line) | 'region' (bars) | 'teams' (quadrant)
  const [hlManagers,setHlManagers]=useStateM(null);  // transient hover highlight (quadrant ↔ leaderboard)
  const [selManager,setSelManager]=useStateM(null);  // clicked team — persists + scrolls leaderboard

  if(!overview){
    return h('div',{className:'page'},
      h('div',{style:{padding:'48px 0',textAlign:'center',color:'var(--muted)',fontSize:14,fontWeight:600}},
        'Loading company overview…'));
  }

  const region=overview.proficiencyByRegion;
  const quadrant=overview.teamQuadrant;
  const activeLearners=overview.activeLearners;

  const trendView = buildTrendView(overview);
  const regionView = buildRegionView(region);

  // Hover highlight takes precedence; otherwise the clicked team stays highlighted.
  const activeHl = hlManagers || (selManager!=null ? [selManager] : null);

  const quadrantView = buildQuadrantView(quadrant, activeHl, setHlManagers, setSelManager);

  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},'Learning Overview'),
      h('div',{className:'sub'},`Company-wide progress toward AI proficiency · ${overview.total.toLocaleString()} employees`)),

    h('div',{className:'mgr-cols'},
      // ── left column ──
      h('div',{className:'mgr-col-main'},
        h('div',{className:'card', style:{minHeight:560,display:'flex',flexDirection:'column'}},
          h('div',{className:'card-head-row', style:{marginBottom:16}},
            h('div',null,
              h('div',{className:'card-title'}, chartTab==='trend'?'AI Proficiency Trend':chartTab==='region'?'AI Proficiency by Region':'Team Landscape'),
              h('div',{className:'card-sub'}, chartTab==='trend'
                ? '% of all employees with AI proficiency ≥ 30%, measured at each quarter end.'
                : chartTab==='region'
                ? "Each region's own % proficient at each level, vs. the company-wide actual and goal."
                : "Each dot is a team — average AI proficiency vs. activity; size = teams clustered, color = manager's region.")),
            h('div',{className:'seg'},
              h('button',{className:chartTab==='trend'?'on':'', onClick:()=>setChartTab('trend')},'Trend'),
              h('button',{className:chartTab==='region'?'on':'', onClick:()=>setChartTab('region')},'By region'),
              h('button',{className:chartTab==='teams'?'on':'', onClick:()=>setChartTab('teams')},'Teams'))),
          h('div',{key:chartTab, className:'chart-swap', style:{flex:1,display:'flex',flexDirection:'column',justifyContent:'center'}},
            chartTab==='trend' ? trendView : chartTab==='region' ? regionView : quadrantView)
        ),
        h('div',{className:'card', style:{flex:1,display:'flex',flexDirection:'column'}},
          h('div',{className:'card-title'},'Proficiency by Vertical'),
          h('div',{className:'card-sub', style:{marginBottom:14}},'% AI-proficient within each business vertical, ranked.'),
          h('div',{style:{flex:1,display:'flex',alignItems:'flex-end'}}, h(VerticalBars,{data:(overview && overview.proficiencyByVertical) || staticData.verticals || []})))
      ),

      // ── right rail ──
      h('div',{className:'mgr-col-rail'},
        h('div',{className:'hero-card hero-purple'},
          h('div',{className:'hero-lab'}, Icons.users({size:16,color:'#fff'}), 'Active learners this week'),
          h('div',{className:'hero-num'}, _fmtNum(activeLearners.count)),
          h('div',{className:'hero-sub'}, `${activeLearners.pct.toFixed(1)}% of ${activeLearners.total.toLocaleString()} employees`)),
        h('div',{className:'card'},
          h('div',{className:'card-title'},'Specialization Landscape'),
          h('div',{className:'card-sub', style:{marginBottom:16}},'Share of AI-proficient employees by role group.'),
          h(SpecializationBar,{data:(overview && overview.specialization) || staticData.specialization || []})),
        h('div',{className:'card', style:{flex:1}},
          h('div',{className:'card-title', style:{marginBottom:16}},'Team Leaderboard'),
          h(TeamLeaderboard,{rows:overview.teamLeaderboard, highlightIds:activeHl,
            onHoverRow:(id)=>setHlManagers(id?[id]:null), scrollToId:selManager}))
      )
    )
  );
}

/* Maps direct-report rows into Team Landscape scatter points. x = all-time
   active days, y = AI proficiency (consistent with the Overview quadrant).
   Pure function of `people` — the scatter always shows the FULL team,
   ignoring the current filter/search. */
function buildScatterPoints(people){
  return people.map(p=>{
    const prof=(p.prof!=null)?p.prof:Math.round(p.ai_proficiency||0);
    return {id:p.user_id, name:p.name, x:p.activeDays||0, y:prof, atRisk:(p.status==='risk')||prof<20};
  });
}

/* ---------------- YOUR TEAM (all managers, direct reports only) ---------------- */
function MgrYourTeam(){
  const mgrData=NOVA.manager;
  const teamData=(mgrData && mgrData.team) || {people:[], radar:null, badges:null, size:0, riskCount:0};
  const [filter,setFilter]=useStateM('all');
  const [searchQ,setSearchQ]=useStateM('');
  const [searchResults,setSearchResults]=useStateM(null);
  const [searching,setSearching]=useStateM(false);
  const [searchScope,setSearchScope]=useStateM('');
  const [compareTeam,setCompareTeam]=useStateM(null);
  const [comparePicker,setComparePicker]=useStateM(false);
  const [selectedId,setSelectedId]=useStateM(null);
  const debounceRef=React.useRef(null);

  const people=teamData.people||[];
  const topTeams=teamData.topTeams||[];
  const filtered = people.filter(p=>{
    if(filter==='all') return true;
    if(filter==='on_track') return p.status==='ok';
    if(filter==='risk') return p.status==='risk';
    return true;
  });
  const riskCount=people.filter(p=>p.status==='risk').length;

  React.useEffect(()=>{
    if(debounceRef.current) clearTimeout(debounceRef.current);
    if(!searchQ.trim()){ setSearchResults(null); setSearchScope(''); return; }
    debounceRef.current=setTimeout(async ()=>{
      setSearching(true);
      try{
        const res=await apiGet('/api/manager/people/search?q='+encodeURIComponent(searchQ.trim()));
        if(res){ setSearchResults(res.employees||[]); setSearchScope(res.search_scope||''); }
      }catch(e){ console.warn('[Nova] search failed:',e); }
      finally{ setSearching(false); }
    },350);
  },[searchQ]);

  const isSearching=searchQ.trim().length>0;
  const displayList=isSearching?(searchResults||[]):filtered;

  const scatterPoints=buildScatterPoints(people);

  // Two-way selection between scatter and table.
  const onSelectPerson=(id)=>{
    if(selectedId===id){ setSelectedId(null); return; }
    const visible=displayList.some(p=>p.user_id===id);
    if(!visible){ setFilter('all'); setSearchQ(''); }  // reveal a filtered/searched-out person
    setSelectedId(id);
  };

  // Scroll the selected person's row into view (retry once after re-render, since
  // clearing a filter/search re-renders the table).
  React.useEffect(()=>{
    if(selectedId==null) return;
    const scroll=()=>{ const el=document.getElementById('ppl-row-'+selectedId);
      if(el){ el.scrollIntoView({behavior:'smooth',block:'center'}); return true; } return false; };
    if(scroll()) return;
    const t=setTimeout(scroll,90);
    return ()=>clearTimeout(t);
  },[selectedId]);

  const radar=teamData.radar;
  const badges=teamData.badges||{total:0,avgPerPerson:0,thisMonthCount:0,byTier:{}};
  const active=teamData.activeThisWeek||{count:0,total:teamData.size,pct:0};

  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},'Your Team'),
      h('div',{className:'sub'},`Your direct reports · ${teamData.size} ${teamData.size===1?'person':'people'} — proficiency, badges, and streaks.`)),

    // top row: radar + badges donut (left) · active learners (right)
    h('div',{className:'mgr-cols mgr-team-top', style:{marginBottom:22}},
      h('div',{className:'card mgr-team-radar'},
        h('div',{className:'card-title'},'Team Average Proficiency'),
        h('div',{className:'card-sub'},'Mean proficiency across your team in each skill category.'),
        radar ? h(RadarChart,{axes:radar.axes, thisMonth:_mgrRadarShift(radar.this_month), lastMonth:_mgrRadarShift(radar.last_month),
                  compareWith: compareTeam?_mgrRadarShift(compareTeam.this_month):null, labelValues:radar.this_month, size:280})
              : h('div',{style:{padding:'40px 0',textAlign:'center',color:'var(--muted)',fontWeight:600}},'No skill data yet.'),
        h('div',{className:'chart-legend', style:{justifyContent:'center',marginTop:8}},
          h('div',{className:'it'}, h('span',{style:{width:14,height:14,borderRadius:4,background:'#A634FF',display:'inline-block'}}),'This month'),
          h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#2ACCFF'}}),'Last month'),
          compareTeam && h('div',{className:'it'}, h('span',{className:'sw dash',style:{borderColor:'#1FA971'}}), compareTeam.name)),
        topTeams.length>0 && h('div',{className:'compare-wrap'},
          h('button',{className:'btn-compare',onClick:()=>setComparePicker(o=>!o)},
            Icons.users({size:14}),
            comparePicker ? 'Cancel' : (compareTeam ? 'Change' : 'Compare with top team')),
          compareTeam && !comparePicker && h('button',{className:'btn-compare-clear',onClick:()=>setCompareTeam(null)},
            '✕ Clear comparison'),
          comparePicker && h('div',{className:'compare-picker'},
            topTeams.map(t=>h('button',{key:t.manager_id, className:'compare-opt',
              onClick:()=>{ setCompareTeam(t); setComparePicker(false); }},
              h('span',{style:{fontWeight:800,flex:1,textAlign:'left'}}, t.name),
              h('span',{style:{color:'var(--muted)',fontWeight:700}}, t.avgSkill+'%')))))),
      h('div',{className:'card mgr-team-badges', style:{display:'flex',flexDirection:'column'}},
        h('div',{className:'card-title', style:{marginBottom:6}},'Badges by tier'),
        h('div',{className:'card-sub', style:{marginBottom:16}},'Every badge your team has earned.'),
        h('div',{style:{flex:1,display:'flex',alignItems:'center',justifyContent:'center'}},
          h(DonutChart,{
            segments:['platinum','diamond','gold','silver','bronze'].map(k=>({
              name:(tierByKey(k)||{}).name||k, color:(tierByKey(k)||{}).color||'#9aa2b1', value:badges.byTier[k]||0})),
            centerValue:badges.total, centerLabel:'badges', size:180}))),
      h('div',{className:'mgr-team-rail'},
        h('div',{className:'hero-card hero-purple hero-mini'},
          h('div',{className:'hero-lab'}, Icons.users({size:15,color:'#fff'}), 'Active learners this week'),
          h('div',{className:'hero-num hero-num-sm'}, _fmtNum(active.count))),
        h('div',{className:'card mgr-team-scatter', style:{flex:1,display:'flex',flexDirection:'column'}},
          h('div',{className:'card-title'},'Team Landscape'),
          h('div',{className:'card-sub', style:{marginBottom:8}},'Each dot is a teammate — active days vs. AI proficiency.'),
          h('div',{style:{flex:1,display:'flex',alignItems:'center'}},
            h(ScatterChart,{points:scatterPoints, selectedId, onSelect:onSelectPerson, compact:true}))))
    ),

    // people table
    h('div',{className:'page-head', style:{display:'flex',alignItems:'flex-end',justifyContent:'space-between',gap:16,flexWrap:'wrap'}},
      h('div',null,
        h('div',{className:'card-title', style:{fontSize:19}},'Individual Progress'),
        h('div',{className:'card-sub'},
          isSearching ? `${displayList.length} result${displayList.length===1?'':'s'} for "${searchQ}"`
                      : 'Who is moving forward — and who needs a nudge.')),
      !isSearching && h('div',{className:'seg'},
        h('button',{className:filter==='all'?'on':'', onClick:()=>setFilter('all')},'All'),
        h('button',{className:filter==='on_track'?'on':'', onClick:()=>setFilter('on_track')},'On track'),
        h('button',{className:filter==='risk'?'on':'', onClick:()=>setFilter('risk')},`At risk (${riskCount})`))),

    h('div',{className:'people-search-wrap'},
      h('input',{type:'text', placeholder:'Search your team…', value:searchQ,
        onChange:e=>setSearchQ(e.target.value),
        style:{width:'100%',padding:'10px 16px 10px 40px',borderRadius:12,border:'1.5px solid var(--line)',
          background:'var(--card)',fontSize:14,fontWeight:600,color:'var(--ink)',outline:'none',boxSizing:'border-box'}}),
      h('span',{style:{position:'absolute',left:13,top:'50%',transform:'translateY(-50%)',color:'var(--muted)',pointerEvents:'none',display:'flex'}},
        Icons.search ? Icons.search({size:16}) : h('span',null,'🔍')),
      searchQ && h('button',{onClick:()=>{setSearchQ('');setSearchResults(null);},
        style:{position:'absolute',right:12,top:'50%',transform:'translateY(-50%)',background:'none',border:0,
          color:'var(--muted)',fontSize:18,cursor:'pointer',lineHeight:1,padding:'0 2px'}},'\xD7')
    ),

    h('div',{className:'card'},
      searching && h('div',{style:{padding:'12px 16px',color:'var(--muted)',fontSize:14,fontWeight:600}},'Searching…'),
      isSearching && !searching && searchScope==='recursive' &&
        h('div',{className:'search-result-note'}, h('b',null,'Extended view:'),' showing all levels below you'),
      isSearching && !searching && searchScope==='company' &&
        h('div',{className:'search-result-note'}, h('b',null,'Exec view:'),' searching company-wide'),
      isSearching && !searching && searchResults && searchResults.length===0 &&
        h('div',{style:{padding:'24px 16px',color:'var(--muted)',fontSize:14,fontWeight:600,textAlign:'center'}},
          'No employees found for "',searchQ,'"'),
      h('div',{className:'ppl-head'},
        h('div',null,'Employee'), h('div',null,'Tier'),
        h('div',null,'AI proficiency'), h('div',{style:{textAlign:'right'}},'Status')),
      displayList.map((p,i)=>{
        const tierKey=(p.tier&&p.tier!=='—')?p.tier:'starter';
        const t=tierByKey(tierKey)||tierByKey('starter');
        const isSearch=isSearching;
        const prof=(p.prof!=null)?p.prof:Math.round(p.ai_proficiency||0);
        const streak=(p.streak!=null)?p.streak:(p.streak_days||0);
        const atRisk=prof<20;
        return h('div',{className:'ppl-row'+(p.user_id===selectedId?' selected':''), key:p.user_id||i,
            id:'ppl-row-'+(p.user_id||i), onClick:()=>onSelectPerson(p.user_id), style:{cursor:'pointer'}},
          h('div',{className:'who'},
            h(Avatar,{name:p.name, grad:p.av||['#A634FF','#FF4398'], size:'s'}),
            h('div',{style:{minWidth:0}},
              h('div',{className:'nm', style:{display:'flex',alignItems:'center',gap:8}},
                h('span',null,p.name), h(StreakPill,{days:streak})),
              h('div',{className:'rl'},isSearch
                ? (p.department||'')+(p.designation?(' · '+p.designation):'')
                : `${p.role||p.department||''}`))),
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
              h('span',{className:'dot'}), atRisk?'At risk':'On track')));
      })
    )
  );
}

Object.assign(window,{MgrOverview, MgrYourTeam});
