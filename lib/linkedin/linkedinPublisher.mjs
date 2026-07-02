// lib/linkedin/linkedinPublisher.mjs
//
// LinkedIn Publisher — R-F2288
// =============================
// Cross-posts curated intelligence from the Telegram channel to LinkedIn.
//
// Uses the LinkedIn API (v2) for posting text + link shares.
// Requires LINKEDIN_ACCESS_TOKEN and LINKEDIN_ORGANIZATION_ID env vars.
//
// Post types:
//   1. Text posts — formatted intelligence updates
//   2. Link shares — article/intel with preview card
//   3. Image posts — infographic cards (via upload)
//
// LinkedIn API docs: https://learn.microsoft.com/en-us/linkedin/marketing/

// ── Constants ──────────────────────────────────────────────────────────────────

const LINKEDIN_API = 'https://api.linkedin.com/v2';

const ACCESS_TOKEN = process.env.LINKEDIN_ACCESS_TOKEN || null;
const ORGANIZATION_ID = process.env.LINKEDIN_ORGANIZATION_ID || null;
const PERSON_URN = process.env.LINKEDIN_PERSON_URN || null;

/** Max LinkedIn post length. */
const MAX_POST_LENGTH = 3000;

/** Cooldown between posts (minutes). */
const POST_COOLDOWN_MIN = 120;

/** Max posts per day. */
const MAX_DAILY_POSTS = 3;

// ── State ──────────────────────────────────────────────────────────────────────

let _lastPostAt = 0;
let _dailyPostCount = 0;
let _dailyPostDate = '';

// ── Configuration ──────────────────────────────────────────────────────────────

/**
 * Check if LinkedIn publisher is configured.
 *
 * @returns {boolean}
 */
export function isConfigured() {
  return !!(ACCESS_TOKEN && (ORGANIZATION_ID || PERSON_URN));
}

/**
 * Get configuration status.
 *
 * @returns {{ configured: boolean, hasToken: boolean, hasOrg: boolean, hasPerson: boolean }}
 */
export function getConfig() {
  return {
    configured: isConfigured(),
    hasToken: !!ACCESS_TOKEN,
    hasOrg: !!ORGANIZATION_ID,
    hasPerson: !!PERSON_URN,
  };
}

// ── Rate Limiting ──────────────────────────────────────────────────────────────

/**
 * Check if we can post now.
 *
 * @returns {{ canPost: boolean, reason: string }}
 */
export function canPostNow() {
  const now = Date.now();

  const today = new Date().toISOString().split('T')[0];
  if (_dailyPostDate !== today) {
    _dailyPostDate = today;
    _dailyPostCount = 0;
  }
  if (_dailyPostCount >= MAX_DAILY_POSTS) {
    return { canPost: false, reason: 'daily limit reached' };
  }

  const elapsed = now - _lastPostAt;
  if (elapsed < POST_COOLDOWN_MIN * 60_000) {
    return { canPost: false, reason: 'cooldown active' };
  }

  return { canPost: true, reason: 'ok' };
}

/**
 * Record a post (updates rate-limit state).
 */
export function recordPost() {
  _lastPostAt = Date.now();
  const today = new Date().toISOString().split('T')[0];
  if (_dailyPostDate !== today) {
    _dailyPostDate = today;
    _dailyPostCount = 0;
  }
  _dailyPostCount++;
}

/**
 * Get publisher state.
 *
 * @returns {object}
 */
export function getState() {
  return {
    configured: isConfigured(),
    lastPostAt: _lastPostAt,
    dailyPostCount: _dailyPostCount,
    dailyPostDate: _dailyPostDate,
  };
}

/**
 * Reset state (for testing).
 */
export function _resetState() {
  _lastPostAt = 0;
  _dailyPostCount = 0;
  _dailyPostDate = '';
}

// ── Posting ─────────────────────────────────────────────────────────────────────

/**
 * Post a text update to LinkedIn.
 *
 * @param {string} text — Post content (markdown stripped, max 3000 chars).
 * @param {object} [opts]
 * @param {string} [opts.channel] — 'organization' or 'person' (default: organization).
 * @returns {Promise<{ok:boolean,postId?:string,error?:string}>}
 */
export async function postTextUpdate(text, opts = {}) {
  if (!isConfigured()) return { ok: false, error: 'LinkedIn not configured' };

  const channel = opts.channel || 'organization';
  const author = channel === 'person' ? `urn:li:person:${PERSON_URN}` : `urn:li:organization:${ORGANIZATION_ID}`;

  // Strip markdown for LinkedIn (it doesn't support it)
  const cleanText = _stripMarkdown(text).substring(0, MAX_POST_LENGTH);

  try {
    const body = {
      author,
      lifecycleState: 'PUBLISHED',
      specificContent: {
        'com.linkedin.ugc.ShareContent': {
          shareCommentary: {
            text: cleanText,
          },
          shareMediaCategory: 'NONE',
        },
      },
      visibility: {
        'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
      },
    };

    const res = await fetch(`${LINKEDIN_API}/ugcPosts`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ACCESS_TOKEN}`,
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 300)}` };
    }

    const data = await res.json();
    recordPost();
    return { ok: true, postId: data.id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Post a link share to LinkedIn (article with preview card).
 *
 * @param {object} linkData
 * @param {string} linkData.url — The URL to share.
 * @param {string} linkData.title — Title for the preview card.
 * @param {string} [linkData.description] — Description for the preview card.
 * @param {string} [linkData.text] — Commentary text.
 * @param {string} [opts.channel] — 'organization' or 'person'.
 * @returns {Promise<{ok:boolean,postId?:string,error?:string}>}
 */
export async function postLinkShare(linkData, opts = {}) {
  if (!isConfigured()) return { ok: false, error: 'LinkedIn not configured' };

  const { url, title, description, text } = linkData;
  if (!url || !title) return { ok: false, error: 'url and title are required' };

  const channel = opts.channel || 'organization';
  const author = channel === 'person' ? `urn:li:person:${PERSON_URN}` : `urn:li:organization:${ORGANIZATION_ID}`;

  try {
    const body = {
      author,
      lifecycleState: 'PUBLISHED',
      specificContent: {
        'com.linkedin.ugc.ShareContent': {
          shareCommentary: {
            text: (text ? _stripMarkdown(text).substring(0, 600) : ''),
          },
          shareMediaCategory: 'ARTICLE',
          media: [{
            status: 'READY',
            description: { text: (description || '').substring(0, 256) },
            originalUrl: url,
            title: { text: title.substring(0, 200) },
          }],
        },
      },
      visibility: {
        'com.linkedin.ugc.MemberNetworkVisibility': 'PUBLIC',
      },
    };

    const res = await fetch(`${LINKEDIN_API}/ugcPosts`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ACCESS_TOKEN}`,
        'Content-Type': 'application/json',
        'X-Restli-Protocol-Version': '2.0.0',
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 300)}` };
    }

    const data = await res.json();
    recordPost();
    return { ok: true, postId: data.id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Format a channel post for LinkedIn cross-posting.
 *
 * Converts Telegram markdown to plain text and adds appropriate
 * hashtags and formatting for LinkedIn's audience.
 *
 * @param {string} channelPost — The original Telegram channel post.
 * @param {object} [opts]
 * @param {string[]} [opts.extraTags] — Additional hashtags.
 * @returns {string} LinkedIn-formatted post.
 */
export function formatForLinkedIn(channelPost, opts = {}) {
  if (!channelPost) return '';

  const extraTags = opts.extraTags || [];

  // Strip Telegram markdown
  let text = _stripMarkdown(channelPost);

  // Remove the reply-keyword hint (Phase 3 feature, not relevant on LinkedIn)
  text = text.replace(/💬.*Reply with.*\n?/g, '');

  // Remove the ARIA Intelligence footer
  text = text.replace(/━━━━━━━━━━━━━━━━━━\n🤖.*ARIA Intelligence.*/g, '');

  // Trim
  text = text.trim().substring(0, MAX_POST_LENGTH);

  // Add hashtags
  const defaultTags = ['#Intelligence', '#Defence', '#Security', '#Africa', '#Geopolitics', '#ARIA'];
  // Extra tags take priority over defaults (replace from the end)
  const combined = [...extraTags, ...defaultTags];
  const tags = [...new Set(combined)].slice(0, 5).join(' ');
  text = `${text}\n\n${tags}\n\n— *ARIA Intelligence*`;

  return text;
}

// ── Internal Helpers ───────────────────────────────────────────────────────────

/**
 * Strip Telegram markdown for plain-text platforms.
 */
function _stripMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/\*([^*]+)\*/g, '$1')     // bold
    .replace(/_([^_]+)_/g, '$1')       // italic
    .replace(/`([^`]+)`/g, '$1')       // code
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links
    .replace(/~~([^~]+)~~/g, '$1')     // strikethrough
    .replace(/\|/g, '')                // table separators
    .replace(/#{1,6}\s/g, '')          // headers
    .replace(/>\s/g, '')               // blockquotes
    .replace(/━━+/g, '')               // dividers
    .replace(/\n{3,}/g, '\n\n')        // excessive newlines
    .trim();
}
