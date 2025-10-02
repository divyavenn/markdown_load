export const TWITTER_THREAD_REGEX = /^https?:\/\/(?:www\.)?x\.com\/([^/]+)\/status\/(\d+)(?:[/?#].*)?$/i;
export const SUBSTACK_ARTICLE_REGEX = /^https?:\/\/[^/]+\.substack\.com\/p\/[A-Za-z0-9-_.]+/i;

export const ContentTypes = Object.freeze({
  TWITTER: {
    name: 'twitter',
    regex: TWITTER_THREAD_REGEX,
    endpoint: 'convert-tweet',
    requiredCookies: ['auth_token', 'ct0'],
    errorMessage: 'Log in to X to download threads.',
  },
  SUBSTACK: {
    name: 'substack',
    regex: SUBSTACK_ARTICLE_REGEX,
    endpoint: 'convert-substack',
    requiredCookies: ['substack.sid'],
    errorMessage: 'only public previews without logging in.',
  },
});

export const contentTypes = Object.freeze(Object.values(ContentTypes));
