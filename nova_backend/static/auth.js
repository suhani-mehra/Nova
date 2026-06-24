let _msalInstance = null;
let _msalInitPromise = null;
// Note: Using standard OIDC scopes for dev. Production should use: ["api://394a1256-a1c9-4238-b06d-2680a3ac0798/user_impersonation"]
// (requires "Expose an API" to be configured in Azure AD app registration)
const _API_SCOPE = ["openid", "profile", "email"];

async function _initMsal() {
  if (_msalInstance) return;
  if (typeof msal === 'undefined') return;

  const _MSAL_CONFIG = {
    auth: {
      clientId: "394a1256-a1c9-4238-b06d-2680a3ac0798",
      authority: "https://login.microsoftonline.com/6594668a-830a-4f34-a950-07a1ab1b2b6b",
      redirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  };
  _msalInstance = new msal.PublicClientApplication(_MSAL_CONFIG);
  await _msalInstance.initialize();
}

async function novaSignIn() {
  try {
    if (!_msalInitPromise) {
      _msalInitPromise = _initMsal();
    }
    await _msalInitPromise;
    if (!_msalInstance) {
      console.error('[Nova Auth] MSAL not initialized');
      return;
    }
    console.log('[Nova Auth] Starting sign-in...');
    await _msalInstance.loginPopup({ scopes: _API_SCOPE });
    console.log('[Nova Auth] Sign-in successful, reloading...');
    window.location.reload();
  } catch (err) {
    console.error('[Nova Auth] Sign-in failed:', err);
  }
}

async function novaGetToken() {
  if (!_msalInstance) {
    if (!_msalInitPromise) {
      _msalInitPromise = _initMsal();
    }
    await _msalInitPromise;
  }
  if (!_msalInstance) return null;
  const accounts = _msalInstance.getAllAccounts();
  if (!accounts.length) return null;
  try {
    const r = await _msalInstance.acquireTokenSilent({ scopes: _API_SCOPE, account: accounts[0] });
    return r.accessToken;
  } catch {
    const r = await _msalInstance.acquireTokenPopup({ scopes: _API_SCOPE });
    return r.accessToken;
  }
}

function novaGetAccount() {
  if (!_msalInstance) return null;
  const accounts = _msalInstance.getAllAccounts();
  return accounts.length ? accounts[0] : null;
}

function novaSignOut() {
  if (_msalInstance) {
    _msalInstance.logoutPopup();
  }
}

Object.assign(window, { novaSignIn, novaGetToken, novaGetAccount, novaSignOut });
