const STORAGE_KEY = 'vivintoneContributorSession';

function normalizeConfig(value) {
  const clientId = value.contributorClientId || value.portalClientId;
  const region = value.contributorRegion || value.region;
  if (!clientId || !region || String(clientId).startsWith('__') || String(region).startsWith('__')) {
    throw new Error('Contributor sign-in is not configured yet.');
  }
  return { clientId, region, contributorSmsEnabled: value.contributorSmsEnabled === true };
}

export async function loadAuthConfig() {
  const response = await fetch('/config.json', { cache: 'no-store' });
  if (!response.ok) throw new Error('Contributor sign-in is temporarily unavailable.');
  return normalizeConfig(await response.json());
}

async function cognito(config, operation, body) {
  const response = await fetch(`https://cognito-idp.${config.region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'content-type': 'application/x-amz-json-1.1',
      'x-amz-target': `AWSCognitoIdentityProviderService.${operation}`,
    },
    body: JSON.stringify({ ClientId: config.clientId, ...body }),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error('We could not complete sign-in. Check the code or request a new one.');
    error.code = String(data.__type || data.code || '').split('#').pop();
    throw error;
  }
  return data;
}

export function normalizeIdentifier(raw) {
  const value = raw.trim();
  if (value.includes('@')) {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) throw new Error('Enter a valid email address.');
    return { username: value.toLowerCase(), type: 'email', attribute: { Name: 'email', Value: value.toLowerCase() } };
  }
  const phone = value.replace(/[()\s.-]/g, '');
  if (!/^\+[1-9]\d{7,14}$/.test(phone)) throw new Error('Enter a valid email or a phone number with country code, such as +1 555 123 4567.');
  return { username: phone, type: 'phone', attribute: { Name: 'phone_number', Value: phone } };
}

export async function beginSignIn(config, rawIdentifier, options = {}) {
  const identity = normalizeIdentifier(rawIdentifier);
  try {
    const auth = await cognito(config, 'InitiateAuth', {
      AuthFlow: 'USER_AUTH',
      AuthParameters: { USERNAME: identity.username, PREFERRED_CHALLENGE: identity.type === 'email' ? 'EMAIL_OTP' : 'SMS_OTP' },
    });
    if (auth.AuthenticationResult) return { authenticated: saveAuthentication(auth.AuthenticationResult), identity };
    return { identity, session: auth.Session, challenge: auth.ChallengeName || (identity.type === 'email' ? 'EMAIL_OTP' : 'SMS_OTP'), flow: 'existing' };
  } catch (error) {
    if (error.code === 'UserNotConfirmedException') {
      await cognito(config, 'ResendConfirmationCode', { Username: identity.username });
      return { identity, challenge: identity.type === 'email' ? 'EMAIL_OTP' : 'SMS_OTP', flow: 'signup' };
    }
    if (!['UserNotFoundException', 'NotAuthorizedException'].includes(error.code)) throw error;
    try {
      const verificationIntentToken = options.createVerificationIntent ? await options.createVerificationIntent(identity) : '';
      const signup = await cognito(config, 'SignUp', {
        Username: identity.username,
        UserAttributes: [identity.attribute],
        ...(verificationIntentToken ? { ClientMetadata: { verificationIntentToken } } : {}),
      });
      return { identity, session: signup.Session, challenge: identity.type === 'email' ? 'EMAIL_OTP' : 'SMS_OTP', flow: 'signup' };
    } catch (signupError) {
      // Never disclose whether an identifier already has an account.
      if (signupError.code === 'UsernameExistsException') {
        const auth = await cognito(config, 'InitiateAuth', {
          AuthFlow: 'USER_AUTH',
          AuthParameters: { USERNAME: identity.username, PREFERRED_CHALLENGE: identity.type === 'email' ? 'EMAIL_OTP' : 'SMS_OTP' },
        });
        return { identity, session: auth.Session, challenge: auth.ChallengeName, flow: 'existing' };
      }
      throw signupError;
    }
  }
}

export async function finishSignIn(config, attempt, code) {
  const cleanCode = code.replace(/\D/g, '');
  if (cleanCode.length !== 6) throw new Error('Enter the complete 6-digit code.');
  if (attempt.flow === 'signup') {
    const confirmed = await cognito(config, 'ConfirmSignUp', { Username: attempt.identity.username, ConfirmationCode: cleanCode, Session: attempt.session });
    const auth = await cognito(config, 'InitiateAuth', {
      AuthFlow: 'USER_AUTH',
      AuthParameters: { USERNAME: attempt.identity.username },
      Session: confirmed.Session,
    });
    if (!auth.AuthenticationResult) throw new Error('Your code was accepted. Please request a fresh code to finish signing in.');
    return saveAuthentication(auth.AuthenticationResult);
  }
  const codeKey = attempt.challenge === 'SMS_OTP' ? 'SMS_OTP_CODE' : 'EMAIL_OTP_CODE';
  const auth = await cognito(config, 'RespondToAuthChallenge', {
    ChallengeName: attempt.challenge,
    ChallengeResponses: { USERNAME: attempt.identity.username, [codeKey]: cleanCode },
    Session: attempt.session,
  });
  if (!auth.AuthenticationResult) throw new Error('We could not finish sign-in. Request a new code and try again.');
  return saveAuthentication(auth.AuthenticationResult);
}

export async function resendSignInCode(config, attempt) {
  if (attempt.flow === 'signup') {
    await cognito(config, 'ResendConfirmationCode', { Username: attempt.identity.username });
    return attempt;
  }
  return beginSignIn(config, attempt.identity.username);
}

function jwtPayload(token) {
  try {
    const value = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(atob(value).split('').map(char => `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`).join('')));
  } catch { return {}; }
}

export function sessionIdentity(session = getStoredSession()) {
  const claims = jwtPayload(session?.idToken || '');
  if (claims.email && String(claims.email_verified) === 'true') return { type: 'email', identity: String(claims.email).toLowerCase() };
  if (claims.phone_number && String(claims.phone_number_verified) === 'true') return { type: 'phone', identity: String(claims.phone_number) };
  return null;
}

function saveAuthentication(result, previous = {}) {
  const session = {
    accessToken: result.AccessToken,
    idToken: result.IdToken,
    refreshToken: result.RefreshToken || previous.refreshToken,
    expiresAt: jwtPayload(result.AccessToken).exp * 1000,
  };
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  return session;
}

export function getStoredSession() {
  try {
    const session = JSON.parse(sessionStorage.getItem(STORAGE_KEY));
    return session?.accessToken && session?.idToken && session?.expiresAt ? session : null;
  } catch { return null; }
}

export function clearSession() { sessionStorage.removeItem(STORAGE_KEY); }

export async function refreshSession(config, session = getStoredSession()) {
  if (!session?.refreshToken) return null;
  try {
    const data = await cognito(config, 'InitiateAuth', { AuthFlow: 'REFRESH_TOKEN_AUTH', AuthParameters: { REFRESH_TOKEN: session.refreshToken } });
    return saveAuthentication(data.AuthenticationResult, session);
  } catch { clearSession(); return null; }
}

export function sessionMinutes(session) { return Math.max(0, Math.ceil((session.expiresAt - Date.now()) / 60000)); }
