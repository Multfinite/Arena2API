// Arena2API UUID Finder
// Drag this into your browser bookmarks bar as a bookmarklet
// Then click it while on arena.ai to see model UUIDs in console.
//
// To use as bookmarklet:
// 1. Create a new bookmark in your browser
// 2. Set the URL to the minified code below
// 3. Click it on arena.ai and open DevTools console (F12)
//
// Bookmarklet version:
javascript:(function(){const o=window.fetch;window.fetch=function(...a){const u=a[0]?.toString()||'';console.log('FETCH:',u.substring(0,80));if(a[1]?.body){try{const b=JSON.parse(a[1].body);if(b.modelAId)console.log('MODEL UUID:',b.modelAId,b.modelAName||'');}catch(e){}}return o.apply(this,a);}})();

(function() {
  const origFetch = window.fetch;
  window.fetch = function(...args) {
    const url = args[0]?.toString() || '';
    console.log('FETCH:', url.substring(0, 80));
    if (args[1]?.body) {
      try {
        const body = JSON.parse(args[1].body);
        if (body.modelAId) console.log('MODEL UUID:', body.modelAId, body.modelAName || '');
        if (body.modelAName) console.log('MODEL NAME:', body.modelAName);
      } catch(e) {}
    }
    return origFetch.apply(this, args);
  };
  console.log('Arena2API UUID Finder active. Check console for model UUIDs when you send a message.');
})();
