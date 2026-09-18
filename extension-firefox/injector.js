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

  // ========== Извлечение списка моделей из страницы ==========
  // arena.ai — это Next.js App Router: серверный проп initialModels лежит в
  // flight-чанках window.__next_f. Собираем их и вырезаем JSON-массив.
  function readFlight() {
    var f = window.__next_f;
    if (!f || typeof f.length !== 'number') return '';
    var out = '';
    for (var i = 0; i < f.length; i++) {
      var e = f[i];
      if (Array.isArray(e) && e.length > 1 && typeof e[1] === 'string') out += e[1];
    }
    return out;
  }

  function sliceArray(s, start) {
    var depth = 0, inStr = false, esc = false;
    for (var i = start; i < s.length; i++) {
      var c = s[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === '\\') esc = true;
        else if (c === '"') inStr = false;
        continue;
      }
      if (c === '"') inStr = true;
      else if (c === '[') depth++;
      else if (c === ']') { depth--; if (depth === 0) return s.slice(start, i + 1); }
    }
    return null;
  }

  // publicName -> id; server.py держит dict по publicName, поэтому при дублях
  // имён побеждает последний элемент. Кладём вариант с 'web' в выводе и
  // userSelectable в конец — он и выиграет.
  function normModel(m) {
    var caps = m.capabilities || {};
    var oc = caps.outputCapabilities || {};
    var ic = caps.inputCapabilities || {};
    var outList = [];
    if (oc.text) outList.push('text');
    if (oc.web) outList.push('web');
    // 'image' в выводе не отдаём: server.py по нему считает модель
    // генератором картинок (modality='image') и чат с ней сломается.
    var inList = [];
    if (ic.text) inList.push('text');
    if (ic.image) inList.push('image');
    if (ic.file) inList.push('file');
    return {
      id: m.id,
      publicName: m.publicName || m.displayName || '',
      _score: (oc.web ? 2 : 0) + (m.userSelectable ? 1 : 0),
      capabilities: { outputCapabilities: outList, inputCapabilities: inList }
    };
  }

  function extractLiveModels() {
    var blob = readFlight();
    if (!blob) return null;
    var i = blob.indexOf('initialModels');
    if (i < 0) return null;
    var start = blob.indexOf('[', i);
    if (start < 0) return null;
    var seg = sliceArray(blob, start);
    if (!seg) return null;
    var raw;
    try { raw = JSON.parse(seg); } catch (e) { return null; }
    if (!Array.isArray(raw) || !raw.length) return null;
    var byName = {};
    raw.forEach(function(m) {
      if (!m || !m.id) return;
      var n = normModel(m);
      if (!n.publicName || !n.capabilities.outputCapabilities.length) return;
      (byName[n.publicName] = byName[n.publicName] || []).push(n);
    });
    var out = [];
    Object.keys(byName).forEach(function(name) {
      var g = byName[name].sort(function(a, b) { return a._score - b._score; });
      g.forEach(function(m) { delete m._score; out.push(m); });
    });
    return out.length ? out : null;
  }

  function extractModels() {
    var live = null;
    try { live = extractLiveModels(); } catch (e) { console.warn(TAG, 'live extract failed:', e); }
    if (live && live.length) {
      console.log(TAG, 'models from window.__next_f:', live.length);
      return live;
    }
    var stat = window.__ARENA_STATIC_MODELS__;   // из arena_models.js (опционально)
    if (stat && stat.length) {
      console.log(TAG, 'models from static list:', stat.length);
      return stat;
    }
    console.warn(TAG, 'no models found, falling back to hardcoded grok-4.5');
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
