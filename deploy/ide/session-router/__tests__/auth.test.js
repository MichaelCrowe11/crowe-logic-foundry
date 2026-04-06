const { createAuthModule } = require('../auth');
const { SignJWT, jwtVerify } = require('jose');

const TEST_SECRET = 'a'.repeat(64); // 64-char hex-shaped string, satisfies length check
const WRONG_SECRET = 'b'.repeat(64);

let auth;

beforeEach(() => {
  auth = createAuthModule({ ideJwtSecret: TEST_SECRET });
});

async function makeHandoffJWT(claims = {}, { secret = TEST_SECRET, expiresIn = '60s', issuer = 'crowe-logic-ai', audience = 'crowe-ide-router', alg = 'HS256' } = {}) {
  const key = new TextEncoder().encode(secret);
  return new SignJWT({ role: 'admin', email: 'test@example.com', ...claims })
    .setProtectedHeader({ alg })
    .setSubject(claims.sub || 'user-123')
    .setIssuer(issuer)
    .setAudience(audience)
    .setIssuedAt()
    .setExpirationTime(expiresIn)
    .sign(key);
}

describe('createAuthModule', () => {
  test('throws when ideJwtSecret is missing', () => {
    expect(() => createAuthModule({})).toThrow(/ideJwtSecret/);
  });

  test('throws when ideJwtSecret is too short', () => {
    expect(() => createAuthModule({ ideJwtSecret: 'too-short' })).toThrow(/at least 32/);
  });
});

describe('validateHandoffToken', () => {
  test('returns user data for a valid handoff JWT', async () => {
    const token = await makeHandoffJWT({ sub: 'user-123', role: 'admin', email: 'a@b.com' });
    const result = await auth.validateHandoffToken(token);
    expect(result).toEqual({
      userId: 'user-123',
      role: 'admin',
      email: 'a@b.com',
    });
  });

  test('coerces unknown role to subscriber', async () => {
    const token = await makeHandoffJWT({ sub: 'user-9', role: 'evil-overlord' });
    const result = await auth.validateHandoffToken(token);
    expect(result.role).toBe('subscriber');
  });

  test('returns null email when claim is missing', async () => {
    const token = await new SignJWT({ role: 'subscriber' })
      .setProtectedHeader({ alg: 'HS256' })
      .setSubject('user-no-email')
      .setIssuer('crowe-logic-ai')
      .setAudience('crowe-ide-router')
      .setIssuedAt()
      .setExpirationTime('60s')
      .sign(new TextEncoder().encode(TEST_SECRET));
    const result = await auth.validateHandoffToken(token);
    expect(result.email).toBeNull();
  });

  test('rejects expired handoff token', async () => {
    const token = await makeHandoffJWT({}, { expiresIn: '-1s' });
    await expect(auth.validateHandoffToken(token)).rejects.toThrow();
  });

  test('rejects handoff token with wrong issuer', async () => {
    const token = await makeHandoffJWT({}, { issuer: 'evil.example.com' });
    await expect(auth.validateHandoffToken(token)).rejects.toThrow();
  });

  test('rejects handoff token with wrong audience', async () => {
    const token = await makeHandoffJWT({}, { audience: 'somebody-else' });
    await expect(auth.validateHandoffToken(token)).rejects.toThrow();
  });

  test('rejects handoff token signed with wrong secret', async () => {
    const token = await makeHandoffJWT({}, { secret: WRONG_SECRET });
    await expect(auth.validateHandoffToken(token)).rejects.toThrow();
  });

  test('rejects malformed token string', async () => {
    await expect(auth.validateHandoffToken('not-a-jwt')).rejects.toThrow();
  });

  test('rejects empty string', async () => {
    await expect(auth.validateHandoffToken('')).rejects.toThrow();
  });

  test('rejects token without sub claim', async () => {
    const token = await new SignJWT({ role: 'admin' })
      .setProtectedHeader({ alg: 'HS256' })
      .setIssuer('crowe-logic-ai')
      .setAudience('crowe-ide-router')
      .setIssuedAt()
      .setExpirationTime('60s')
      .sign(new TextEncoder().encode(TEST_SECRET));
    await expect(auth.validateHandoffToken(token)).rejects.toThrow();
  });

  test('rejects a session token presented as a handoff token (mixed-up kinds)', async () => {
    // Session tokens use different iss/aud and must not validate as handoffs.
    const sessionToken = await auth.mintSessionToken({ userId: 'user-123', role: 'admin' });
    await expect(auth.validateHandoffToken(sessionToken)).rejects.toThrow();
  });
});

describe('mintSessionToken + validateSessionToken', () => {
  test('round-trips user claims', async () => {
    const token = await auth.mintSessionToken({
      userId: 'user-abc',
      role: 'admin',
      email: 'me@x.com',
    });
    const result = await auth.validateSessionToken(token);
    expect(result).toEqual({ userId: 'user-abc', role: 'admin' });
  });

  test('coerces unknown role to subscriber on mint', async () => {
    const token = await auth.mintSessionToken({ userId: 'user-1', role: 'wat' });
    const result = await auth.validateSessionToken(token);
    expect(result.role).toBe('subscriber');
  });

  test('mintSessionToken throws when userId is missing', async () => {
    await expect(auth.mintSessionToken({ role: 'admin' })).rejects.toThrow();
  });

  test('session tokens have a long expiry (~24h)', async () => {
    const token = await auth.mintSessionToken({ userId: 'user-1', role: 'admin' });
    const { payload } = await jwtVerify(token, new TextEncoder().encode(TEST_SECRET), {
      issuer: 'crowe-ide-router',
      audience: 'crowe-ide-router',
    });
    const ttl = payload.exp - payload.iat;
    expect(ttl).toBe(60 * 60 * 24);
  });

  test('rejects session token with wrong secret', async () => {
    const otherAuth = createAuthModule({ ideJwtSecret: WRONG_SECRET });
    const token = await otherAuth.mintSessionToken({ userId: 'user-x', role: 'admin' });
    await expect(auth.validateSessionToken(token)).rejects.toThrow();
  });

  test('rejects a handoff token presented as a session token', async () => {
    const handoff = await makeHandoffJWT({ sub: 'user-1' });
    await expect(auth.validateSessionToken(handoff)).rejects.toThrow();
  });
});

describe('extractToken', () => {
  test('extracts token from query parameter', () => {
    const req = { query: { token: 'abc123' }, cookies: {} };
    expect(auth.extractToken(req)).toBe('abc123');
  });

  test('extracts token from cookie', () => {
    const req = { query: {}, cookies: { 'crowe-ide-session': 'def456' } };
    expect(auth.extractToken(req)).toBe('def456');
  });

  test('returns null when no token present', () => {
    const req = { query: {}, cookies: {} };
    expect(auth.extractToken(req)).toBeNull();
  });

  test('extracts token from Authorization Bearer header', () => {
    const req = { headers: { authorization: 'Bearer xyz789' }, query: {}, cookies: {} };
    expect(auth.extractToken(req)).toBe('xyz789');
  });

  test('prefers Authorization header over query and cookie', () => {
    const req = {
      headers: { authorization: 'Bearer from-header' },
      query: { token: 'from-query' },
      cookies: { 'crowe-ide-session': 'from-cookie' },
    };
    expect(auth.extractToken(req)).toBe('from-header');
  });
});
