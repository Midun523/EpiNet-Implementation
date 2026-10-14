import { useCallback, useState } from 'react';
import { Upload, FileText, AlertCircle, CheckCircle2 } from 'lucide-react';

const MAX_SIZE_MB = 5;

export default function UploadZone({ onFile, disabled }) {
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);

  const validate = (file) => {
    if (!file.name.endsWith('.csv')) return 'Only .csv files are accepted.';
    if (file.size > MAX_SIZE_MB * 1024 * 1024) return `File must be under ${MAX_SIZE_MB} MB.`;
    return null;
  };

  const handle = useCallback((file) => {
    const err = validate(file);
    if (err) { setFileError(err); setSelectedFile(null); return; }
    setFileError('');
    setSelectedFile(file);
    onFile(file);
  }, [onFile]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handle(file);
  }, [handle]);

  const onInputChange = (e) => {
    const file = e.target.files[0];
    if (file) handle(file);
    e.target.value = '';
  };

  return (
    <div className="space-y-4">
      <label
        id="upload-dropzone"
        htmlFor="csv-input"
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`
          flex flex-col items-center justify-center gap-4 p-10 rounded-2xl border-2 border-dashed
          cursor-pointer transition-all duration-300 select-none
          ${disabled ? 'opacity-50 pointer-events-none' : ''}
          ${dragging
            ? 'border-indigo-400 bg-indigo-900/20 shadow-[0_0_30px_rgba(99,102,241,0.25)]'
            : 'border-indigo-800/60 hover:border-indigo-600 hover:bg-indigo-900/10'}
        `}
      >
        <div className={`p-4 rounded-2xl transition-all duration-300
          ${dragging ? 'bg-indigo-600/30' : 'bg-indigo-900/40'}`}>
          <Upload size={32} className={`transition-colors ${dragging ? 'text-indigo-300' : 'text-indigo-500'}`} />
        </div>

        <div className="text-center">
          <p className="text-slate-300 font-semibold text-base">
            {dragging ? 'Drop it!' : 'Drag & drop your CSV here'}
          </p>
          <p className="text-slate-500 text-sm mt-1">
            or <span className="text-indigo-400 underline underline-offset-2">browse</span> · Max 5 MB
          </p>
        </div>

        <div className="flex flex-wrap gap-2 justify-center">
          {['sample_id', 'CYP2D6…ABCB1', 'phenotype'].map(col => (
            <span key={col} className="px-2.5 py-1 rounded-lg bg-slate-800/60 border border-slate-700/50 text-xs text-slate-400 font-mono">
              {col}
            </span>
          ))}
        </div>
      </label>

      <input id="csv-input" type="file" accept=".csv" className="hidden" onChange={onInputChange} disabled={disabled} />

      {fileError && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-900/20 border border-red-700/40 text-red-300 text-sm animate-fade-in">
          <AlertCircle size={16} className="shrink-0" />
          <span>{fileError}</span>
        </div>
      )}

      {selectedFile && !fileError && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-900/20 border border-emerald-700/40 text-emerald-300 text-sm animate-fade-in">
          <FileText size={16} className="shrink-0" />
          <span className="font-mono">{selectedFile.name}</span>
          <span className="text-emerald-500 ml-auto">({(selectedFile.size / 1024).toFixed(1)} KB)</span>
          <CheckCircle2 size={16} className="text-emerald-400" />
        </div>
      )}
    </div>
  );
}
