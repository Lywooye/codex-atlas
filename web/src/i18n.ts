import { english } from './translations';
import { errorMessages } from './errorMessages';

export type Language = 'en' | 'zh';
const preferenceKey = 'codex-atlas-language';

function initialLanguage(): Language {
  try {
    const saved = localStorage.getItem(preferenceKey);
    if (saved === 'en' || saved === 'zh') return saved;
  } catch { /* Browser storage can be disabled; the in-memory setting still works. */ }
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en';
}
let language = initialLanguage();
export function getLanguage(): Language { return language; }
export function locale(): string { return language === 'zh' ? 'zh-CN' : 'en'; }
export function setLanguage(value: Language): void {
  language = value;
  try { localStorage.setItem(preferenceKey, value); } catch { /* Keep the current page usable without storage. */ }
  document.documentElement.lang = locale();
  document.title = value === 'zh' ? '对话图谱 · Codex Atlas' : 'Codex Atlas · Projects from conversations';
}

/** Use only for authored UI copy, never for source text or generated project content. */
export function tr(key: string, values: readonly unknown[] = []): string {
  const template = language === 'zh' ? key : (english[key] ?? key);
  return template.replace(/\{(\d+)\}/g, (placeholder, index: string) =>
    Number(index) < values.length ? String(values[Number(index)]) : placeholder);
}

/** Known application diagnostics are localized; unknown tool messages remain unchanged. */
export function localizeError(message: string): string {
  for (const [zh, en] of Object.entries(errorMessages)) {
    const source = language === 'en' ? zh : en;
    const target = language === 'en' ? en : zh;
    if (message === source) return target;
    if (!source.includes('{0}')) continue;
    const captures: string[] = [];
    const pattern = source.split(/(\{\d+\})/).map(part => {
      if (/^\{\d+\}$/.test(part)) { captures.push(part); return '(.*?)'; }
      return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }).join('');
    const match = message.match(new RegExp(`^${pattern}$`, 's'));
    if (match) return target.replace(/\{\d+\}/g, token => match[captures.indexOf(token) + 1] ?? token);
  }
  return message;
}
