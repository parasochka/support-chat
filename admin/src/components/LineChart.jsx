import { useId, useMemo, useRef, useState } from 'react';
import { useTheme } from '@mui/material/styles';
import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';

/**
 * Dependency-free SVG line chart for the dashboard time series
 * (GET /admin/timeseries -> [{bucket, value}]). One series per panel, area
 * fill, y grid, first/last date labels and a crosshair tooltip on hover.
 */
const W = 600;
const H = 190;
const PAD = { top: 10, right: 10, bottom: 24, left: 48 };

const fmtDay = (iso) => {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}`;
};

const LineChart = ({ series, color, format = (v) => String(v) }) => {
  const theme = useTheme();
  const gradId = useId();
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null); // index into series

  const geom = useMemo(() => {
    const data = series || [];
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const max = Math.max(...data.map((d) => d.value), 0) || 1;
    const x = (i) =>
      PAD.left + (data.length > 1 ? (i / (data.length - 1)) * innerW : innerW / 2);
    const y = (v) => PAD.top + innerH - (v / max) * innerH;
    const line = data.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.value)}`).join('');
    const area = data.length
      ? `${line}L${x(data.length - 1)},${PAD.top + innerH}L${x(0)},${PAD.top + innerH}Z`
      : '';
    return { data, max, x, y, line, area, innerH };
  }, [series]);

  const { data, max, x, y } = geom;

  if (!data.length) {
    return (
      <Box sx={{ py: 4, textAlign: 'center' }}>
        <Typography variant="body2" color="text.secondary">
          No data for the period.
        </Typography>
      </Box>
    );
  }

  const onMove = (e) => {
    const rect = svgRef.current.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const innerW = W - PAD.left - PAD.right;
    const rel = Math.min(1, Math.max(0, (px - PAD.left) / innerW));
    setHover(Math.round(rel * (data.length - 1)));
  };

  const gridColor = theme.palette.divider;
  const textColor = theme.palette.text.secondary;
  const ticks = [0, max / 2, max];
  const hovered = hover != null ? data[hover] : null;

  return (
    <Box sx={{ position: 'relative' }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.25" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(t)}
              y2={y(t)}
              stroke={gridColor}
              strokeWidth="1"
            />
            <text
              x={PAD.left - 6}
              y={y(t) + 3}
              textAnchor="end"
              fontSize="10"
              fill={textColor}
            >
              {format(t)}
            </text>
          </g>
        ))}
        <text x={PAD.left} y={H - 6} fontSize="10" fill={textColor}>
          {fmtDay(data[0].bucket)}
        </text>
        <text
          x={W - PAD.right}
          y={H - 6}
          textAnchor="end"
          fontSize="10"
          fill={textColor}
        >
          {fmtDay(data[data.length - 1].bucket)}
        </text>
        <path d={geom.area} fill={`url(#${gradId})`} />
        <path
          d={geom.line}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {hovered && (
          <g>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={H - PAD.bottom}
              stroke={gridColor}
              strokeWidth="1"
            />
            <circle
              cx={x(hover)}
              cy={y(hovered.value)}
              r="4"
              fill={color}
              stroke={theme.palette.background.paper}
              strokeWidth="2"
            />
          </g>
        )}
      </svg>
      {hovered && (
        <Box
          sx={{
            position: 'absolute',
            top: 0,
            left: `${(x(hover) / W) * 100}%`,
            transform: `translateX(${hover > data.length / 2 ? '-105%' : '8px'})`,
            bgcolor: 'background.paper',
            border: 1,
            borderColor: 'divider',
            borderRadius: 1,
            px: 1,
            py: 0.5,
            pointerEvents: 'none',
            whiteSpace: 'nowrap',
            boxShadow: 2,
          }}
        >
          <Typography variant="caption" color="text.secondary" component="div">
            {fmtDay(hovered.bucket)}
          </Typography>
          <Typography variant="caption" component="div">
            {format(hovered.value)}
          </Typography>
        </Box>
      )}
    </Box>
  );
};

export default LineChart;
