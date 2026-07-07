/* ===================== Nova — static config ===================== */
/* No dummy/fallback data lives here. window.NOVA starts fully null and is
   populated exclusively by api.js after the real Fabric data is fetched.
   If data fails to load, the fields stay null and app.jsx shows the error
   screen — there is nothing to fall back to. */
(function(){

  const TIERS = [
    {key:'starter',  name:'Starter',  color:'#9aa2b1'},
    {key:'bronze',   name:'Bronze',   color:'#e08531'},
    {key:'silver',   name:'Silver',   color:'#9aa3af'},
    {key:'gold',     name:'Gold',     color:'#f5b71e'},
    {key:'diamond',  name:'Diamond',  color:'#2ACCFF'},
    {key:'platinum', name:'Platinum', color:'#A634FF'},
  ];

  // ── Static placeholder sections on the manager Overview ──────────────────
  // These two charts have NO real data source yet: there is no business-vertical
  // / specialization-track taxonomy anywhere in Classmate/Fabric. The numbers
  // below are the design-mockup placeholders, shown until a real API exists.
  // TODO: replace with a real API once a vertical/specialization taxonomy exists.
  const MANAGER_STATIC = {
    verticals: [
      {name:'Sports',     pct:92, earners:169, top:true},
      {name:'Telecom',    pct:84, earners:157},
      {name:'KPMG',       pct:82, earners:1013},
      {name:'Others',     pct:75, earners:23},
      {name:'Prof. Svcs', pct:68, earners:55},
      {name:'Corporate',  pct:67, earners:207},
      {name:'BFSI',       pct:64, earners:158},
    ],
    specialization: [
      {track:'AI Augmented Engineers',      pct:61, earners:1129, col:'#FF4398'},
      {track:'Platform & Reliability Eng.', pct:14, earners:259,  col:'#A634FF'},
      {track:'Domain Product Builders',     pct:13, earners:230,  col:'#2ACCFF'},
      {track:'Enabling & Architectural',    pct:12, earners:222,  col:'#C21178'},
    ],
  };

  window.NOVA = {
    TIERS,
    managerStatic: MANAGER_STATIC,
    accounts: {
      employee: null,
      manager:  null,
      current:  null,
      role:     null,
    },
    employee: null,
    team:     null,
    manager:  null,
  };
  // api.js overwrites NOVA.* with real Fabric data after fetching.
  // App renders only after every required field for the role is populated.
})();
