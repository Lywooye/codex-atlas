import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
function runtime({ browserLanguage = 'en-US', saved, blockedStorage = false } = {}) {
  const storage = new Map(saved ? [['codex-atlas-language', saved]] : []);
  const document = { documentElement: { lang: '' }, title: '' };
  const cache = new Map();
  function load(name) {
    if (cache.has(name)) return cache.get(name);
    const filename = path.join(root, 'src', `${name}.ts`);
    const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText;
    const module = { exports: {} };
    vm.runInNewContext(code, {
      module, exports: module.exports, document,
      navigator: { language: browserLanguage },
      localStorage: {
        getItem(key) { if (blockedStorage) throw new Error('disabled'); return storage.get(key) ?? null; },
        setItem(key, value) { if (blockedStorage) throw new Error('disabled'); storage.set(key, value); },
      },
      require: relative => load(relative.replace(/^\.\//, '')),
    }, { filename });
    cache.set(name, module.exports);
    return module.exports;
  }
  return { ...load('i18n'), english: load('translations').english, errors: load('errorMessages').errorMessages, storage, document };
}

const en = runtime();
assert.equal(en.getLanguage(), 'en');
assert.equal(runtime({ browserLanguage: 'zh-TW' }).getLanguage(), 'zh');
assert.equal(runtime({ browserLanguage: 'zh-CN', saved: 'en' }).getLanguage(), 'en');
assert.equal(runtime({ browserLanguage: 'fr-FR', saved: 'unsupported' }).getLanguage(), 'en');
const blocked = runtime({ browserLanguage: 'zh-CN', blockedStorage: true });
blocked.setLanguage('en');
assert.equal(blocked.tr('模型设置'), 'Model settings');
en.setLanguage('zh');
assert.equal(en.storage.get('codex-atlas-language'), 'zh');
assert.equal(en.document.documentElement.lang, 'zh-CN');
assert.equal(en.tr('模型设置'), '模型设置');
en.setLanguage('en');
assert.equal(en.document.documentElement.lang, 'en');
assert.equal(en.tr('模型设置'), 'Model settings');
const sourceName = '保留原始名称 <example>';
assert.equal(en.tr('编辑 {0}', [sourceName]), `Edit ${sourceName}`);
assert.equal(en.localizeError('外部工具：保留原始诊断'), '外部工具：保留原始诊断');
assert.equal(en.localizeError('事件引用了所属片段和附带证据以外的消息'), 'An event referenced a message outside its segment and supporting evidence.');
assert.equal(en.localizeError('分析遗漏 3 条输入消息，未保存不完整结果'), 'Analysis omitted 3 input messages. The incomplete result was not saved.');
en.setLanguage('zh');
assert.equal(en.localizeError('Analysis omitted 3 input messages. The incomplete result was not saved.'), '分析遗漏 3 条输入消息，未保存不完整结果');
en.setLanguage('en');

const placeholders = value => [...value.matchAll(/\{\d+\}/g)].map(match => match[0]).sort();
for (const [key, value] of Object.entries({ ...en.english, ...en.errors })) {
  assert.deepEqual(placeholders(value), placeholders(key), `Interpolation differs for ${key}`);
  assert.ok(!/\p{Script=Han}/u.test(value), `Untranslated English catalog value: ${key}`);
}
const maps = new Set(['eventLabels', 'statusLabels', 'navItems', 'effortLabels']);
let checked = 0;
for (const name of ['App.tsx', 'api.ts']) {
  const filename = path.join(root, 'src', name);
  const text = fs.readFileSync(filename, 'utf8');
  const file = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function visit(node) {
    if (ts.isJsxText(node) && node.text === '中文' && ts.isJsxElement(node.parent) && node.parent.openingElement.tagName.getText(file) === 'option') return;
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isJsxText(node)) && /\p{Script=Han}/u.test(node.text)) {
      assert.ok(node.text in en.english, `Missing catalog entry in ${name}: ${node.text}`);
      let ancestor = node;
      let localized = false;
      while (ancestor) {
        if (ts.isCallExpression(ancestor) && ancestor.expression.getText(file) === 'tr') localized = true;
        if (ts.isVariableDeclaration(ancestor) && maps.has(ancestor.name.getText(file))) localized = true;
        ancestor = ancestor.parent;
      }
      assert.ok(localized, `Unwrapped authored UI text in ${name}: ${node.text}`);
      checked++;
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
}
console.log(`Internationalization checks passed: ${checked} UI strings, ${Object.keys(en.english).length} translations, ${Object.keys(en.errors).length} diagnostic translations.`);
