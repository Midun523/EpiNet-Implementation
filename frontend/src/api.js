const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8080';

/**
 * Upload a CSV file for analysis.
 * Returns { analysisId, status }.
 */
export async function uploadCsv(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`${BASE}/api/uploads`, { method: 'POST', body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Upload failed' }));
    throw new Error(err.error || 'Upload failed');
  }
  return res.json();
}

/**
 * Poll analysis status.
 * Returns { analysisId, status, filename, createdAt, errorMessage }.
 */
export async function getStatus(analysisId) {
  const res = await fetch(`${BASE}/api/analyses/${analysisId}/status`);
  if (!res.ok) throw new Error('Status fetch failed');
  return res.json();
}

/**
 * Fetch completed analysis result.
 */
export async function getResult(analysisId) {
  const res = await fetch(`${BASE}/api/analyses/${analysisId}/result`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Result not found' }));
    throw new Error(err.error || 'Result not found');
  }
  return res.json();
}

/**
 * Fetch all past analysis runs (history).
 */
export async function listAnalyses() {
  const res = await fetch(`${BASE}/api/analyses`);
  if (!res.ok) throw new Error('Failed to fetch history');
  return res.json();
}
