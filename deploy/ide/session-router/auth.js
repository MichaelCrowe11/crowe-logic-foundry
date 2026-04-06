// auth.js — JWT handling for the Crowe Logic IDE session router.
//
// The router uses a single shared HS256 secret with two distinct token kinds,
// distinguished by issuer/audience claims so they cannot be confused:
//
//   1. Handoff token (issued by the launcher in crowe-logic-ai)
//      - alg: HS256, iss: crowe-logic-ai, aud: crowe-ide-router
//      - Lifetime: 60s
//      - Arrives as a query string parameter on the IDE host
//      - Validated once, then exchanged for a session token
//
//   2. Session token (issued by this router after validating a handoff)
//      - alg: HS256, iss: crowe-ide-router, aud: crowe-ide-router
//      - Lifetime: SESSION_TTL_SECONDS (24h)
//      - Stored in an httpOnly cookie on the IDE host
//      - Re-validated on every subsequent request and on each WS upgrade
//
// The same secret is used to mint and verify both kinds. The launcher must
// sign with the exact same IDE_JWT_SECRET; mismatched secrets cause every
// handoff to 401.

const { SignJWT, jwtVerify } = require('jose');

const COOKIE_NAME = 'crowe-ide-session';

const HANDOFF_ISSUER = 'crowe-logic-ai';
const HANDOFF_AUDIENCE = 'crowe-ide-router';
const HANDOFF_MAX_AGE = '5m'; // generous skew tolerance, well above the 60s exp

const SESSION_ISSUER = 'crowe-ide-router';
const SESSION_AUDIENCE = 'crowe-ide-router';
const SESSION_TTL_SECONDS = 60 * 60 * 24; // 24 hours

function createAuthModule({ ideJwtSecret }) {
  if (typeof ideJwtSecret !== 'string' || ideJwtSecret.length < 32) {
    throw new Error(
      'ideJwtSecret must be a string of at least 32 characters (use `openssl rand -hex 32`)'
    );
  }
  // Encode the secret the same way the launcher does (`new TextEncoder().encode(secret)`),
  // so the HMAC computation matches byte-for-byte.
  const secretKey = new TextEncoder().encode(ideJwtSecret);

  async function validateHandoffToken(token) {
    if (!token) throw new Error('No token provided');
    const { payload } = await jwtVerify(token, secretKey, {
      issuer: HANDOFF_ISSUER,
      audience: HANDOFF_AUDIENCE,
      algorithms: ['HS256'],
      maxTokenAge: HANDOFF_MAX_AGE,
    });
    if (typeof payload.sub !== 'string' || payload.sub.length === 0) {
      throw new Error('Handoff token missing sub claim');
    }
    return {
      userId: payload.sub,
      role: payload.role === 'admin' ? 'admin' : 'subscriber',
      email: typeof payload.email === 'string' ? payload.email : null,
    };
  }

  async function mintSessionToken({ userId, role, email }) {
    if (typeof userId !== 'string' || userId.length === 0) {
      throw new Error('mintSessionToken: userId is required');
    }
    const claims = { role: role === 'admin' ? 'admin' : 'subscriber' };
    if (typeof email === 'string' && email.length > 0) {
      claims.email = email;
    }
    return new SignJWT(claims)
      .setProtectedHeader({ alg: 'HS256' })
      .setSubject(userId)
      .setIssuer(SESSION_ISSUER)
      .setAudience(SESSION_AUDIENCE)
      .setIssuedAt()
      .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
      .sign(secretKey);
  }

  async function validateSessionToken(token) {
    if (!token) throw new Error('No token provided');
    const { payload } = await jwtVerify(token, secretKey, {
      issuer: SESSION_ISSUER,
      audience: SESSION_AUDIENCE,
      algorithms: ['HS256'],
    });
    if (typeof payload.sub !== 'string' || payload.sub.length === 0) {
      throw new Error('Session token missing sub claim');
    }
    return {
      userId: payload.sub,
      role: payload.role === 'admin' ? 'admin' : 'subscriber',
    };
  }

  function extractToken(req) {
    const authHeader = req.headers && req.headers.authorization;
    if (authHeader && authHeader.startsWith('Bearer ')) {
      return authHeader.slice(7);
    }
    if (req.query && req.query.token) return req.query.token;
    if (req.cookies && req.cookies[COOKIE_NAME]) return req.cookies[COOKIE_NAME];
    return null;
  }

  return {
    validateHandoffToken,
    mintSessionToken,
    validateSessionToken,
    extractToken,
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
  };
}

module.exports = {
  createAuthModule,
  COOKIE_NAME,
  SESSION_TTL_SECONDS,
};
