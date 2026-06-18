
/* ===================== Nova — employee views ===================== */
const { useState:useStateE } = React;

function DotsDeco({rows=6, cols=10}){
  const dots=[];
  for(let r=0;r<rows;r++) for(let c=0;c<cols;c++){
    dots.push(h('circle',{key:`${r}-${c}`, cx:c*9+2, cy:r*9+2, r:1.5, fill:'#A634FF',
      opacity: 0.04 + (c/cols)*0.16}));
  }
  return h('svg',{className:'dots-deco', width:cols*9, height:rows*9}, dots);
}

function CourseTile({grad, glyph, size=54, glyphSize=1}){
  return h('div',{className:'course-tile', style:{width:size,height:size,background:`linear-gradient(135deg,${grad[0]},${grad[1]})`, position:'relative'}}, 
    h('div', {style:{position:'absolute', top:'50%', left:'50%', transform:'translate(-50%, -57%)', fontSize:size*glyphSize, lineHeight:1}}, glyph));
}

function CongratsButton(){
  const [sent,setSent]=useStateE(false);
  return h('button',{className:'btn-congrats'+(sent?' sent':''), onClick:()=>setSent(true), disabled:sent},
    sent?'Sent ✓':'Congrats');
}

const achIcon = (type)=>{
  const map={
    course:{ic:Icons.book({size:20,color:'#A634FF'})},
    diamond:{ic:Icons.spark({size:20,color:'#2ACCFF'})},
    streak:{ic:Icons.fire({size:20,color:'#FF6A2C',sw:1.6})},
    gold:{ic:h(Hex,{color:'#f5b71e',glyph:tierHexInner('gold'),active:true,size:24})},
  };
  return (map[type]||map.course).ic;
};

/* ---------------- MY PROGRESS ---------------- */
function MyProgress(){
  const E=NOVA.employee, T=NOVA.TIERS;
  const days=['M','T','W','T','F','S','S'];
  return h('div',{className:'page'},
    // Tier card
    h('div',{className:'card tier-card', style:{marginBottom:22}},
      h('div',{className:'tier-main'},
        h('div',{className:'card-title'},'Your Tier'),
        h(TierTrack,{tiers:T, currentKey:E.currentTier}),
        h('div',{className:'tier-progress'},
          h(Hex,{color:'#f5b71e',glyph:tierHexInner('gold'),active:true,size:42}),
          h('div',{className:'bar'}, h('i',{style:{width:E.tierProgress+'%'}})),
          h(Hex,{color:'#2ACCFF',glyph:tierHexInner('diamond'),active:false,size:42})
        ),
        h('div',{className:'next-tier'},'Next tier: ', h('b',null,'Diamond'))
      ),
      h('aside',{className:'badges'},
        h('div',{className:'badges-title'},'Badges Earned'),
        h('div',{className:'badge-rows'},
          E.badges.map((g,gi)=>h('div',{className:'badge-row',key:gi},
            Array.from({length:g.count}).map((_,i)=>
              h(Ribbon,{key:i, color:g.color, glyph:g.glyph})))))
      )
    ),
    // 3 cards
    h('div',{className:'grid cols-3'},
      // streak
      h('div',{className:'card tall'},
        h('div',{className:'card-title', style:{alignSelf:'flex-start', margin:0}},'Streak'),
        h('div', {className: 'streak-card'},
        h('div',{className:'flame'},'🔥'),
        h('div',{className:'streak-num'},E.streak),
        h('div',{className:'streak-lab'},'Day Streak'),
        h('div',{className:'streak-week'}, days.map((d,i)=>
          h('div',{key:i,className:'d'+(E.streakWeek[i]?' on':'')}, d)))
      )
      ),
      // skill growth
      h('div',{className:'card tall'},
        h('div',{className:'card-title'},'Skill Growth'),
        h(RadarChart,{axes:E.skills.axes, thisMonth:E.skills.thisMonth, lastMonth:E.skills.lastMonth, size:300}),
        h('div',{className:'legend'},
          h('div',{className:'it'}, h('span',{className:'sw',style:{background:'#A634FF'}}),'This Month'),
          h('div',{className:'it'}, h('span',{className:'sw',style:{background:'#2ACCFF',opacity:.7}}),'Last Month')),
        h('div',{className:'delta'}, h('b',null,`+${E.skills.delta}%`),' ', h('span',null,'vs last month'))
      ),
      // continue + recommended
      h('div',{style:{display:'flex',flexDirection:'column',gap:22}},
        h('div',{className:'card'},
          h('div',{className:'card-title', style:{marginBottom:18}},'Continue Learning'),
          h('div',{className:'course-row'},
            h(CourseTile,{grad:E.continueCourse.tile, glyph:'⚛', glyphSize:.8}),
            h('div',null,
              h('div',{className:'course-name'},E.continueCourse.name),
              h('div',{className:'course-meta', style:{color:'#FF4398'}},E.continueCourse.status))),
          h('div',{style:{display:'flex',alignItems:'center',gap:12,margin:'18px 0'}},
            h('div',{className:'pbar', style:{flex:1}}, h('i',{style:{width:E.continueCourse.progress+'%'}})),
            h('div',{style:{fontWeight:700,fontSize:13.5,color:'var(--ink-soft)'}},`${E.continueCourse.progress}% Complete`)),
          h('button',{className:'btn-grad'},'Continue Learning')),
        h('div',{className:'card'},
          h('div',{className:'card-title', style:{marginBottom:16}},'Recommended for You'),
          h('div',{className:'reco'},
            h(CourseTile,{grad:E.recommended.tile, glyph:'AI', size:42, glyphSize:0.7}),
            h('div',null,
              h('div',{style:{fontWeight:800,fontSize:15.5}},E.recommended.name),
              h('div',{className:'course-meta',style:{color:'var(--muted)'}},E.recommended.meta)),
            h('span',{className:'arrow'}, Icons.chevR({size:20}))))
      )
    )
  );
}

/* ---------------- MY TEAM ---------------- */
function MyTeam(){
  const TM=NOVA.team, first=NOVA.accounts.employee.first;
  const hr=new Date().getHours();
  const part = hr<12?'morning':hr<18?'afternoon':'evening';
  return h('div',{className:'page'},
    h('div',{className:'page-head'},
      h('h1',{className:'greeting'},`Good ${part}, ${first}! 👋`),
      h('div',{className:'sub'},"Here's what your team has accomplished.")),
    // highlights
    h('div',{className:'card', style:{marginBottom:22}},
      h(DotsDeco,null),
      h('div',{className:'card-title'},'Team Highlights'),
      h('div',{className:'highlights'},
        h('div',{className:'hl'},
          h('div',{className:'ic',style:{background:'rgba(166,52,255,.1)'}},h('i', {className: 'fas fa-hands-clapping', style: {color: "rgb(167, 53, 255)", fontSize: 26}})),
          h('div',null, h('div',{className:'big'},TM.highlights.congrats),
            h('div',{className:'lab'},'Congrats sent this week'))),
        h('div',{className:'hl'},
          h('div',{className:'ic',style:{background:'rgba(42,204,255,.12)'}}, Icons.book({size:26,color:'#2ACCFF'})),
          h('div',null, h('div',{className:'lab strong'},TM.highlights.topCourse),
            h('div',{className:'lab'},'Most completed course'))),
        h('div',{className:'hl'},
          h('div',{className:'ic',style:{background:'rgba(31,169,113,.12)'}}, Icons.trend({size:26,color:'#1FA971'})),
          h('div',null, h('div',{className:'big',style:{color:'#1FA971'}},`+${TM.highlights.timeDelta}%`),
            h('div',{className:'lab'},'Team learning time vs last week')))
      )
    ),
    // two columns
    h('div',{className:'grid cols-2'},
      // accomplishments
      h('div',{className:'card',style:{display:'flex',flexDirection:'column'}},
        h('div',{className:'card-title'},'Team Accomplishments'),
        h('div',{className:'card-sub'},'See what your team has achieved this week.'),
        h('div',{style:{position:'relative',flex:1,minHeight:0}},
          h('div',{className:'acc-list',style:{position:'absolute',top:0,left:0,right:0,bottom:0,overflowY:'auto',scrollbarWidth:'thin'}},
            TM.accomplishments.map((a,i)=>h('div',{className:'acc',key:i},
              h(Avatar,{name:a.name, grad:a.av, size:'s'}),
              h('span',{className:'ach-ico'}, achIcon(a.type)),
              h('div',{className:'body'},
                h('div',{className:'txt'}, h('span',{className:'nm'},a.name),' ',
                  h('span',{className:'verb'},a.verb),' ',
                  h('span',{className:'ach'},a.ach))),
              h('div',{className:'time'},a.time),
              h(CongratsButton,null))))),
      ),
      // recommended
      h('div',{className:'card'},
        h('div',{className:'card-title'},'Recommended for You'),
        h('div',{className:'card-sub'},'Courses most of your team has completed.'),
        h('div',{style:{display:'flex',flexDirection:'column',gap:14,marginTop:18}},
          TM.recommended.map((c,i)=>h('div',{className:'reco-card',key:i},
            h(CourseTile,{grad:c.tile, glyph:c.glyph, glyphSize:0.8}),
            h('div',{className:'body'},
              h('div',{className:'nm'}, c.name, h('span',{className:`badge ${c.cls}`}, c.badge)),
              h('div',{className:'meta'}, c.meta)),
            h('div',{className:'match'},
              h('div',{className:'pct'}, c.match+'% ', h('span',null,'match')),
              h('div',{className:'pbar',style:{marginTop:8}}, h('i',{style:{width:c.match+'%'}})))))),
        h('div',{className:'link-row',style:{marginTop:18}},
          h('button',{className:'link'},'Browse all courses ', Icons.arrow({size:18})))
      )
    )
  );
}

Object.assign(window,{MyProgress, MyTeam});
