/* ===================== Nova — fallback data ===================== */
(function(){

  const TIERS = [
    {key:'starter',  name:'Starter',  color:'#9aa2b1'},
    {key:'bronze',   name:'Bronze',   color:'#e08531'},
    {key:'silver',   name:'Silver',   color:'#9aa3af'},
    {key:'gold',     name:'Gold',     color:'#f5b71e'},
    {key:'diamond',  name:'Diamond',  color:'#2ACCFF'},
    {key:'platinum', name:'Platinum', color:'#A634FF'},
  ];

  // ----- Pradeep Menon — same person, two views -----
  const accounts = {
    employee: {
      id:    5575,
      name:  'Pradeep Menon',
      first: 'Pradeep',
      role:  'Learning & Development',
      email: 'pradeep.menon@orioninc.com',
      team:  'Nova',
      av:    ['#A634FF','#FF4398'],
      kind:  'employee',
    },
    manager: {
      id:    5575,
      name:  'Pradeep Menon',
      first: 'Pradeep',
      role:  'Learning & Development',
      email: 'pradeep.menon@orioninc.com',
      team:  'Nova',
      av:    ['#2ACCFF','#5400DC'],
      kind:  'manager',
    },
  };
  accounts.current = accounts.employee;

  // ===================== EMPLOYEE FALLBACK =====================
  // FALLBACK DATA — shown when Fabric is unreachable
  const employee = {
    currentTier:  'diamond',
    nextTier:     'platinum',
    tierProgress: 62,
    learningTime: '4h 20m',
    streak:       9,
    streakWeek:   [true,true,false,true,true,true,false],
    skills: {
      axes:      ['AI','Cloud','Frontend','Backend','Data'],
      thisMonth: [82,74,45,60,68],
      lastMonth: [70,65,40,55,60],
      delta:     10,
    },
    badges: [],
    continueCourse: {
      name:     'Generative AI for Leaders',
      cat:      'ai',
      status:   'In Progress',
      progress: 55,
      tile:     ['#A634FF','#5400DC'],
    },
    recommended: {
      name: 'AI Strategy and Governance',
      meta: 'Based on your recent AI coursework',
      tile: ['#2ACCFF','#5400DC'],
    },
  };

  // ===================== TEAM FALLBACK =====================
  // FALLBACK DATA — shown when Fabric is unreachable
  const team = {
    learningTime: '—',
    highlights: { congrats: 0, topCourse: '—', timeDelta: 0 },
    accomplishments: [
      {name:'Team Member',  verb:'completed', ach:'AI Foundations',         type:'course', time:'recently', av:['#2ACCFF','#A634FF']},
      {name:'Team Member',  verb:'completed', ach:'Cloud Fundamentals',     type:'course', time:'recently', av:['#A634FF','#FF4398']},
      {name:'Team Member',  verb:'reached',   ach:'Gold tier',              type:'gold',   time:'recently', av:['#F588FF','#A634FF']},
    ],
    recommended: [
      {name:'AI Foundations',          badge:'AI',    cls:'ai',    meta:'Popular on your team',  match:85, tile:['#2ACCFF','#5400DC'], glyph:'AI'},
      {name:'Generative AI for Leaders',badge:'AI',   cls:'ai',    meta:'Trending this quarter', match:80, tile:['#A634FF','#5400DC'], glyph:'✦'},
      {name:'Cloud Architecture',      badge:'Cloud', cls:'cloud', meta:'Taken by teammates',    match:72, tile:['#FF4398','#A634FF'], glyph:'☁'},
    ],
  };

  // ===================== MANAGER FALLBACK =====================
  // FALLBACK DATA — shown when Fabric is unreachable
  // manager.people is always derived dynamically from /api/manager/people
  // in production — never hardcode real names here
  const months = ['Jan','Feb','Mar','Apr','May','Jun'];
  const manager = {
    total:  10,
    goal:   'Every employee AI-proficient',
    target: 80,
    kpis: [
      {key:'prof',   num:'40%',  lab:'AI-proficient — <b>4 of 10</b>',   trend:'+0 pts', dir:'up',   ic:'spark',  tint:'rgba(166,52,255,.12)', col:'#A634FF'},
      {key:'active', num:'7',    lab:'Active learners <b>this week</b>',  trend:'+0%',    dir:'up',   ic:'users',  tint:'rgba(42,204,255,.14)', col:'#0f8fc4'},
      {key:'ret',    num:'—',    lab:'Learning <b>retention rate</b>',    trend:'—',      dir:'flat', ic:'shield', tint:'rgba(31,169,113,.14)', col:'#1FA971'},
      {key:'risk',   num:'2',    lab:'Employees <b>falling behind</b>',   trend:'—',      dir:'down', ic:'alert',  tint:'rgba(226,61,110,.12)', col:'#E23D6E'},
    ],
    months: months,
    series: {
      proficiency: [25,28,30,33,37,40],
      retention:   [0,0,0,0,0,0],
    },
    distribution: [
      {tier:'starter',  count:1},
      {tier:'bronze',   count:2},
      {tier:'silver',   count:3},
      {tier:'gold',     count:2},
      {tier:'diamond',  count:1},
      {tier:'platinum', count:1},
    ],
    teams: [
      {name:'Engineering',    members:4, prof:50, trend:'+0%', dir:'flat', status:'warn', col:'#A634FF'},
      {name:'Product',        members:3, prof:33, trend:'+0%', dir:'flat', status:'risk', col:'#2ACCFF'},
      {name:'Data & Analytics',members:3,prof:67, trend:'+0%', dir:'flat', status:'ok',   col:'#5400DC'},
    ],
    people: [
      {name:'Team Member 1',  role:'Engineering',     team:'Engineering',     tier:'diamond', prof:80, trend:'+0%', dir:'up',   status:'ok',   av:['#2ACCFF','#A634FF']},
      {name:'Team Member 2',  role:'Engineering',     team:'Engineering',     tier:'gold',    prof:65, trend:'+0%', dir:'flat', status:'warn', av:['#A634FF','#FF4398']},
      {name:'Team Member 3',  role:'Product',         team:'Product',         tier:'silver',  prof:45, trend:'+0%', dir:'flat', status:'warn', av:['#F588FF','#A634FF']},
      {name:'Team Member 4',  role:'Data & Analytics',team:'Data & Analytics',tier:'gold',    prof:70, trend:'+0%', dir:'up',   status:'ok',   av:['#5400DC','#2ACCFF']},
      {name:'Team Member 5',  role:'Engineering',     team:'Engineering',     tier:'bronze',  prof:30, trend:'-2%', dir:'down', status:'risk', av:['#FF6B88','#C21178']},
    ],
  };

  window.NOVA = { TIERS, accounts, employee, team, manager };
  // NOTE: api.js will overwrite NOVA.* with real Fabric data after fetching.
  // This fallback data is shown only when the API is unreachable.
})();
