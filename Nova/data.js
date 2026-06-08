/* ===================== Nova — dummy data ===================== */
window.NOVA = (function(){

  const TIERS = [
    {key:'starter',  name:'Starter',  color:'#9aa2b1'},
    {key:'bronze',   name:'Bronze',   color:'#e08531'},
    {key:'silver',   name:'Silver',   color:'#9aa3af'},
    {key:'gold',     name:'Gold',     color:'#f5b71e'},
    {key:'diamond',  name:'Diamond',  color:'#2ACCFF'},
    {key:'platinum', name:'Platinum', color:'#A634FF'},
  ];

  // ----- logged-in accounts (role decided by the "database") -----
  const accounts = {
    employee:{
      id:'alex', name:'Alex Morgan', first:'Alex', role:'Product Designer',
      email:'alex.morgan@orioninc.com', team:'Product Design', av:['#A634FF','#FF4398'],
      kind:'employee',
    },
    manager:{
      id:'jordan', name:'Jordan Reyes', first:'Jordan', role:'Director, Learning & Talent',
      email:'jordan.reyes@orioninc.com', team:'People & Capability', av:['#2ACCFF','#5400DC'],
      kind:'manager',
    },
  };

  // ===================== EMPLOYEE =====================
  const employee = {
    currentTier:'gold',
    nextTier:'diamond',
    tierProgress:78,
    learningTime:'6h 45m',
    streak:7,
    streakWeek:[true,true,true,true,true,true,true], // Mon..Sun
    skills:{
      axes:['AI','Frontend','Backend','Cloud','Data'],
      thisMonth:[78,88,70,64,72],
      lastMonth:[58,82,66,52,60],
      delta:12,
    },
    badges:[
      {color:'#EC12F0', glyph:'crown',   count:2},
      {color:'#2ACCFF', glyph:'diamond', count:1},
      {color:'#F7B100', glyph:'star',    count:3},
      {color:'#8C8C96', glyph:'star',    count:1},
      {color:'#e08531', glyph:'star',    count:2},
    ],
    continueCourse:{name:'React Advanced Patterns', cat:'frontend', status:'In Progress', progress:60, tile:['#A634FF','#5400DC']},
    recommended:{name:'AI Foundations', meta:'Based on your recent interests', tile:['#2ACCFF','#5400DC']},
  };

  // ===================== TEAM (for employee → My Team) =====================
  const team = {
    learningTime:'24h 10m',
    highlights:{congrats:28, topCourse:'AI Foundations', timeDelta:18},
    accomplishments:[
      {name:'Maria Santos', verb:'completed', ach:'AI Foundations',          type:'course', time:'2h ago',  av:['#FF4398','#A634FF']},
      {name:'James Lee',    verb:'reached',   ach:'Diamond tier',            type:'diamond',time:'5h ago',  av:['#2ACCFF','#5400DC']},
      {name:'Priya Patel',  verb:'hit a',     ach:'7-day streak',            type:'streak', time:'1d ago',  av:['#A634FF','#FF6B88']},
      {name:'Tyler Nguyen', verb:'completed', ach:'React Advanced Patterns', type:'course', time:'1d ago',  av:['#5400DC','#2ACCFF']},
      {name:'Sophie Clark', verb:'reached',   ach:'Gold tier',               type:'gold',   time:'2d ago',  av:['#F588FF','#A634FF']},
    ],
    recommended:[
      {name:'AI Foundations',         badge:'AI',       cls:'ai',       meta:'Completed by 65% of your team', match:85, tile:['#2ACCFF','#5400DC'], glyph:'AI'},
      {name:'React Advanced Patterns',badge:'Frontend', cls:'frontend', meta:'Taken by 8 teammates',          match:78, tile:['#A634FF','#5400DC'], glyph:'⚛'},
      {name:'Prompt Engineering',     badge:'AI',       cls:'ai',       meta:'Completed by 60% of your team', match:72, tile:['#FF4398','#A634FF'], glyph:'✦'},
    ],
  };

  // ===================== MANAGER =====================
  const TOTAL = 5400;
  const months = ['Jul','Aug','Sep','Oct','Nov','Dec','Jan','Feb','Mar','Apr','May','Jun'];

  const manager = {
    total:TOTAL,
    goal:'Every employee AI-proficient',
    target:80, // % target by year end
    // headline KPIs
    kpis:[
      {key:'prof', num:'57%',   lab:'AI-proficient — <b>3,078 of 5,400</b>', trend:'+9 pts', dir:'up',   ic:'spark', tint:'rgba(166,52,255,.12)', col:'#A634FF'},
      {key:'active',num:'4,612',lab:'Active learners <b>this week</b>',      trend:'+6%',   dir:'up',   ic:'users', tint:'rgba(42,204,255,.14)', col:'#0f8fc4'},
      {key:'ret',  num:'91%',   lab:'Learning <b>retention rate</b>',        trend:'+2 pts',dir:'up',   ic:'shield',tint:'rgba(31,169,113,.14)',col:'#1FA971'},
      {key:'risk', num:'612',   lab:'Employees <b>falling behind</b>',       trend:'-4%',   dir:'up',   ic:'alert', tint:'rgba(226,61,110,.12)', col:'#E23D6E'},
    ],
    // line chart — progress toward AI proficiency + retention
    months:months,
    series:{
      proficiency:[31,34,37,40,43,45,47,49,51,53,55,57], // % AI-proficient
      retention:  [85,86,86,88,88,89,90,89,90,92,91,91], // % retention
    },
    // tier distribution across all employees (sums to TOTAL)
    distribution:[
      {tier:'starter', count:760},
      {tier:'bronze',  count:1180},
      {tier:'silver',  count:1420},
      {tier:'gold',    count:1080},
      {tier:'diamond', count:640},
      {tier:'platinum',count:320},
    ],
    // per-team learning overview
    teams:[
      {name:'Platform Engineering', members:420, prof:74, trend:'+8%',  dir:'up',   status:'ok',   col:'#A634FF'},
      {name:'Data Science',         members:260, prof:81, trend:'+11%', dir:'up',   status:'ok',   col:'#2ACCFF'},
      {name:'Product Design',       members:180, prof:69, trend:'+6%',  dir:'up',   status:'ok',   col:'#5400DC'},
      {name:'Cloud Infrastructure', members:340, prof:63, trend:'+4%',  dir:'up',   status:'warn', col:'#FF4398'},
      {name:'Frontend Guild',       members:300, prof:58, trend:'+2%',  dir:'flat', status:'warn', col:'#F588FF'},
      {name:'Sales Enablement',     members:520, prof:41, trend:'-3%',  dir:'down', status:'risk', col:'#C21178'},
      {name:'Customer Success',     members:610, prof:38, trend:'-5%',  dir:'down', status:'risk', col:'#e08531'},
      {name:'Marketing',            members:240, prof:52, trend:'+1%',  dir:'flat', status:'warn', col:'#FF6B88'},
    ],
    // individual employees — who is progressing / who is not
    people:[
      {name:'Maria Santos',  role:'ML Engineer',        team:'Data Science',     tier:'diamond', prof:94, trend:'+7%',  dir:'up',   status:'ok',   av:['#FF4398','#A634FF']},
      {name:'David Okafor',  role:'Backend Engineer',   team:'Platform Eng',     tier:'platinum',prof:97, trend:'+4%',  dir:'up',   status:'ok',   av:['#5400DC','#2ACCFF']},
      {name:'Priya Patel',   role:'Product Designer',   team:'Product Design',   tier:'gold',    prof:82, trend:'+9%',  dir:'up',   status:'ok',   av:['#A634FF','#FF6B88']},
      {name:'James Lee',     role:'Frontend Engineer',  team:'Frontend Guild',   tier:'gold',    prof:76, trend:'+5%',  dir:'up',   status:'ok',   av:['#2ACCFF','#5400DC']},
      {name:'Sophie Clark',  role:'Data Analyst',       team:'Data Science',     tier:'silver',  prof:64, trend:'+2%',  dir:'flat', status:'warn', av:['#F588FF','#A634FF']},
      {name:'Tyler Nguyen',  role:'Solutions Architect',team:'Cloud Infra',      tier:'silver',  prof:59, trend:'+1%',  dir:'flat', status:'warn', av:['#5400DC','#FF4398']},
      {name:'Hannah Brooks', role:'Account Executive',  team:'Sales Enablement', tier:'bronze',  prof:38, trend:'-2%',  dir:'down', status:'risk', av:['#FF6B88','#C21178']},
      {name:'Marcus Webb',   role:'Support Lead',       team:'Customer Success', tier:'bronze',  prof:31, trend:'-6%',  dir:'down', status:'risk', av:['#e08531','#FF4398']},
      {name:'Elena Rossi',   role:'Content Strategist', team:'Marketing',        tier:'starter', prof:22, trend:'-4%',  dir:'down', status:'risk', av:['#C21178','#A634FF']},
      {name:'Omar Haddad',   role:'Sales Engineer',     team:'Sales Enablement', tier:'starter', prof:18, trend:'-1%',  dir:'down', status:'risk', av:['#A634FF','#5400DC']},
    ],
  };

  return {TIERS, accounts, employee, team, manager};
})();
