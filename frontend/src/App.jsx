import { useState } from 'react';
import { Upload, Network, BarChart3, Clock, Github, FlaskConical, AlertTriangle } from 'lucide-react';
import { useAnalysis } from './hooks/useAnalysis';
import { getResult } from './api';
import UploadZone from './components/UploadZone';
import NetworkGraph from './components/NetworkGraph';
import PairsTable from './components/PairsTable';
import ScoreChart from './components/ScoreChart';
import HistoryPage from './components/HistoryPage';

const TABS = [
  { id: 'upload',  label: 'Upload',  icon: Upload },
  { id: 'results', label: 'Results', icon: Network },
  { id: 'history', label: 'History', icon: Clock },
];

const RESULT_TABS = [
  { id: 'graph', label: 'Gene Network', icon: Network },
  { id: 'table', label: 'Top Pairs',    icon: BarChart3 },
  { id: 'chart', label: 'Score Chart',  icon: BarChart3 },
];

export default function App() {
  const [tab, setTab] = useState('upload');
  const [resultTab, setResultTab] = useState('graph');
  const { phase, result, errorMsg, filename, startUpload, loadResult, reset } = useAnalysis();

  // Load a past result from the history page
  const loadHistoricResult = async (analysisId) => {
    try {
      const data = await getResult(analysisId);
      loadResult(data, `Analysis ${analysisId.slice(0, 8)}`, analysisId);
      setTab('results');
    } catch (e) {
      console.error('Failed to load historical result:', e);
    }
  };

  const handleFile = async (file) => {
    setTab('results');
    await startUpload(file);
  };

  return (
    <div className="min-h-screen gradient-bg flex flex-col">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-40 border-b border-indigo-900/30 bg-slate-950/80 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-br from-indigo-600 to-violet-700 shadow-lg shadow-indigo-900/40">
              <FlaskConical size={20} className="text-white" />
            </div>
            <div>
              <h1 className="font-bold text-white text-lg leading-tight">EpiNet Visualizer</h1>
              <p className="text-indigo-400 text-xs leading-tight">Epistasis & Biomarker Interaction Dashboard</p>
            </div>
          </div>

          <nav className="hidden sm:flex items-center gap-1 bg-slate-900/60 border border-indigo-900/30 rounded-xl p-1">
            {TABS.map(t => {
              const Icon = t.icon;
              return (
                <button
                  key={t.id}
                  id={`tab-${t.id}`}
                  onClick={() => setTab(t.id)}
                  className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                    tab === t.id
                      ? 'bg-indigo-600 text-white shadow'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  <Icon size={15} />
                  {t.label}
                  {t.id === 'results' && result && (
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  )}
                </button>
              );
            })}
          </nav>

          <a
            href="https://github.com/Midun523/epinet-visualizer"
            target="_blank"
            rel="noopener noreferrer"
            title="View EpiNet Visualizer source code on GitHub"
            className="text-slate-500 hover:text-slate-300 transition-colors"
          >
            <Github size={20} />
          </a>
        </div>
      </header>

      {/* ── Main ──────────────────────────────────────────────────────────── */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 py-8">

        {/* ── Upload Tab ─────────────────────────────────────────────────── */}
        {tab === 'upload' && (
          <div className="max-w-xl mx-auto space-y-6 animate-slide-up">
            <div className="text-center space-y-2">
              <h2 className="text-2xl font-bold text-white">Analyze Your Data</h2>
              <p className="text-slate-400 text-sm leading-relaxed">
                Upload a pharmacogenomic CSV to compute simulated gene-gene epistasis interaction scores.
              </p>
            </div>

            <div className="glass-card p-6">
              <UploadZone onFile={handleFile} disabled={phase === 'uploading' || phase === 'processing'} />
            </div>

            {/* CSV format guide */}
            <div className="glass-card p-5 space-y-3">
              <h3 className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                <FlaskConical size={14} className="text-indigo-400" /> Expected CSV Format
              </h3>
              <pre className="text-xs font-mono text-emerald-400/80 overflow-x-auto bg-slate-950/60 rounded-lg p-3">
{`sample_id,CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,
SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype
SAMPLE_001,0.72,0.45,0.38,0.91,...`}
              </pre>
              <p className="text-xs text-slate-500">Continuous numeric gene expression/activity values + a phenotype column.</p>
            </div>
          </div>
        )}

        {/* ── Results Tab ────────────────────────────────────────────────── */}
        {tab === 'results' && (
          <div className="space-y-6 animate-slide-up">
            {/* Status bar */}
            <StatusBar phase={phase} filename={filename} errorMsg={errorMsg} onReset={() => { reset(); setTab('upload'); }} />

            {/* Result content */}
            {result && (
              <div className="space-y-4">
                {/* Meta pills */}
                <div className="flex flex-wrap gap-3 items-center">
                  <span className="badge badge-success">✓ Analysis Complete</span>
                  <span className="badge badge-info">
                    {result.meta?.rows_processed} rows
                  </span>
                  <span className="badge badge-warning flex items-center gap-1">
                    <AlertTriangle size={10} /> Epistasis Regression · {result.meta?.model}
                  </span>
                </div>

                {/* Sub-tab switcher */}
                <div className="flex gap-2 flex-wrap">
                  {RESULT_TABS.map(rt => {
                    const Icon = rt.icon;
                    return (
                      <button
                        key={rt.id}
                        id={`result-tab-${rt.id}`}
                        onClick={() => setResultTab(rt.id)}
                        className={`flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-all duration-200 ${
                          resultTab === rt.id
                            ? 'bg-indigo-600/80 text-white border border-indigo-500/50 shadow'
                            : 'bg-slate-900/40 text-slate-400 border border-indigo-900/30 hover:text-slate-200'
                        }`}
                      >
                        <Icon size={14} /> {rt.label}
                      </button>
                    );
                  })}
                </div>

                {/* Panels */}
                <div className="glass-card p-5 md:p-6">
                  {resultTab === 'graph' && (
                    <NetworkGraph genes={result.genes} matrix={result.matrix} />
                  )}
                  {resultTab === 'table' && (
                    <PairsTable topPairs={result.top_pairs} />
                  )}
                  {resultTab === 'chart' && (
                    <ScoreChart topPairs={result.top_pairs} />
                  )}
                </div>
              </div>
            )}

            {/* Empty / loading state */}
            {!result && phase === 'idle' && (
              <EmptyResults onGoUpload={() => setTab('upload')} />
            )}
          </div>
        )}

        {/* ── History Tab ────────────────────────────────────────────────── */}
        {tab === 'history' && (
          <HistoryPage onLoadResult={(id) => loadHistoricResult(id)} />
        )}
      </main>

      {/* ── Footer ────────────────────────────────────────────────────────── */}
      <footer className="border-t border-indigo-900/30 bg-slate-950/60 py-4 px-6 text-center text-xs text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <AlertTriangle size={11} className="text-amber-600" />
          <strong className="text-slate-500">Demo:</strong> Classical pairwise interaction regression (ΔR²) on synthetic data.
          Architecture is ready for a trained attention/transformer model to be dropped in. · EpiNet Visualizer v1.0 · MIT
        </span>
      </footer>
    </div>
  );
}

/* ── Sub-components ────────────────────────────────────────────────────────── */

function StatusBar({ phase, filename, errorMsg, onReset }) {
  if (phase === 'idle') return null;

  const configs = {
    uploading:  { color: 'border-indigo-700/50 bg-indigo-900/20', text: 'text-indigo-300', msg: `Uploading ${filename}…` },
    processing: { color: 'border-amber-700/50 bg-amber-900/10',   text: 'text-amber-300',  msg: `Analyzing ${filename} — polling every 2 s…` },
    done:       { color: 'border-emerald-700/50 bg-emerald-900/10',text: 'text-emerald-300',msg: `Analysis complete for ${filename}` },
    error:      { color: 'border-red-700/50 bg-red-900/10',        text: 'text-red-300',    msg: errorMsg },
  };
  const cfg = configs[phase] || configs.uploading;

  return (
    <div className={`flex items-center gap-3 px-4 py-3 rounded-xl border ${cfg.color} ${cfg.text} text-sm animate-fade-in`}>
      {(phase === 'uploading' || phase === 'processing') && <div className="spinner shrink-0" style={{ width: 18, height: 18 }} />}
      <span className="flex-1">{cfg.msg}</span>
      {(phase === 'done' || phase === 'error') && (
        <button onClick={onReset} className="text-xs underline opacity-60 hover:opacity-100">Reset</button>
      )}
    </div>
  );
}

function EmptyResults({ onGoUpload }) {
  return (
    <div className="flex flex-col items-center gap-5 py-24 text-center animate-fade-in">
      <div className="p-5 rounded-2xl bg-indigo-900/20 border border-indigo-800/30">
        <Network size={40} className="text-indigo-600" />
      </div>
      <div>
        <h3 className="text-lg font-semibold text-slate-300">No results yet</h3>
        <p className="text-slate-500 text-sm mt-1">Upload a pharmacogenomic CSV to visualize gene interactions.</p>
      </div>
      <button onClick={onGoUpload} className="btn-primary flex items-center gap-2">
        <Upload size={14} /> Go to Upload
      </button>
    </div>
  );
}
