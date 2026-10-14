import { useEffect, useState } from 'react';
import { Clock, CheckCircle2, XCircle, Loader2, RefreshCw, ChevronRight } from 'lucide-react';
import { listAnalyses } from '../api';

const STATUS_CONFIG = {
  COMPLETED: { icon: CheckCircle2, cls: 'badge-success', label: 'Completed' },
  FAILED:    { icon: XCircle,      cls: 'badge-error',   label: 'Failed' },
  PROCESSING:{ icon: Loader2,      cls: 'badge-warning',  label: 'Processing' },
  UPLOADED:  { icon: Clock,        cls: 'badge-info',     label: 'Uploaded' },
};

export default function HistoryPage({ onLoadResult }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true); setError('');
    try {
      const data = await listAnalyses();
      setRuns(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  if (loading) return (
    <div className="flex items-center justify-center py-24">
      <div className="spinner" />
    </div>
  );

  if (error) return (
    <div className="flex flex-col items-center gap-4 py-16 text-center">
      <XCircle size={40} className="text-red-400" />
      <p className="text-red-300">{error}</p>
      <button onClick={load} className="btn-secondary flex items-center gap-2">
        <RefreshCw size={14} /> Retry
      </button>
    </div>
  );

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-200">Analysis History</h2>
        <button onClick={load} className="btn-secondary flex items-center gap-2 text-xs">
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {runs.length === 0 ? (
        <div className="flex flex-col items-center gap-4 py-20 text-center glass-card p-10">
          <Clock size={40} className="text-slate-600" />
          <p className="text-slate-500">No analyses yet. Upload a CSV to get started.</p>
        </div>
      ) : (
        <div className="rounded-xl border border-indigo-900/30 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-indigo-900/40 bg-slate-900/60">
                {['File', 'Status', 'Created', 'Action'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map(run => {
                const cfg = STATUS_CONFIG[run.status] || STATUS_CONFIG.UPLOADED;
                const Icon = cfg.icon;
                return (
                  <tr key={run.id} className="table-row">
                    <td className="px-4 py-3 font-mono text-xs text-slate-300 max-w-[200px] truncate">
                      {run.filename}
                    </td>
                    <td className="px-4 py-3">
                      <span className={cfg.cls}>
                        <Icon size={11} className={run.status === 'PROCESSING' ? 'animate-spin' : ''} />
                        {cfg.label}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500 text-xs">
                      {new Date(run.createdAt).toLocaleString()}
                    </td>
                    <td className="px-4 py-3">
                      {run.status === 'COMPLETED' && (
                        <button
                          onClick={() => onLoadResult(run.id)}
                          className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-200 transition-colors"
                        >
                          View <ChevronRight size={12} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
