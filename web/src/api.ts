import { locale, localizeError, tr } from './i18n';

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  } catch {
    throw new Error(tr('网络连接失败，请确认本地服务正在运行。'));
  }
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === 'string' ? localizeError(error.detail) : tr('请求失败（{0}）', [response.status]));
  }
  return response.json();
}
export const post = <T,>(path: string, body?: unknown) => api<T>(path, {method: 'POST', body: JSON.stringify(body ?? {})});
export const patch = <T,>(path: string, body: unknown) => api<T>(path, {method: 'PATCH', body: JSON.stringify(body)});
export function date(value?: string, long = false) {
  if (!value) return tr('尚无记录');
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : new Intl.DateTimeFormat(locale(), long
    ? {year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}
    : {month:'short',day:'numeric'}).format(d);
}
export function errorText(e: unknown) { return e instanceof Error ? localizeError(e.message) : tr('暂时无法完成操作'); }
