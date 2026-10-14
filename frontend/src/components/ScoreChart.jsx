import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, Legend
} from 'recharts';

const COLORS = ['#818cf8','#a78bfa','#c084fc','#e879f9','#34d399',
                 '#2dd4bf','#38bdf8','#fb923c','#fbbf24','#f87171',
                 '#6366f1','#8b5cf6','#ec4899','#10b981','#0ea5e9'];

const CustomTooltip = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="px-3 py-2 rounded-xl bg-slate-800 border border-indigo-700/50 text-xs shadow-xl">
      <div className="font-semibold text-slate-200">{d.pair}</div>
      <div className="text-emerald-400 mt-0.5 font-mono">Score: {d.score.toFixed(4)}</div>
    </div>
  );
};

export default function ScoreChart({ topPairs }) {
  const data = topPairs.slice(0, 15).map((p, i) => ({
    pair: `${p.gene_a}↔${p.gene_b}`,
    score: p.score,
    color: COLORS[i % COLORS.length],
  }));

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Top {data.length} gene-pair interaction scores (simulated attention weights)
      </p>
      <ResponsiveContainer width="100%" height={320}>
        <BarChart data={data} margin={{ top: 4, right: 12, left: -12, bottom: 80 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(99,102,241,0.1)" vertical={false} />
          <XAxis
            dataKey="pair"
            tick={{ fill: '#94a3b8', fontSize: 9, fontFamily: 'JetBrains Mono' }}
            angle={-45}
            textAnchor="end"
            interval={0}
          />
          <YAxis
            tick={{ fill: '#94a3b8', fontSize: 10 }}
            domain={[0, 1]}
            tickFormatter={v => v.toFixed(1)}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(99,102,241,0.08)' }} />
          <Bar dataKey="score" radius={[4, 4, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.color} fillOpacity={0.85} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
