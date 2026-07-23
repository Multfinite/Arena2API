/**
 * Arena2API - Injector (MAIN world)
 * 
 * 在页面主世界中运行，可以直接访问：
 * - window.grecaptcha.enterprise
 * - window.__next_f (Next.js 数据)
 * - 页面的所有全局变量
 * 
 * 通过 window.postMessage 与 content.js 通信
 */
(function() {
  'use strict';

  var SITEKEY = '6LeTGMcsAAAAALuIlkVwIxaAuZA8VledA6d3Nnb0';
  var TAG = '[Arena2API]';

  // ========== 提取模型列表 ==========
  // ponytail: single hardcoded model — add more UUIDs when captured from network traffic
  function extractModels() {
    return [
      { id: "019f42aa-9c3b-76d1-8bdf-2e883b1ca227", publicName: "grok-4.5", capabilities: { outputCapabilities: ["text"], inputCapabilities: ["text"] } },
    ];
  }

  // ========== 提取 Next.js server action hashes ==========
  function extractNextActions() {
    // 这些 hash 用于 Next.js server actions（如 generateUploadUrl 等）
    // 暂时不需要，后续如果支持图片上传再添加
    return {};
  }

  // ========== reCAPTCHA token 获取 ==========
  function getRecaptchaToken(action) {
    return new Promise(function(resolve, reject) {
      if (!window.grecaptcha || !window.grecaptcha.enterprise) {
        reject(new Error('grecaptcha.enterprise not available'));
        return;
      }

      var g = window.grecaptcha.enterprise;

      g.ready(function() {
        g.execute(SITEKEY, { action: action || 'chat_submit' })
          .then(resolve)
          .catch(reject);
      });
    });
  }

  // ========== 提取 cookies ==========
  function extractCookies() {
    var cookies = {};
    try {
      var cookieStr = document.cookie;
      if (cookieStr) {
        cookieStr.split(';').forEach(function(pair) {
          var parts = pair.trim().split('=');
          if (parts.length >= 2) {
            cookies[parts[0]] = parts.slice(1).join('=');
          }
        });
      }
    } catch(e) {
      console.error(TAG, 'extractCookies error:', e);
    }
    return cookies;
  }

  // ========== 消息处理 ==========
  window.addEventListener('message', function(event) {
    if (event.source !== window) return;
    if (!event.data || event.data.from !== 'arena2api-content') return;

    var msg = event.data;
    var rid = msg.rid;

    switch (msg.type) {
      case 'GET_TOKEN':
        getRecaptchaToken(msg.action).then(function(token) {
          window.postMessage({
            from: 'arena2api-injector',
            type: 'TOKEN_OK',
            rid: rid,
            token: token,
            action: msg.action || 'chat_submit',
          }, '*');
        }).catch(function(err) {
          window.postMessage({
            from: 'arena2api-injector',
            type: 'TOKEN_ERR',
            rid: rid,
            error: err.message || String(err),
          }, '*');
        });
        break;

      case 'GET_MODELS':
        var models = extractModels();
        window.postMessage({
          from: 'arena2api-injector',
          type: 'MODELS_OK',
          rid: rid,
          models: models,
        }, '*');
        break;

      case 'GET_COOKIES':
        var cookies = extractCookies();
        window.postMessage({
          from: 'arena2api-injector',
          type: 'COOKIES_OK',
          rid: rid,
          cookies: cookies,
        }, '*');
        break;

      case 'CHECK':
        var g = window.grecaptcha && window.grecaptcha.enterprise
          ? window.grecaptcha.enterprise
          : window.grecaptcha;
        window.postMessage({
          from: 'arena2api-injector',
          type: 'CHECK_OK',
          rid: rid,
          recaptcha: !!(g && typeof g.execute === 'function'),
          enterprise: !!(window.grecaptcha && window.grecaptcha.enterprise),
        }, '*');
        break;
    }
  });

  // ========== 初始化通知 ==========
  // 延迟一下确保 content.js 已经在监听
  setTimeout(function() {
    var models = extractModels();
    var cookies = extractCookies();
    window.postMessage({
      from: 'arena2api-injector',
      type: 'INIT',
      models: models,
      cookies: cookies,
      userAgent: navigator.userAgent,
    }, '*');
    console.log(TAG, 'Injector ready, models:', models ? models.length : 0, 'cookies:', Object.keys(cookies).join(', '));
  }, 1000);

})();
