import { useState, useMemo } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown, Search } from 'lucide-react';

export default function PairsTable({ topPairs }) {
  const [sortKey, setSortKey] = useState('score');
  const [sortDir, setSortDir] = useState('desc');
  const [filter, setFilter] = useState('');

  const sorted = useMemo(() => {
    const filtered = topPairs.filter(p =>
      p.gene_a.toLowerCase().includes(filter.toLowerCase()) ||
      p.gene_b.toLowerCase().includes(filter.toLowerCase())
    );
    return [...filtered].sort((a, b) => {
      const va = a[sortKey], vb = b[sortKey];
      if (typeof va === 'string') return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
      return sortDir === 'asc' ? va - vb : vb - va;
    });
  }, [topPairs, sortKey, sortDir, filter]);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('desc'); }
  };

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return <ArrowUpDown size={12} className="text-slate-600" />;
    return sortDir === 'asc'
      ? <ArrowUp size={12} className="text-indigo-400" />
      : <ArrowDown size={12} className="text-indigo-400" />;
  };

  const scoreBar = (score) => (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 bg-slate-800 rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-emerald-400 transition-all"
          style={{ width: `${score * 100}%` }}
        />
      </div>
      <span className="font-mono text-xs text-emerald-400 w-12 text-right">{score.toFixed(4)}</span>
    </div>
  );

  const cols = [
    { key: 'rank', label: '#', sortable: false },
    { key: 'gene_a', label: 'Gene A', sortable: true },
    { key: 'gene_b', label: 'Gene B', sortable: true },
    { key: 'score', label: 'Interaction Score', sortable: true },
  ];

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          id="pairs-filter"
          type="text"
          placeholder="Filter by gene name…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          className="input-field pl-9"
        />
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-indigo-900/30">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-indigo-900/40 bg-slate-900/60">
              {cols.map(col => (
                <th key={col.key}
                  className="px-4 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider">
                  {col.sortable ? (
                    <button
                      onClick={() => toggleSort(col.key)}
                      className="flex items-center gap-1.5 hover:text-slate-200 transition-colors"
                    >
                      {col.label}
                      <SortIcon col={col.key} />
                    </button>
                  ) : col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  No pairs match your filter.
                </td>
              </tr>
            ) : sorted.map((pair, idx) => (
              <tr key={`${pair.gene_a}-${pair.gene_b}`} className="table-row">
                <td className="px-4 py-3 text-slate-500 font-mono text-xs">{idx + 1}</td>
                <td className="px-4 py-3">
                  <span className="px-2.5 py-1 rounded-lg bg-indigo-900/40 text-indigo-300 text-xs font-mono font-semibold">
                    {pair.gene_a}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="px-2.5 py-1 rounded-lg bg-violet-900/40 text-violet-300 text-xs font-mono font-semibold">
                    {pair.gene_b}
                  </span>
                </td>
                <td className="px-4 py-3">{scoreBar(pair.score)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-slate-600 text-right">{sorted.length} pair{sorted.length !== 1 ? 's' : ''} shown</p>
    </div>
  );
}
