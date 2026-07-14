/* ===================== Nova — static config ===================== */
/* No dummy/fallback data lives here. window.NOVA starts fully null and is
   populated exclusively by api.js after the real Fabric data is fetched.
   If data fails to load, the fields stay null and app.jsx shows the error
   screen — there is nothing to fall back to. */
(function(){

  const TIERS = [
    {key:'starter',  name:'Starter',  color:'#9aa2b1'},
    {key:'bronze',   name:'Bronze',   color:'#e08531'},
    {key:'silver',   name:'Silver',   color:'#7EC8E3'},
    {key:'gold',     name:'Gold',     color:'#f5b71e'},
    {key:'diamond',  name:'Diamond',  color:'#4632d4'},
    {key:'platinum', name:'Platinum', color:'#A634FF'},
  ];

  // ── Static placeholder sections on the manager Overview ──────────────────
  // The Specialization Landscape chart has NO real data source yet: there is no
  // specialization-track taxonomy anywhere in Classmate/Fabric. The numbers
  // below are the design-mockup placeholders, shown until a real API exists.
  // TODO: replace with a real API once a specialization taxonomy exists.
  const MANAGER_STATIC = {
    specialization: [
      {track:'AI Augmented Engineers',      pct:61, earners:1129, col:'#FF4398'},
      {track:'Platform & Reliability Eng.', pct:14, earners:259,  col:'#A634FF'},
      {track:'Domain Product Builders',     pct:13, earners:230,  col:'#2ACCFF'},
      {track:'Enabling & Architectural',    pct:12, earners:222,  col:'#C21178'},
    ],
  };

  // Exec dev/sign-in IDs allowed to use the "view as" impersonation panel.
  // Sourced from a runtime global (set only by the gitignored dev-config.js in
  // local dev) so no privileged IDs are hardcoded in shipped source; empty in
  // production, which makes the dev sign-in / impersonation panel inert.
  const DEV_USER_IDS = new Set(window.NOVA_DEV_IDS || []);

  window.NOVA = {
    TIERS,
    DEV_USER_IDS,
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
