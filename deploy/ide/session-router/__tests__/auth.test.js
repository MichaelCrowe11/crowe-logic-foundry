const { createAuthModule } = require('../auth');
const { SignJWT, exportJWK, generateKeyPair } = require('jose');

let keyPair;
let jwkPublic;
let auth;

beforeAll(async () => {
  keyPair = await generateKeyPair('RS256');
  jwkPublic = await exportJWK(keyPair.publicKey);
  jwkPublic.kid = 'test-key-id';
  jwkPublic.alg = 'RS256';
  jwkPublic.use = 'sig';
});

beforeEach(() => {
  const mockFetchJWKS = async () => ({ keys: [jwkPublic] });
  auth = createAuthModule({
    supabaseUrl: 'https://test.supabase.co',
    fetchJWKS: mockFetchJWKS,
  });
});

async function makeJWT(claims = {}, expiresIn = '60s') {
  return new SignJWT({ sub: 'user-123', role: 'admin', ...claims })
    .setProtectedHeader({ alg: 'RS256', kid: 'test-key-id' })
    .setIssuer('https://test.supabase.co/auth/v1')
    .setAudience('authenticated')
    .setExpirationTime(expiresIn)
    .setIssuedAt()
    .sign(keyPair.privateKey);
}

describe('validateToken', () => {
  test('returns user data for valid token', async () => {
    const token = await makeJWT({ sub: 'user-123', role: 'admin' });
    const result = await auth.validateToken(token);
    expect(result).toEqual({
      userId: 'user-123',
      role: 'admin',
    });
  });

  test('rejects expired token', async () => {
    const token = await makeJWT({}, '-1s');
    await expect(auth.validateToken(token)).rejects.toThrow();
  });

  test('rejects token with wrong issuer', async () => {
    const token = await new SignJWT({ sub: 'user-123', role: 'admin' })
      .setProtectedHeader({ alg: 'RS256', kid: 'test-key-id' })
      .setIssuer('https://wrong.supabase.co/auth/v1')
      .setAudience('authenticated')
      .setExpirationTime('60s')
      .setIssuedAt()
      .sign(keyPair.privateKey);
    await expect(auth.validateToken(token)).rejects.toThrow();
  });

  test('rejects malformed token string', async () => {
    await expect(auth.validateToken('not-a-jwt')).rejects.toThrow();
  });

  test('rejects empty string', async () => {
    await expect(auth.validateToken('')).rejects.toThrow();
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

  test('prefers query token over cookie', () => {
    const req = { query: { token: 'from-query' }, cookies: { 'crowe-ide-session': 'from-cookie' } };
    expect(auth.extractToken(req)).toBe('from-query');
  });

  test('returns null when no token present', () => {
    const req = { query: {}, cookies: {} };
    expect(auth.extractToken(req)).toBeNull();
  });
});
