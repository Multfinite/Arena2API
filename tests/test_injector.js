/**
 * test_injector.js — гоняет НАСТОЯЩИЙ extension/injector.js в node
 * против реальных flight-данных, вытащенных из сохранённой страницы arena.ai.
 *
 *   node test_injector.js [flight.txt] [extDir]
 *
 * Без аргументов берёт tests/flight_sample.txt и ../extension.
 *
 * Проверяет, что GET_MODELS отдаёт модели, что Max имеет ожидаемый UUID
 * и что ни у одной текстовой модели нет 'image' в outputCapabilities.
 */
const fs = require('fs');
const path = require('path');

const flightPath = process.argv[2] || path.join(__dirname, 'flight_sample.txt');
const extDir = process.argv[3] || path.join(__dirname, '..', 'extension');

if (process.argv.includes('--help')) {
  console.log('usage: node test_injector.js [flight.txt] [extDir]');
  console.log('  flight.txt — flight-чанки страницы arena.ai (по умолчанию tests/flight_sample.txt)');
  console.log('  extDir     — каталог расширения (по умолчанию ../extension)');
  process.exit(0);
}

if (!fs.existsSync(flightPath)) {
  console.error(`FAIL: нет файла flight-данных: ${flightPath}`);
  process.exit(2);
}

// ---- фейковое окно ----
const listeners = [];
const posted = [];
const win = {
  __next_f: [null, 'ignored'],           // чанки ниже подставим из flight.txt
  addEventListener: (t, cb) => { if (t === 'message') listeners.push(cb); },
  postMessage: (m) => posted.push(m),
  grecaptcha: null,
  document: { cookie: 'a=b' },
};
win.window = win;
global.window = win;
global.navigator = { userAgent: 'node-test' };

// __next_f хранит готовые (уже анэскейпленные) строки — берём их из flight.txt
win.__next_f.push([1, fs.readFileSync(flightPath, 'utf8')]);

// подгружаем статический fallback, как это делает manifest
const staticSrc = fs.readFileSync(path.join(extDir, 'arena_models.js'), 'utf8');
new Function('window', staticSrc)(win);

// грузим сам инжектор
const injectorSrc = fs.readFileSync(path.join(extDir, 'injector.js'), 'utf8');
new Function('window', 'console', 'setTimeout', injectorSrc)(win, console, setTimeout);

if (!listeners.length) { console.error('FAIL: injector не подписался на message'); process.exit(1); }

// ---- просим модели ровно так, как это делает content.js ----
listeners[0]({ source: win, data: { from: 'arena2api-content', type: 'GET_MODELS', rid: 'test1' } });

const resp = posted.find((m) => m.from === 'arena2api-injector' && m.type === 'MODELS_OK');
if (!resp) { console.error('FAIL: нет ответа MODELS_OK'); process.exit(1); }

const models = resp.models;
const byName = {};
models.forEach((m) => { byName[m.publicName] = m; });   // сервер берёт последний по имени

const checks = [];
checks.push(['моделей получено', models.length > 100, models.length]);
checks.push(['уникальных имён', Object.keys(byName).length > 100, Object.keys(byName).length]);
checks.push(['Max -> ожидаемый UUID', byName['Max'] && byName['Max'].id === '019b24bb-5caf-71c3-b854-37d0c7086f21',
  byName['Max'] ? byName['Max'].id : 'нет Max']);
checks.push(["Max: нет 'image' в выводе", byName['Max'] && !byName['Max'].capabilities.outputCapabilities.includes('image'),
  JSON.stringify(byName['Max'] && byName['Max'].capabilities)]);

const badImage = models.filter((m) => m.capabilities.outputCapabilities.includes('image')
  && m.capabilities.outputCapabilities.includes('text'));
checks.push(['нет моделей text+image (иначе сервер ушлёт их в image-режим)', badImage.length === 0, badImage.length]);

const noText = models.filter((m) => !m.capabilities.outputCapabilities.length);
checks.push(['у всех моделей непустой outputCapabilities', noText.length === 0, noText.length]);

let fail = 0;
for (const [name, ok, val] of checks) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}  (${val})`);
  if (!ok) fail++;
}

if (process.env.DUMP) fs.writeFileSync(process.env.DUMP, JSON.stringify(models, null, 1));

console.log(fail ? `\n${fail} проверок провалено` : '\nвсе проверки пройдены');
process.exit(fail ? 1 : 0);
