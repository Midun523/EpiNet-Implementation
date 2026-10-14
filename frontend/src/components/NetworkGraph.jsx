import { useCallback, useRef, useState, useEffect, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { SlidersHorizontal, Info } from 'lucide-react';

const GENE_COLORS = {
  CYP2D6: '#818cf8', CYP2C19: '#a78bfa', CYP3A4: '#c084fc',
  CYP2C9: '#e879f9', VKORC1: '#34d399', SLCO1B1: '#2dd4bf',
  TPMT: '#38bdf8', DPYD: '#fb923c', UGT1A1: '#fbbf24', ABCB1: '#f87171',
};

function buildGraph(genes, matrix, threshold) {
  const nodes = genes.map(id => ({ id, color: GENE_COLORS[id] || '#818cf8' }));
  const links = [];
  for (let i = 0; i < genes.length; i++) {
    for (let j = i + 1; j < genes.length; j++) {
      const score = matrix[i][j];
      if (score >= threshold) {
        links.push({ source: genes[i], target: genes[j], value: score });
      }
    }
  }
  return { nodes, links };
}

export default function NetworkGraph({ genes, matrix }) {
  const [threshold, setThreshold] = useState(0.2);
  const [hoveredLink, setHoveredLink] = useState(null);
  const [highlightNode, setHighlightNode] = useState(null);
  const [dimensions, setDimensions] = useState({ width: 640, height: 480 });
  const containerRef = useRef(null);
  const graphRef = useRef(null);

  useEffect(() => {
    const obs = new ResizeObserver(([entry]) => {
      const { width } = entry.contentRect;
      setDimensions({ width, height: Math.max(420, width * 0.65) });
    });
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  // Memoize graph data to prevent re-simulating the D3 layout on hover or node highlight
  const graphData = useMemo(() => buildGraph(genes, matrix, threshold), [genes, matrix, threshold]);

  const linkColor = useCallback((link) => {
    if (highlightNode && link.source.id !== highlightNode && link.target.id !== highlightNode) {
      return 'rgba(99,102,241,0.08)';
    }
    const alpha = 0.2 + link.value * 0.8;
    return `rgba(139,92,246,${alpha.toFixed(2)})`;
  }, [highlightNode]);

  const linkWidth = useCallback((link) => {
    const base = 0.5 + link.value * 5;
    return (highlightNode && (link.source.id === highlightNode || link.target.id === highlightNode))
      ? base * 1.8 : base;
  }, [highlightNode]);

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const isHighlighted = node.id === highlightNode;
    const r = isHighlighted ? 10 : 7;

    // Glow
    if (isHighlighted) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, r + 5, 0, 2 * Math.PI);
      ctx.fillStyle = `${node.color}33`;
      ctx.fill();
    }

    // Node circle
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = isHighlighted ? node.color : `${node.color}cc`;
    ctx.fill();
    ctx.strokeStyle = isHighlighted ? '#fff' : `${node.color}80`;
    ctx.lineWidth = isHighlighted ? 2 : 1;
    ctx.stroke();

    // Label
    const label = node.id;
    const fontSize = Math.min(12, 10 / globalScale * 3);
    ctx.font = `${fontSize}px Inter, sans-serif`;
    ctx.fillStyle = '#e2e8f0';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, node.x, node.y + r + fontSize + 1);
  }, [highlightNode]);

  return (
    <div className="space-y-4">
      {/* Threshold slider */}
      <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-900/50 border border-indigo-900/30">
        <SlidersHorizontal size={16} className="text-indigo-400 shrink-0" />
        <label className="text-sm text-slate-400 shrink-0">Edge threshold:</label>
        <input
          id="edge-threshold-slider"
          type="range" min="0" max="0.95" step="0.01"
          value={threshold}
          onChange={e => setThreshold(parseFloat(e.target.value))}
          className="flex-1 accent-indigo-500"
        />
        <span className="text-indigo-300 font-mono text-sm w-10 text-right">{threshold.toFixed(2)}</span>
        <span className="text-xs text-slate-500 ml-1">
          ({graphData.links.length} edges)
        </span>
      </div>

      {/* Hover tooltip */}
      {hoveredLink && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-indigo-900/30 border border-indigo-700/40 text-sm animate-fade-in">
          <Info size={14} className="text-indigo-400" />
          <span className="text-slate-300">
            <span className="text-indigo-300 font-semibold">{hoveredLink.source.id}</span>
            {' ↔ '}
            <span className="text-indigo-300 font-semibold">{hoveredLink.target.id}</span>
            {' — Score: '}
            <span className="text-emerald-400 font-semibold font-mono">{hoveredLink.value.toFixed(4)}</span>
          </span>
        </div>
      )}

      {/* Graph */}
      <div ref={containerRef} className="graph-container rounded-2xl overflow-hidden border border-indigo-900/30 bg-slate-950/80">
        <ForceGraph2D
          ref={graphRef}
          graphData={graphData}
          width={dimensions.width}
          height={dimensions.height}
          backgroundColor="transparent"
          nodeCanvasObject={nodeCanvasObject}
          nodePointerAreaPaint={(node, color, ctx) => {
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(node.x, node.y, 12, 0, 2 * Math.PI);
            ctx.fill();
          }}
          linkColor={linkColor}
          linkWidth={linkWidth}
          linkDirectionalParticles={0}
          onLinkHover={setHoveredLink}
          onNodeClick={(node) => setHighlightNode(n => n === node.id ? null : node.id)}
          onNodeHover={(node) => {
            document.body.style.cursor = node ? 'pointer' : 'default';
          }}
          cooldownTicks={80}
          d3VelocityDecay={0.3}
        />
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-2 px-2">
        {genes.map(g => (
          <button
            key={g}
            onClick={() => setHighlightNode(n => n === g ? null : g)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-mono transition-all ${
              highlightNode === g
                ? 'bg-indigo-700/50 border border-indigo-500/60 text-white'
                : 'bg-slate-800/60 border border-slate-700/40 text-slate-400 hover:text-slate-200'
            }`}
          >
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: GENE_COLORS[g] }} />
            {g}
          </button>
        ))}
      </div>
    </div>
  );
}
