const { createRemoteJWKSet, createLocalJWKSet, jwtVerify } = require('jose');

const COOKIE_NAME = 'crowe-ide-session';

function createAuthModule({ supabaseUrl, fetchJWKS }) {
  const issuer = `${supabaseUrl}/auth/v1`;
  const audience = 'authenticated';

  let jwks;
  let jwksPromise;
  if (fetchJWKS) {
    // Test mode: use provided JWKS fetcher with createLocalJWKSet
    jwksPromise = fetchJWKS().then((jwksData) => createLocalJWKSet(jwksData));
  } else {
    // Production: fetch JWKS from Supabase
    jwks = createRemoteJWKSet(
      new URL(`${supabaseUrl}/auth/v1/.well-known/jwks.json`)
    );
  }

  async function validateToken(token) {
    if (!token) throw new Error('No token provided');
    const getKey = jwks || (await jwksPromise);
    const { payload } = await jwtVerify(token, getKey, {
      issuer,
      audience,
    });
    return {
      userId: payload.sub,
      role: payload.role || 'subscriber',
    };
  }

  function extractToken(req) {
    if (req.query && req.query.token) return req.query.token;
    if (req.cookies && req.cookies[COOKIE_NAME]) return req.cookies[COOKIE_NAME];
    return null;
  }

  return { validateToken, extractToken, COOKIE_NAME };
}

module.exports = { createAuthModule, COOKIE_NAME };
