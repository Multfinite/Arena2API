/**
 * Arena2API - Background Service Worker
 * 
 * 管理 token 池、cookies，定期推送到本地代理服务器
 */
(function() {
  'use strict';

  var TAG = '[Arena2API]';

  // ========== 状态 ==========
  var state = {
    proxyUrl: 'http://127.0.0.1:9090',
    connected: false,
    lastError: '',
    lastPush: 0,

    // tokens
    v3Tokens: [],   // [{token, action, ts}]
    v2Token: null,

    // cookies
    cookies: {},
    authToken: '',
    cfClearance: '',

    // models
    models: null,
    injectorModels: [],  // raw from injector.js

    // tab
    tabId: null,

    // user agent
    userAgent: '',
  };

  // ========== 从页面获取 cookies ==========
  async function requestPageCookies() {
    if (!state.tabId) return null;
    try {
      return await new Promise(function(resolve) {
        chrome.tabs.sendMessage(state.tabId, { type: 'NEED_COOKIES' }, function(resp) {
          if (chrome.runtime.lastError || !resp || !resp.cookies) {
            resolve(null);
          } else {
            resolve(resp.cookies);
          }
        });
      });
    } catch(e) {
      return null;
    }
  }

  // ========== Cookie 刷新 ==========
  async function refreshCookies() {
    try {
      var byDomain = await chrome.cookies.getAll({ domain: 'arena.ai' });
      var byDotDomain = await chrome.cookies.getAll({ domain: '.arena.ai' });
      var byUrl = await chrome.cookies.getAll({ url: 'https://arena.ai' });

      console.log(TAG, 'chrome.cookies.getAll results:');
      console.log(TAG, '  domain=arena.ai:', byDomain.length, 'cookies:', byDomain.map(function(c) { return c.name; }).join(', '));
      console.log(TAG, '  domain=.arena.ai:', byDotDomain.length, 'cookies:', byDotDomain.map(function(c) { return c.name; }).join(', '));
      console.log(TAG, '  url=https://arena.ai:', byUrl.length, 'cookies:', byUrl.map(function(c) { return c.name; }).join(', '));

      state.cookies = {};
      byDomain.concat(byDotDomain).concat(byUrl).forEach(function(c) {
        state.cookies[c.name] = c.value;
      });

      // 尝试从页面获取 document.cookie（可以读取非 HttpOnly cookies）
      var pageCookies = await requestPageCookies();
      if (pageCookies) {
        console.log(TAG, 'Page cookies:', Object.keys(pageCookies).join(', '));
        // 合并页面 cookies
        for (var k in pageCookies) {
          if (!state.cookies[k]) {
            state.cookies[k] = pageCookies[k];
          }
        }
      }

      console.log(TAG, 'All cookies:', Object.keys(state.cookies).join(', '));

      state.cfClearance = state.cookies['cf_clearance'] || '';

      // auth token 可能分片存储
      var auth = state.cookies['arena-auth-prod-v1'] || '';
      if (!auth) {
        var p0 = state.cookies['arena-auth-prod-v1.0'] || '';
        var p1 = state.cookies['arena-auth-prod-v1.1'] || '';
        console.log(TAG, 'Checking fragmented auth cookies - p0:', !!p0, 'p1:', !!p1);
        if (p0) {
          auth = p0 + (p1 || '');
          console.log(TAG, 'Combined auth token length:', auth.length);
        }
      }
      state.authToken = auth;

      if (auth) {
        console.log(TAG, 'Auth Cookie found! Length:', auth.length, 'Preview:', auth.substring(0, 50) + '...');
      } else {
        console.log(TAG, 'Auth Cookie NOT found. Available cookies:', Object.keys(state.cookies));
      }
    } catch(e) {
      console.error(TAG, 'Cookie error:', e);
    }
  }

  // ========== Model merging (injector defaults + custom from storage) ==========
  function mergeModels(customModels) {
    // Custom models override injector models by UUID
    var base = state.injectorModels || [];
    var custom = customModels || [];
    var byId = {};
    base.forEach(function(m) { byId[m.id] = m; });
    custom.forEach(function(m) { byId[m.id] = m; });
    state.models = Object.values(byId);
  }

  // ========== Token 管理 ==========
  function addToken(token, action) {
    if (!token || token.length < 20) return;
    if (state.v3Tokens.some(function(t) { return t.token === token; })) return;
    state.v3Tokens.push({ token: token, action: action || 'chat_submit', ts: Date.now() });
    while (state.v3Tokens.length > 10) state.v3Tokens.shift();
    console.log(TAG, 'Token added, pool:', state.v3Tokens.length);
  }

  // 清理过期 token
  function cleanTokens() {
    var now = Date.now();
    state.v3Tokens = state.v3Tokens.filter(function(t) { return now - t.ts < 110000; });
  }

  // ========== 向 content script 请求 token ==========
  function requestToken() {
    return new Promise(function(resolve, reject) {
      if (!state.tabId) {
        chrome.tabs.query({ url: 'https://arena.ai/*' }, function(tabs) {
          if (chrome.runtime.lastError || !tabs || tabs.length === 0) {
            reject(new Error('No active arena.ai tab found'));
            return;
          }
          state.tabId = tabs[0].id;
          sendTokenRequest(resolve, reject);
        });
      } else {
        sendTokenRequest(resolve, reject);
      }
    });
  }

  function sendTokenRequest(resolve, reject) {
    try {
      chrome.tabs.sendMessage(state.tabId, {
        type: 'NEED_TOKEN',
        action: 'chat_submit',
      }, function(resp) {
        if (chrome.runtime.lastError) {
          state.tabId = null;
          reject(new Error(chrome.runtime.lastError.message || 'tabs.sendMessage error'));
          return;
        }
        if (resp && resp.token) {
          addToken(resp.token, resp.action);
          pushToServer().then(resolve).catch(reject);
        } else {
          reject(new Error(resp && resp.error ? resp.error : 'No token received from page'));
        }
      });
    } catch(e) {
      state.tabId = null;
      reject(e);
    }
  }

  // ========== 推送到服务器 ==========
  async function pushToServer() {
    if (!state.proxyUrl) return;
    try {
      await refreshCookies();
      cleanTokens();

      var data = {
        cookies: state.cookies,
        auth_token: state.authToken,
        cf_clearance: state.cfClearance,
        user_agent: state.userAgent || '',
        v3_tokens: state.v3Tokens.map(function(t) {
          return { token: t.token, action: t.action, age_ms: Date.now() - t.ts };
        }),
        v2_token: state.v2Token ? {
          token: state.v2Token.token,
          age_ms: Date.now() - state.v2Token.ts,
        } : null,
        models: state.models,
      };

      var url = state.proxyUrl.replace(/\/+$/, '') + '/v1/extension/push';
      var resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });

      if (resp.ok) {
        state.connected = true;
        state.lastError = '';
        state.lastPush = Date.now();
        var result = await resp.json();
        // Removed automatic need_tokens pre-fetching to prevent farming low-score tokens
      } else {
        state.connected = false;
        state.lastError = 'HTTP ' + resp.status;
        throw new Error('HTTP ' + resp.status);
      }
    } catch(e) {
      state.connected = false;
      state.lastError = e.message || 'Connection failed';
      throw e;
    }
  }

  // ========== 消息处理 ==========
  chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
    switch (msg.type) {
      case 'TAB_READY':
        state.tabId = sender.tab ? sender.tab.id : null;
        console.log(TAG, 'Tab ready:', state.tabId);
        refreshCookies().then(function() { pushToServer(); });
        sendResponse({ ok: true });
        break;

      case 'PAGE_INIT':
        if (msg.userAgent) {
          state.userAgent = msg.userAgent;
          console.log(TAG, 'User agent received:', state.userAgent);
        }
        if (msg.models && msg.models.length > 0) {
          state.injectorModels = msg.models;
          // Merge with custom models from storage
          chrome.storage.local.get(['customModels'], function(r) {
            mergeModels(r.customModels || []);
            console.log(TAG, 'Models merged:', state.models.length, '(injector:', state.injectorModels.length, '+ custom:', (r.customModels || []).length + ')');
          });
        }
        if (msg.pageCookies) {
          console.log(TAG, 'Page cookies received:', Object.keys(msg.pageCookies).join(', '));
          // 合并页面 cookies
          for (var k in msg.pageCookies) {
            if (!state.cookies[k]) {
              state.cookies[k] = msg.pageCookies[k];
            }
          }
          // 重新检查 auth token
          var auth = state.cookies['arena-auth-prod-v1'] || '';
          if (!auth) {
            var p0 = state.cookies['arena-auth-prod-v1.0'] || '';
            var p1 = state.cookies['arena-auth-prod-v1.1'] || '';
            if (p0) {
              auth = p0 + (p1 || '');
              state.authToken = auth;
              console.log(TAG, 'Auth token updated from page cookies! Length:', auth.length);
            }
          }
        }
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'NEW_TOKEN':
        addToken(msg.token, msg.action);
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'GET_STATUS':
        cleanTokens();
        refreshCookies().then(function() {
          sendResponse({
            connected: state.connected,
            proxyUrl: state.proxyUrl,
            lastError: state.lastError,
            lastPush: state.lastPush,
            v3Count: state.v3Tokens.length,
            hasV2: !!state.v2Token,
            hasAuth: !!state.authToken,
            hasCf: !!state.cfClearance,
            hasModels: !!(state.models && state.models.length),
            modelCount: state.models ? state.models.length : 0,
            tabId: state.tabId,
          });
        });
        return true;

      case 'SET_PROXY_URL':
        state.proxyUrl = msg.url;
        chrome.storage.local.set({ proxyUrl: msg.url });
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'FORCE_PUSH':
        pushToServer();
        sendResponse({ ok: true });
        break;

      case 'FORCE_TOKEN':
        requestToken();
        sendResponse({ ok: true });
        break;

      case 'MODELS_UPDATED':
        // Custom models updated from popup UI
        if (msg.models) {
          mergeModels(msg.models);
          pushToServer();
        }
        sendResponse({ ok: true });
        break;

      default:
        sendResponse({ error: 'unknown' });
    }
  });

  // ========== Long Poll Commands from Server ==========
  var polling = false;
  async function pollCommands() {
    if (polling) return;
    polling = true;
    while (polling) {
      if (!state.proxyUrl) {
        await new Promise(function(r) { setTimeout(r, 5000); });
        continue;
      }
      try {
        var url = state.proxyUrl.replace(/\/+$/, '') + '/v1/extension/command';
        var resp = await fetch(url);
        if (resp.ok) {
          var data = await resp.json();
          if (data && data.command === 'get_token') {
            console.log(TAG, 'Server requested token on-demand');
            try {
              await requestToken();
              console.log(TAG, 'Successfully fetched and pushed token on-demand');
            } catch(tokenErr) {
              console.error(TAG, 'Failed to fetch/push token on-demand:', tokenErr);
            }
          }
        } else {
          // If server returns error, wait before retry
          await new Promise(function(r) { setTimeout(r, 5000); });
        }
      } catch(e) {
        // Network error, wait
        await new Promise(function(r) { setTimeout(r, 5000); });
      }
    }
  }

  // ========== 定时任务 ==========
  // 定期清理过期 token
  setInterval(function() {
    cleanTokens();
  }, 30000);

  // 每 30 秒推送一次
  setInterval(function() {
    pushToServer();
  }, 30000);

  // ========== 初始化 ==========
  chrome.storage.local.get(['proxyUrl', 'customModels'], function(result) {
    if (result.proxyUrl) state.proxyUrl = result.proxyUrl;
    if (result.customModels && result.customModels.length > 0) {
      mergeModels(result.customModels);
      console.log(TAG, 'Loaded custom models from storage:', result.customModels.length);
    }
    console.log(TAG, 'Proxy URL:', state.proxyUrl);
    // 启动后立即推送
    refreshCookies().then(function() { pushToServer(); });
    // Start command polling
    pollCommands();
  });

  // 监听 tab 关闭
  chrome.tabs.onRemoved.addListener(function(tabId) {
    if (tabId === state.tabId) state.tabId = null;
  });

  // 监听 cookie 变化
  chrome.cookies.onChanged.addListener(function(info) {
    if (info.cookie.domain.indexOf('arena.ai') >= 0) {
      refreshCookies();
    }
  });

  console.log(TAG, 'Background started');
})();
