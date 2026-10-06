export class Rejection extends Error {
  constructor(status, error) { super(error.message || 'The request was refused.'); this.status=status; this.code=error.code; }
}
export async function request(path, {method='GET', bodyText, token, key} = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(path, {method, signal:controller.signal, headers:{
      ...(bodyText !== undefined ? {'Content-Type':'application/json'} : {}),
      ...(token ? {Authorization:`Bearer ${token}`} : {}), ...(key ? {'Idempotency-Key':key} : {})
    }, ...(bodyText !== undefined ? {body:bodyText} : {})});
    const data = await response.json();
    if (!response.ok) {
      if (response.status >= 400 && response.status < 500 && typeof data?.error?.code === 'string') throw new Rejection(response.status,data.error);
      throw new Error('The server result could not be confirmed.');
    }
    return data;
  } finally { clearTimeout(timeout); }
}
export function randomKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return Array.from(bytes, b => b.toString(16).padStart(2,'0')).join('');
}
