// Random picture API — Cloudflare Pages / EdgeOne Pages compatible.
// Compatible with oyz8/random-pic-api semantics, extended:
//   GET /pic                -> 302 to a random image (auto h/v by User-Agent)
//   GET /pic?img=h|v        -> 302 to a random horizontal / vertical image
//   GET /pic?json=h|v|a     -> JSON {theme, folder, file, url}
// Images live in /ri/{hd,hl,vd,vl}/<n>.webp, indexed by /ri/count.json.
// Env: ALLOWED_DOMAINS (comma separated, empty = public), ALLOW_EMPTY_REQUEST.

const COUNT_URL_PATH = '/ri/count.json';
const IMAGE_BASE_PATH = '/ri';
const IMAGE_EXT = '.webp';
const FOLDERS = ['hd', 'hl', 'vd', 'vl'];
const COUNT_CACHE_TTL = 5 * 60 * 1000;
const COUNT_ERROR_CACHE_TTL = 30 * 1000;

const MOBILE_KEYWORDS = [
  'mobile', 'android', 'iphone', 'ipad', 'ipod', 'blackberry',
  'windows phone', 'opera mini', 'iemobile', 'mobile safari',
  'webos', 'kindle', 'silk', 'fennec', 'maemo', 'tablet',
];

let ALLOWED_DOMAINS = [];
let ALLOW_EMPTY_REQUEST = true;
let countData = null;
let validArrays = null;
let countExpiresAt = 0;
let countLoading = null;

function initConfig(env) {
  if (!env) return;
  if (env.ALLOWED_DOMAINS && typeof env.ALLOWED_DOMAINS === 'string') {
    ALLOWED_DOMAINS = env.ALLOWED_DOMAINS.split(',').map((d) => d.trim()).filter(Boolean);
  }
  if (env.ALLOW_EMPTY_REQUEST !== undefined) {
    ALLOW_EMPTY_REQUEST =
      env.ALLOW_EMPTY_REQUEST === true ||
      env.ALLOW_EMPTY_REQUEST === 'true' ||
      env.ALLOW_EMPTY_REQUEST === '1';
  }
}

const isPublicMode = () => ALLOWED_DOMAINS.length === 0;

function extractDomain(urlString) {
  if (!urlString) return null;
  try { return new URL(urlString).hostname.toLowerCase(); } catch { return null; }
}

function isDomainAllowed(domain) {
  if (!domain) return false;
  const lower = domain.toLowerCase();
  return ALLOWED_DOMAINS.some((a) => {
    const al = a.toLowerCase();
    return lower === al || lower.endsWith('.' + al);
  });
}

function checkAntiLeech(request) {
  if (isPublicMode()) return { allowed: true };
  const refererDomain = extractDomain(request.headers.get('Referer'));
  const originDomain = extractDomain(request.headers.get('Origin'));
  if (originDomain && isDomainAllowed(originDomain)) return { allowed: true };
  if (refererDomain && isDomainAllowed(refererDomain)) return { allowed: true };
  if (!request.headers.get('Origin') && !request.headers.get('Referer') && ALLOW_EMPTY_REQUEST) {
    return { allowed: true };
  }
  return { allowed: false, reason: `域名未授权 (Origin: ${originDomain || '无'}, Referer: ${refererDomain || '无'})` };
}

function resolveCorsOrigin(request) {
  const origin = request.headers.get('Origin');
  if (isPublicMode()) return origin || '*';
  if (origin && isDomainAllowed(extractDomain(origin))) return origin;
  if (!origin && ALLOW_EMPTY_REQUEST) return '*';
  return null;
}

function buildHeaders(corsOrigin, extra = {}) {
  const headers = new Headers(extra);
  if (corsOrigin) {
    headers.set('Access-Control-Allow-Origin', corsOrigin);
    if (corsOrigin !== '*') headers.set('Vary', 'Origin');
  }
  return headers;
}

function jsonResponse(body, status, corsOrigin, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: buildHeaders(corsOrigin, { 'Content-Type': 'application/json; charset=utf-8', ...extraHeaders }),
  });
}

function textResponse(text, status, corsOrigin, extraHeaders = {}) {
  return new Response(text, {
    status,
    headers: buildHeaders(corsOrigin, { 'Content-Type': 'text/plain; charset=utf-8', ...extraHeaders }),
  });
}

function buildValidArray(config) {
  const max = Number(config?.max) || 0;
  if (max <= 0) return [];
  const exclude = new Set((Array.isArray(config?.exclude) ? config.exclude : []).map(Number));
  const arr = [];
  for (let i = 1; i <= max; i++) if (!exclude.has(i)) arr.push(i);
  return arr;
}

async function loadCount(request) {
  const now = Date.now();
  if (countData && now < countExpiresAt) return countData;
  if (countLoading) return countLoading;
  countLoading = (async () => {
    try {
      const resp = await fetch(new URL(COUNT_URL_PATH, request.url).toString());
      if (!resp.ok) throw new Error(`count.json HTTP ${resp.status}`);
      const data = await resp.json();
      const arrays = {};
      for (const folder of FOLDERS) arrays[folder] = buildValidArray(data[folder]);
      countData = data;
      validArrays = arrays;
      countExpiresAt = Date.now() + COUNT_CACHE_TTL;
      return data;
    } catch (e) {
      console.error('Failed to load count.json:', e);
      countData = { hd: { max: 0, exclude: [] }, hl: { max: 0, exclude: [] }, vd: { max: 0, exclude: [] }, vl: { max: 0, exclude: [] } };
      validArrays = { hd: [], hl: [], vd: [], vl: [] };
      countExpiresAt = Date.now() + COUNT_ERROR_CACHE_TTL;
      return countData;
    } finally {
      countLoading = null;
    }
  })();
  return countLoading;
}

const isMobileDevice = (ua) => !!ua && MOBILE_KEYWORDS.some((k) => ua.toLowerCase().includes(k));

function randomFromTwoFolders(darkFolder, lightFolder) {
  const darkArr = validArrays?.[darkFolder] || [];
  const lightArr = validArrays?.[lightFolder] || [];
  const total = darkArr.length + lightArr.length;
  if (total === 0) return null;
  const idx = Math.floor(Math.random() * total);
  if (idx < darkArr.length) return { folder: darkFolder, file: darkArr[idx], theme: 'dark' };
  return { folder: lightFolder, file: lightArr[idx - darkArr.length], theme: 'light' };
}

function randomAny() {
  const pools = FOLDERS.map((f) => ({ folder: f, arr: validArrays?.[f] || [] })).filter((p) => p.arr.length);
  const total = pools.reduce((s, p) => s + p.arr.length, 0);
  if (total === 0) return null;
  let idx = Math.floor(Math.random() * total);
  for (const p of pools) {
    if (idx < p.arr.length) {
      return { folder: p.folder, file: p.arr[idx], theme: p.folder.endsWith('d') ? 'dark' : 'light' };
    }
    idx -= p.arr.length;
  }
  return null;
}

async function handleRequest(request, env) {
  initConfig(env);
  let corsOrigin = null;
  try {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      if (!isPublicMode()) {
        const origin = request.headers.get('Origin');
        if (!origin || !isDomainAllowed(extractDomain(origin))) return textResponse('CORS 未授权', 403, null);
      }
      const allowOrigin = request.headers.get('Origin') || '*';
      const headers = {
        'Access-Control-Allow-Origin': allowOrigin,
        'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Max-Age': '86400',
      };
      if (allowOrigin !== '*') headers['Vary'] = 'Origin';
      return new Response(null, { status: 204, headers });
    }

    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return textResponse('Method Not Allowed', 405, null, { Allow: 'GET, HEAD, OPTIONS' });
    }

    const leechCheck = checkAntiLeech(request);
    if (!leechCheck.allowed) return jsonResponse({ error: leechCheck.reason }, 403, null);
    corsOrigin = resolveCorsOrigin(request);

    const imgType = url.searchParams.get('img') || url.searchParams.get('type');
    const jsonType = url.searchParams.get('json');

    await loadCount(request);

    if (jsonType === 'h' || jsonType === 'v' || jsonType === 'a') {
      const selected = jsonType === 'a'
        ? randomAny()
        : randomFromTwoFolders(jsonType + 'd', jsonType + 'l');
      if (!selected) return jsonResponse({ error: '没有图片' }, 404, corsOrigin);
      const imageUrl = new URL(`${IMAGE_BASE_PATH}/${selected.folder}/${selected.file}${IMAGE_EXT}`, url.origin).toString();
      return jsonResponse(
        { theme: selected.theme, folder: selected.folder, file: `${selected.file}${IMAGE_EXT}`, url: imageUrl },
        200, corsOrigin, { 'Cache-Control': 'no-store' },
      );
    }

    let orientation;
    if (imgType === 'h' || imgType === 'v') orientation = imgType;
    else if (imgType === 'a') orientation = null;
    else orientation = isMobileDevice(request.headers.get('User-Agent') || '') ? 'v' : 'h';

    const selected = orientation === null
      ? randomAny()
      : randomFromTwoFolders(orientation + 'd', orientation + 'l');
    if (!selected) return textResponse('没有图片', 404, corsOrigin);

    return new Response(null, {
      status: 302,
      headers: buildHeaders(corsOrigin, {
        Location: `${IMAGE_BASE_PATH}/${selected.folder}/${selected.file}${IMAGE_EXT}`,
        'Cache-Control': 'no-store',
      }),
    });
  } catch (error) {
    console.error('Request failed:', error);
    return textResponse('服务器内部错误', 500, corsOrigin);
  }
}

export async function onRequest(context) {
  return handleRequest(context.request, context.env);
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
