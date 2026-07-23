const origFetch = window.fetch;
window.fetch = function(...args) {
  const url = args[0]?.toString() || '';
  console.log('FETCH:', url.substring(0, 80));
  if (args[1]?.body) {
    try {
      const body = JSON.parse(args[1].body);
      if (body.modelAId) console.log('MODEL UUID:', body.modelAId, body.modelAName || '');
    } catch(e) {}
  }
  return origFetch.apply(this, args);
};
