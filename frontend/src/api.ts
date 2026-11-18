/** Call the same-origin API with the current bearer token. */
export async function api<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${import.meta.env.VITE_API_BASE || ''}/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
  });
  if (!response.ok) {
    if (response.status === 401 && token) {
      localStorage.removeItem('ketsip.token');
      window.dispatchEvent(new Event('ketsip:session-expired'));
    }
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (${response.status})`);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
