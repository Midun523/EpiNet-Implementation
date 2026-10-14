import { useCallback, useRef, useState } from 'react';
import { uploadCsv, getStatus, getResult } from '../api';

const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 60; // 60 * 2000ms = 120s timeout limit

/**
 * Custom hook managing the full upload → poll → result lifecycle.
 * Provides clean actions: `startUpload`, `loadResult`, and `reset`.
 */
export function useAnalysis() {
  const [phase, setPhase] = useState('idle'); // idle | uploading | processing | done | error
  const [analysisId, setAnalysisId] = useState(null);
  const [result, setResult] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [filename, setFilename] = useState('');
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
          setErrorMsg('Analysis timed out after 2 minutes. The backend may still be processing.');
          setPhase('error');
          return;
        }

        try {
          const status = await getStatus(id);
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
  }, [stopPolling]);

  const reset = useCallback(() => {
    stopPolling();
    setPhase('idle');
    setAnalysisId(null);
    setResult(null);
    setErrorMsg('');
    setFilename('');
  }, [stopPolling]);

  return {
    phase,
    analysisId,
    result,
    errorMsg,
    filename,
    startUpload,
    loadResult,
    reset,
  };
}
