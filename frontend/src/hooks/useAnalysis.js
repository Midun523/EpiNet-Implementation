import { useCallback, useRef, useState } from 'react';
import { uploadCsv, getStatus, getResult } from '../api';

const POLL_INTERVAL_MS = 2000;
// WHY 5 minutes: long enough for real analyses but prevents infinite polling
// if the backend or ML service is truly dead.
const MAX_POLL_ATTEMPTS = 150; // 150 * 2000ms = 300s = 5 minutes

/**
 * Custom hook managing the full upload → poll → result lifecycle.
 * Provides clean actions: `startUpload`, `loadResult`, and `reset`.
 *
 * Phase 1 changes:
 * - Increased timeout from 2 min to 5 min (Kafka path can be slower on first run)
 * - Treats both PENDING and PROCESSING as valid "waiting" states
 * - Shows which status we're actually in (PENDING vs PROCESSING) for debugging
 */
export function useAnalysis() {
  const [phase, setPhase] = useState('idle'); // idle | uploading | processing | done | error
  const [analysisId, setAnalysisId] = useState(null);
  const [result, setResult] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [filename, setFilename] = useState('');
  const [backendStatus, setBackendStatus] = useState(''); // raw status from backend (PENDING, PROCESSING, etc.)
  const pollRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startUpload = useCallback(async (file) => {
    stopPolling();
    setPhase('uploading');
    setResult(null);
    setErrorMsg('');
    setFilename(file.name);
    setBackendStatus('');

    try {
      const { analysisId: id } = await uploadCsv(file);
      setAnalysisId(id);
      setPhase('processing');

      let attempts = 0;

      // Poll every 2 s until COMPLETED or FAILED, with safety timeout
      pollRef.current = setInterval(async () => {
        attempts++;
        if (attempts >= MAX_POLL_ATTEMPTS) {
          stopPolling();
          setErrorMsg('Analysis timed out after 5 minutes. The backend may still be processing — check the History tab later.');
          setPhase('error');
          return;
        }

        try {
          const status = await getStatus(id);
          // WHY track backendStatus: so the UI can distinguish PENDING
          // (queued in Kafka) from PROCESSING (ML service is working).
          setBackendStatus(status.status);

          if (status.status === 'COMPLETED') {
            stopPolling();
            const data = await getResult(id);
            setResult(data);
            setPhase('done');
          } else if (status.status === 'FAILED') {
            stopPolling();
            setErrorMsg(status.errorMessage || 'Analysis failed on the server.');
            setPhase('error');
          }
          // PENDING and PROCESSING both mean "still working" — keep polling
        } catch (err) {
          stopPolling();
          setErrorMsg(err.message || 'Failed to poll analysis status.');
          setPhase('error');
        }
      }, POLL_INTERVAL_MS);

    } catch (err) {
      setErrorMsg(err.message || 'Failed to upload CSV file.');
      setPhase('error');
    }
  }, [stopPolling]);

  /**
   * Explicitly load a pre-computed or historical analysis result into state.
   */
  const loadResult = useCallback((data, name = 'Historical Run', id = null) => {
    stopPolling();
    setPhase('done');
    setResult(data);
    setErrorMsg('');
    setFilename(name);
    setAnalysisId(id);
    setBackendStatus('COMPLETED');
  }, [stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setPhase('idle');
    setAnalysisId(null);
    setResult(null);
    setErrorMsg('');
    setFilename('');
    setBackendStatus('');
  }, [stopPolling]);

  return {
    phase,
    analysisId,
    result,
    errorMsg,
    filename,
    backendStatus, // new: raw backend status for UI display
    startUpload,
    loadResult,
    reset,
  };
}
