/**
 * Arena2API - Popup
 */
(function() {
  var $ = function(id) { return document.getElementById(id); };

  function update(s) {
    if (!s) return;
    // Server
    if (s.connected) {
      $('srv').innerHTML = '<span class="dot dot-g"></span>Connected';
      $('srv').className = 'val ok';
    } else {
      $('srv').innerHTML = '<span class="dot dot-r"></span>Disconnected';
      $('srv').className = 'val err';
    }
    // Tab
    if (s.tabId) {
      $('tab').innerHTML = '<span class="dot dot-g"></span>Active';
      $('tab').className = 'val ok';
    } else {
      $('tab').innerHTML = '<span class="dot dot-y"></span>No Tab';
      $('tab').className = 'val warn';
    }
    // Error
    $('err').textContent = s.lastError || 'None';
    $('err').className = 'val ' + (s.lastError ? 'err' : 'ok');
    // V3
    $('v3').textContent = s.v3Count || 0;
    $('v3').className = 'val ' + (s.v3Count > 0 ? 'ok' : 'warn');
    // Auth
    if (s.hasAuth) {
      $('auth').innerHTML = '<span class="dot dot-g"></span>Yes';
      $('auth').className = 'val ok';
    } else {
      $('auth').innerHTML = '<span class="dot dot-r"></span>No';
      $('auth').className = 'val err';
    }
    // Models
    $('mdl').textContent = s.modelCount || 0;
    $('mdl').className = 'val ' + (s.modelCount > 0 ? 'ok' : 'warn');
  }

  function refresh() {
    chrome.runtime.sendMessage({ type: 'GET_STATUS' }, function(s) {
      if (!chrome.runtime.lastError && s) update(s);
    });
  }

  $('save').onclick = function() {
    var url = $('url').value.trim();
    if (url) {
      chrome.runtime.sendMessage({ type: 'SET_PROXY_URL', url: url }, function() {
        $('save').textContent = 'OK!';
        setTimeout(function() { $('save').textContent = 'Save'; }, 1000);
      });
    }
  };

  $('open').onclick = function() {
    chrome.tabs.create({ url: 'https://arena.ai/text/direct' });
    window.close();
  };

  $('token').onclick = function() {
    chrome.runtime.sendMessage({ type: 'FORCE_TOKEN' }, function() {
      $('token').textContent = 'Sent!';
      setTimeout(function() { $('token').textContent = 'Get Token'; refresh(); }, 1500);
    });
  };

  $('push').onclick = function() {
    chrome.runtime.sendMessage({ type: 'FORCE_PUSH' }, function() {
      $('push').textContent = 'OK!';
      setTimeout(function() { $('push').textContent = 'Push'; refresh(); }, 1000);
    });
  };

  // ========== Model management ==========
  function renderModels(models) {
    var list = $('mlist');
    list.innerHTML = '';
    if (!models || !models.length) {
      var empty = document.createElement('div');
      empty.style.cssText = 'color:#6b7280;padding:4px 0;';
      empty.textContent = 'No custom models';
      list.appendChild(empty);
      return;
    }
    models.forEach(function(m, i) {
      var row = document.createElement('div');
      row.className = 'mrow';

      var info = document.createElement('div');
      var nameSpan = document.createElement('span');
      nameSpan.className = 'mname';
      nameSpan.textContent = m.publicName;
      var br = document.createElement('br');
      var uuidSpan = document.createElement('span');
      uuidSpan.className = 'muuid';
      uuidSpan.textContent = m.id;
      info.appendChild(nameSpan);
      info.appendChild(br);
      info.appendChild(uuidSpan);

      var del = document.createElement('button');
      del.className = 'mdel';
      del.textContent = '✕';
      del.dataset.idx = i;
      del.onclick = function() {
        chrome.storage.local.get(['customModels'], function(r) {
          var arr = r.customModels || [];
          arr.splice(i, 1);
          chrome.storage.local.set({ customModels: arr }, function() {
            renderModels(arr);
            chrome.runtime.sendMessage({ type: 'MODELS_UPDATED', models: arr });
          });
        });
      };

      row.appendChild(info);
      row.appendChild(del);
      list.appendChild(row);
    });
  }

  function loadModels() {
    chrome.storage.local.get(['customModels'], function(r) {
      renderModels(r.customModels || []);
    });
  }

  function addModel() {
    var name = $('mname').value.trim();
    var uuid = $('muuid').value.trim();
    if (!name || !uuid) return;
    // Basic UUID format check
    if (!/^[0-9a-f-]{20,}$/i.test(uuid)) {
      $('muuid').style.borderColor = '#f87171';
      setTimeout(function() { $('muuid').style.borderColor = ''; }, 1500);
      return;
    }
    var model = {
      id: uuid,
      publicName: name,
      capabilities: { outputCapabilities: ["text"], inputCapabilities: ["text"] }
    };
    chrome.storage.local.get(['customModels'], function(r) {
      var arr = r.customModels || [];
      // Dedupe by UUID
      arr = arr.filter(function(m) { return m.id !== uuid; });
      arr.push(model);
      chrome.storage.local.set({ customModels: arr }, function() {
        renderModels(arr);
        $('mname').value = '';
        $('muuid').value = '';
        chrome.runtime.sendMessage({ type: 'MODELS_UPDATED', models: arr });
      });
    });
  }

  $('madd').onclick = addModel;
  $('muuid').onkeydown = function(e) { if (e.key === 'Enter') addModel(); };
  $('mname').onkeydown = function(e) { if (e.key === 'Enter') $('muuid').focus(); };
  loadModels();

  // Init
  chrome.storage.local.get(['proxyUrl'], function(r) {
    $('url').value = r.proxyUrl || 'http://127.0.0.1:9090';
  });
  refresh();
  setInterval(refresh, 2000);
})();
