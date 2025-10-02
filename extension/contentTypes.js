const TWITTER_THREAD_REGEX = /^https?:\/\/(?:www\.)?x\.com\/([^/]+)\/status\/(\d+)(?:[/?#].*)?$/i;
const SUBSTACK_ARTICLE_REGEX = /^https?:\/\/[^/]+\.substack\.com\/p\/[A-Za-z0-9-_.]+/i;

export class ContentType{
  constructor(name, regex, handler, endpoint, fileNameGenerator){
    this.name = name;
    this.regex = regex;
    this.handler = handler;
    this.endpoint = endpoint;
    this.fileNameGenerator = fileNameGenerator;
  }
}

async function getCookies(url){
  return new Promise((resolve) => {
    chrome.cookies.getAll({ url }, (cookies) => {
      if (chrome.runtime.lastError) {
        console.warn('Cookie lookup failed', chrome.runtime.lastError.message);
        resolve(null);
        return;
      }
      const cookieMap = {};
      cookies.forEach((cookie) => {
        cookieMap[cookie.name] = cookie.value;
      });
      resolve(cookieMap);
    });
  });
}

function getCookie(url, name) {
  return new Promise((resolve) => {
    chrome.cookies.get({ url, name }, (cookie) => {
      if (chrome.runtime.lastError) {
        console.warn('Cookie lookup failed', chrome.runtime.lastError.message);
        resolve(null);
        return;
      }
      resolve(cookie ? cookie.value : null);
    });
  });
}

export async function substackFileName(url){
  const slug = slugify(url.split('/').pop() || 'substack-article');
  filenameInput.value = `${slug}.md`;
}

export async function prepareSubstackUI(url) {
  queueDisabled = false;
  queueButton.disabled = false;

  const cookie = await getCookie(url, 'substack.sid');
  setStatus(cookie ? defaultStatus : 'Without logging in, we can only download the public preview.');
}

export async function tweetFileName(url){
  const match = url.match(TWITTER_THREAD_REGEX);
  const handle = match ? match[1] : 'thread';
  const tweetId = match ? match[2] : 'tweet';
  const slug = slugify(`${handle}-${tweetId}`) || 'twitter-thread';
  filenameInput.value = `${slug}.md`;
}

export async function prepareTwitterUI(url) {
  queueDisabled = false;
  queueButton.disabled = false;


  const [authToken, ct0] = await Promise.all([
    getCookie(url, 'auth_token'),
    getCookie(url, 'ct0'),
  ]);

  if (authToken && ct0) {
    setStatus(defaultStatus);
  } else {
    setStatus('You have to log in to X.');
  }
}


export const contentTypes = [
  new ContentType('twitter', TWITTER_THREAD_REGEX, prepareTwitterUI, '/convert-twitter'),
  new ContentType('substack', SUBSTACK_ARTICLE_REGEX, prepareSubstackUI, '/convert-substack'),
]