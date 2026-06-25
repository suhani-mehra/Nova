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

  window.NOVA = {
    TIERS,
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
