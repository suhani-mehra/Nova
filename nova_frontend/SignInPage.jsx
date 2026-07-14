/* ===================== Nova — Sign-in page =====================
 *
 * Props (when rendered):
 *   onSignIn    – click handler; wire to msalInstance.loginRedirect() when SSO is ready
 *   onDevSignIn – dev mode sign-in handler (uid) => void
 *   contactHref – href for the "Contact your admin" link (default "#")
 */

function SignInPage({ onSignIn, onDevSignIn, contactHref = "#" }) {
  const [devMode, setDevMode] = React.useState(false);
  const [devId, setDevId] = React.useState('');
  const [devPass, setDevPass] = React.useState('');
  const [devError, setDevError] = React.useState('');

  const handleDevSubmit = () => {
    const id = parseInt(devId, 10);
    // Dev password comes from the local-only dev-config.js global; when it is
    // undefined (production, where that file is not shipped) dev sign-in is inert.
    if (!window.NOVA_DEV_PASSWORD || !NOVA.DEV_USER_IDS.has(id) || devPass !== window.NOVA_DEV_PASSWORD) {
      setDevError('Invalid credentials');
      return;
    }
    if (onDevSignIn) onDevSignIn(id);
  };
  return (
    <div className="nova-signin">
      <div className="nova-signin__card">

        {/* Left: brand + value proposition */}
        <section className="nova-signin__brand">
          <header className="nova-signin__logo">
            <span className="nova-signin__logo-oi" aria-hidden="true">
              <_OILogo />
            </span>
          </header>

          <h1 className="nova-signin__headline">
            Track your tier, build daily streaks, and unlock recommended
            learning paths.
          </h1>
        </section>

        {/* Right: sign-in action */}
        <section className="nova-signin__panel">
          <h2 className="nova-signin__title">Welcome back</h2>
          <p className="nova-signin__subtitle">
            Sign in with your Orion work account to continue.
          </p>

          <button
            type="button"
            className="nova-signin__button"
            onClick={onSignIn}
          >
            <_MicrosoftLogo />
            <span>Sign in with Microsoft</span>
          </button>

          <p className="nova-signin__secured">
            <_ShieldIcon />
            <span>Secured by Azure AD single sign-on</span>
          </p>

          {!devMode ? (
            <button
              type="button"
              className="nova-signin__dev-toggle"
              onClick={() => setDevMode(true)}
            >
              Dev mode
            </button>
          ) : (
            <div className="nova-signin__dev-form">
              <input
                type="number"
                placeholder="User ID"
                value={devId}
                onChange={e => setDevId(e.target.value)}
                className="nova-signin__dev-input"
              />
              <input
                type="password"
                placeholder="Password"
                value={devPass}
                onChange={e => setDevPass(e.target.value)}
                className="nova-signin__dev-input"
              />
              {devError && <p className="nova-signin__dev-error">{devError}</p>}
              <div className="nova-signin__dev-actions">
                <button type="button" className="nova-signin__dev-submit" onClick={handleDevSubmit}>Enter</button>
                <button type="button" className="nova-signin__dev-cancel" onClick={() => { setDevMode(false); setDevError(''); }}>Cancel</button>
              </div>
            </div>
          )}

          <footer className="nova-signin__footer">
            <span>Trouble signing in? </span>
            <a className="nova-signin__link" href={contactHref}>
              Contact your admin
            </a>
          </footer>
        </section>

      </div>
    </div>
  );
}

/* ---- Private icons (prefixed _ so they don't pollute the global namespace) ---- */

function _OILogo() {
  return <img className="nova-signin__oi-img" src="/logo-white.svg" alt="OI Polaris" />;
}

function _MicrosoftLogo() {
  return (
    <svg width="26" height="26" viewBox="0 0 23 23" aria-hidden="true">
      <rect x="1"  y="1"  width="10" height="10" fill="#f25022" />
      <rect x="12" y="1"  width="10" height="10" fill="#7fba00" />
      <rect x="1"  y="12" width="10" height="10" fill="#00a4ef" />
      <rect x="12" y="12" width="10" height="10" fill="#ffb900" />
    </svg>
  );
}

function _ShieldIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 2.5l7 3v5.5c0 4.4-3 8.2-7 9.5-4-1.3-7-5.1-7-9.5V5.5l7-3z"
        fill="#9b59c9"
      />
      <path
        d="M9.3 12.2l1.9 1.9 3.5-3.7"
        stroke="#ffffff"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

Object.assign(window, { SignInPage });
