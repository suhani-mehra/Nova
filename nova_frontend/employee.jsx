
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

function CongratsButton({onSend}){
  const [sent,setSent]=useStateE(false);
  return h('button',{className:'btn-congrats'+(sent?' sent':''),
    onClick:()=>{ setSent(true); if(onSend) onSend(); },
    disabled:sent},
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

const _radarShift = arr => arr.map(v => 25 + Math.round((v / 100) * 75));

// Link a recommended course to Classmate's global search for that course name.
const classmateSearchUrl = (name) =>
  'https://learning.orioninc.com/OVSP/Dashboard/GlobalSearch?search=' + encodeURIComponent(name || '');

// Deterministic int from a string — used to give each accomplishment a stable
// activity_id so re-congratulating the same item is de-duplicated server-side.
const _hashStr = (s) => {
  let n = 0;
  s = s || '';
  for (let i = 0; i < s.length; i++) n = (n * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(n);
};

/* Pure greeting helper: hour (0-23) -> time-of-day label. */
function dayPartFromHour(hour){
  return hour<12?'morning':hour<18?'afternoon':'evening';
}

/* Encapsulates the "compare with teammate" concern for the Skill Growth card:
   teammate list fetch (on mount), compare-picker state, and the
   select-a-teammate handler. */
function useCompareTeammate(){
  const [comparePicker, setComparePicker] = useStateE(false);
  const [compareUser,   setCompareUser]   = useStateE(null); // {name, scores}
  const [teammates,     setTeammates]     = useStateE([]);

  React.useEffect(()=>{
    apiGet('/api/employee/teammates').then(data=>{
      if(data && data.teammates) setTeammates(data.teammates);
    });
  },[]);

  const selectCompare = async (tm) => {
    setComparePicker(false);
    const data = await apiGet(`/api/employee/compare/${tm.user_id}`);
    if (data && data.scores) setCompareUser({name: tm.name, scores: data.scores});
  };

  return { comparePicker, setComparePicker, compareUser, setCompareUser, teammates, selectCompare };
}

/* Tier progress track + badges-earned card (full width). */
function TierBadgesCard({tiers, currentTier, nextTier, tierProgress, badges}){
  const curMeta  = TIER_META[currentTier] || TIER_META.gold;
  const nextMeta = TIER_META[nextTier]    || TIER_META.diamond;
  const isPlatinum = currentTier === 'platinum';
  const hasBadges  = badges && badges.length > 0;

  return h('div',{className:'card tier-card fade-up fade-up-2', style:{marginBottom:22, '--cur-tier-glow-1':curMeta.tile[0]+'aa', '--cur-tier-glow-2':curMeta.tile[1]+'55'}},
    h('div',{className:'tier-main'},
      h('div',{className:'card-title'},'Your Tier'),
      h(TierTrack,{tiers:tiers, currentKey:currentTier}),
      isPlatinum
        ? h('div',{className:'platinum-congrats'},
            h('div',{className:'plat-msg'},'🏆 You\'ve reached the top!'),
            h('div',{className:'plat-sub'},'Keep it up to maintain your Platinum rank.'))
        : h(React.Fragment,null,
            h('div',{className:'tier-progress'},
              h(Hex,{color:curMeta.color, glyph:tierHexInner(currentTier), active:true, size:42}),
              h('div',{className:'bar'}, h('i',{style:{width:tierProgress+'%', background:`linear-gradient(90deg, ${curMeta.tile[0]}, ${curMeta.tile[1]})`}})),
              h(Hex,{color:nextMeta.color, glyph:tierHexInner(nextTier), active:false, size:42})
            ),
            h('div',{className:'next-tier'},'Next tier: ', h('b',{style:{color:nextMeta.color}},nextMeta.name))
          )
    ),
    h('aside',{className:'badges'},
      h('div',{className:'badges-title'},'Badges Earned'),
      hasBadges
        ? h('div',{className:'badge-cols'},
            badges.map((g,gi)=>{
              const MAX_STACK=3;                       // ribbons visible per column
              const shown=Math.min(g.count, MAX_STACK);
              const extra=g.count-shown;
              return h('div',{className:'badge-col',key:gi},
                Array.from({length:shown}).map((_,i)=>
                  h(Ribbon,{key:i, color:g.color, glyph:g.glyph})),
                extra>0 ? h('div',{className:'badge-more'}, '+'+extra) : null);
            }))
        : h('div',{className:'badge-empty'},
            h('div',{className:'badge-empty-icon'},'🎖'),
            h('div',{className:'badge-empty-title'},'No badges yet'),
            h('div',{className:'badge-empty-sub'},'Complete a course to earn your first.'))
    )
  );
}

/* "Recommended for You" course list card (col 1). */
function RecommendedCoursesCard({recSource, recommended}){
  return h('div',{className:'card',style:{flex:'1.35 0 0',minWidth:0,position:'relative',overflow:'hidden',padding:0}},
    h('div',{style:{position:'absolute',inset:0,display:'flex',flexDirection:'column',padding:'26px 28px'}},
      h('div',{className:'card-title'},'Recommended for You'),
      h('div',{className:'card-sub'},
        recSource === 'fallback'
          ? 'Waiting for teammates to complete more courses — until then, check these out.'
          : 'Courses most of your team has completed.'),
      h('div',{style:{display:'flex',flexDirection:'column',gap:12,marginTop:14,flex:1,minHeight:0,overflowY:'auto',scrollbarWidth:'thin'}},
        recommended.map((c,i)=>h('a',{className:'reco-card', key:i, href:classmateSearchUrl(c.name), target:'_blank', rel:'noopener noreferrer'},
          h(CourseTile,{grad:c.tile, glyph:(verticalIcon(c.cls) || c.glyph), glyphSize:0.8}),
          h('div',{className:'body'},
            h('div',{className:'nm'}, c.name, h('span',{className:`badge ${c.cls}`}, c.badge)),
            h('div',{className:'meta'}, c.meta)),
          recSource === 'fallback'
            ? null
            : h('div',{className:'match'},
                h('div',{className:'pct'}, c.match+'% ', h('span',null,'match')),
                h('div',{className:'pbar',style:{marginTop:8}}, h('i',{style:{width:c.match+'%'}})))))),
      h('div',{className:'link-row',style:{marginTop:14}},
        h('a',{className:'link',href:'https://learning.orioninc.com/OVSP/Dashboard/Skills',target:'_blank',rel:'noopener noreferrer',style:{textDecoration:'none'}},'Browse all courses ', Icons.arrow({size:18})))
    )
  );
}

/* Skill Growth radar card (col 2) — bundles the radar chart with the
   compare-with-teammate UI, since they're one visual card. */
function SkillGrowthCard({skills, compareUser, comparePicker, teammates, onTogglePicker, onSelectCompare, onClearCompare}){
  return h('div',{style:{flex:'1.2 0 0',minWidth:0,display:'flex',flexDirection:'column',gap:22}},
    h('div',{className:'card tall',style:{flex:1}},
    h('div',{className:'card-title'},'Skill Growth'),
    h(RadarChart,{
      axes:skills.axes,
      thisMonth:_radarShift(skills.thisMonth),
      lastMonth:_radarShift(skills.lastMonth),
      compareWith: compareUser ? _radarShift(compareUser.scores) : null,
      labelValues:skills.thisMonth,
      size:300
    }),
    h('div',{className:'legend'},
      h('div',{className:'it'}, h('span',{className:'sw',style:{background:'#A634FF'}}),'This Month'),
      h('div',{className:'it'}, h('span',{className:'sw',style:{background:'#2ACCFF',opacity:.7}}),'Last Month'),
      compareUser && h('div',{className:'it'},
        h('span',{className:'sw',style:{background:'#1FA971',opacity:.8}}),
        compareUser.name
      )
    ),
    h('div',{className:'delta'}, h('b',null,`+${skills.delta}%`),' ', h('span',null,'vs last month')),
    h('div',{className:'compare-wrap'},
      h('button',{className:'btn-compare',onClick:onTogglePicker},
        Icons.users({size:14}),
        comparePicker ? 'Cancel' : (compareUser ? 'Change' : 'Compare with teammate')
      ),
      compareUser && !comparePicker && h('button',{className:'btn-compare-clear',onClick:onClearCompare},
        '✕ Clear comparison'
      ),
      comparePicker && h('div',{className:'compare-picker'},
        teammates.length===0
          ? h('div',{className:'compare-empty'},'No teammates with recent activity')
          : teammates.map(tm=>h('button',{key:tm.user_id, className:'compare-opt', onClick:()=>onSelectCompare(tm)},
              h(Avatar,{name:tm.name, grad:_nameToGrad(tm.name), size:'s'}),
              tm.name))
      )
    )
  )
  );
}

/* Continue Learning card (col 3) — in-progress course, or an empty prompt. */
function ContinueLearningCard({continueCourse}){
  return continueCourse
    ? h('div',{className:'card'},
        h('div',{className:'card-title', style:{marginBottom:18}},'Continue Learning'),
        h('div',{className:'course-row'},
          h(CourseTile,{grad:continueCourse.tile, glyph:'⚛', glyphSize:.8}),
          h('div',null,
            h('div',{className:'course-name'},continueCourse.name),
            h('div',{className:'course-meta', style:{color:'#FF4398'}},continueCourse.status))),
        h('a',{className:'btn-grad',href:'https://learning.orioninc.com/OVSP/Dashboard',target:'_blank',rel:'noopener noreferrer',style:{textDecoration:'none',display:'block',textAlign:'center',marginTop:18}},'Continue Learning'))
    : h('div',{className:'card'},
        h('div',{className:'card-title'},'Continue Learning'),
        h('div',{style:{color:'var(--muted)',fontSize:14,padding:'18px 0'}},'No course in progress. Browse the library to get started.'),
        h('a',{className:'btn-grad',href:'https://learning.orioninc.com/OVSP/Dashboard',target:'_blank',rel:'noopener noreferrer',style:{textDecoration:'none',display:'block',textAlign:'center'}},'Browse Courses'));
}

/* Team Accomplishments clickable summary box (col 3) — opens the modal below.
   Kept as its own component (not bundled with the modal) so it stays exactly
   where it was in the DOM: nested inside col 3's flex column, using flex:1
   to fill the remaining height there. */
function AccomplishmentsBox({count, congratsReceived, onOpen}){
  return h('div',{className:'card acc-box', style:{flex:1, display:'flex', flexDirection:'column', cursor:'pointer'},
    onClick:onOpen, role:'button', tabIndex:0,
    onKeyDown:(e)=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); onOpen(); } }},
    h('div',{className:'acc-box-head'},
      h('div',null,
        h('div',{className:'card-title', style:{margin:0}},'Team Accomplishments'),
        h('div',{className:'card-sub', style:{margin:0}},`${count} recent — click to view & send congrats`)),
      h('span',{className:'acc-chev'}, Icons.arrow({size:18}))
    ),
    h('div',{className:'congrats-big'},
      h('i',{className:'fa-solid fa-hands-clapping cr-clap'}),
      h('div',{className:'congrats-big-main'},
        h('div',{className:'congrats-big-num'}, congratsReceived||0),
        h('div',{className:'congrats-big-lab'}, ((congratsReceived===1)?'congrat':'congrats')+' received')))
  );
}

/* Team Accomplishments popup — a sibling of the emp-grid's columns (fixed-
   position backdrop, so its DOM nesting depth doesn't affect layout). */
function AccomplishmentsModal({open, accomplishments, onClose, onSendCongrats}){
  return open && h('div',{className:'acc-modal-backdrop', onClick:onClose},
    h('div',{className:'acc-modal', onClick:(e)=>e.stopPropagation()},
      h('div',{className:'acc-modal-head'},
        h('div',null,
          h('div',{className:'card-title', style:{margin:0}},'Team Accomplishments'),
          h('div',{className:'card-sub', style:{margin:0}},'Cheer on your teammates')),
        h('button',{className:'acc-modal-close', onClick:onClose, 'aria-label':'Close'},'✕')
      ),
      h('div',{className:'acc-modal-list'},
        accomplishments.map((a,i)=>h('div',{className:'acc',key:i},
          h(Avatar,{name:a.name, grad:a.av, size:'s'}),
          h('span',{className:'ach-ico'}, achIcon(a.type)),
          h('div',{className:'body'},
            h('div',{className:'txt'}, h('span',{className:'nm'},a.name),' ',
              h('span',{className:'verb'},a.verb),' ',
              h('span',{className:'ach'},a.ach))),
          h('div',{className:'time'},a.time),
          h(CongratsButton,{onSend:()=>onSendCongrats(a)}))))
    )
  );
}

/* ---------------- MY LEARNING (consolidated employee view) ---------------- */
function MyEmployee(){
  const E=NOVA.employee, TM=NOVA.team, tiers=NOVA.TIERS;
  const first=NOVA.accounts.employee.first;
  const days=['M','T','W','T','F','S','S'];
  const currentHour=new Date().getHours();
  const dayPart = dayPartFromHour(currentHour);

  const [accsOpen, setAccsOpen] = useStateE(false);
  const compare = useCompareTeammate();

  const sendCongrats=(a)=>{
    if(a.user_id==null) return;
    apiPost('/api/congrats',{
      receiver_user_id: a.user_id,
      activity_id:      _hashStr(a.ach),
      message:          'Congrats!',
    });
  };

  return h('div',{className:'page'},
    // greeting
    h('div',{className:'page-head fade-up fade-up-1'},
      h('h1',{className:'greeting'},`Good ${dayPart}, ${first}! 👋`),
      h('div',{className:'sub'},'Your progress and what your team has been up to.')),

    // tier + badges (full width)
    h(TierBadgesCard,{tiers, currentTier:E.currentTier, nextTier:E.nextTier, tierProgress:E.tierProgress, badges:E.badges}),

    // layout: [col1 recommended + col2 radar/streak paired] | [col3 continue/accomplishments]
    h('div',{className:'emp-grid fade-up fade-up-3'},

      // col1 + col2 share a flex group so their bottoms align
      h('div',{className:'emp-col-group'},
        // col 1 — recommended for you (with match %)
        // content is absolutely positioned so the card contributes zero intrinsic
        // height — Col 2 (radar+streak) drives the group height and the card
        // stretches to match it, scrolling internally when courses overflow.
        h(RecommendedCoursesCard,{recSource:TM.recSource, recommended:TM.recommended}),

        // col 2 — skill growth radar (fills full height now that streak moved to topbar)
        h(SkillGrowthCard,{
          skills:E.skills,
          compareUser:compare.compareUser,
          comparePicker:compare.comparePicker,
          teammates:compare.teammates,
          onTogglePicker:()=>compare.setComparePicker(o=>!o),
          onSelectCompare:compare.selectCompare,
          onClearCompare:()=>compare.setCompareUser(null),
        })
      ), // end emp-col-group

      // col 3 — continue learning + expandable team accomplishments
      h('div',{style:{flex:'1 0 0',minWidth:0,display:'flex',flexDirection:'column',gap:22}},
        h(ContinueLearningCard,{continueCourse:E.continueCourse}),
        // team accomplishments — clickable box that opens a scrollable popup
        h(AccomplishmentsBox,{count:TM.accomplishments.length, congratsReceived:E.congratsReceived, onOpen:()=>setAccsOpen(true)})
      ),

      // accomplishments popup
      h(AccomplishmentsModal,{open:accsOpen, accomplishments:TM.accomplishments, onClose:()=>setAccsOpen(false), onSendCongrats:sendCongrats})
    )
  );
}

Object.assign(window,{MyEmployee});
