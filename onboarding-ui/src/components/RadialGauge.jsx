import React from 'react';

export default function RadialGauge({ percentage = 0, label, sublabel, color = "#f59e0b" }) {
  const radius = 36;
  const circumference = 2 * Math.PI * radius;

  // FIX: Intercept NaN values to prevent SVG stroke distortion
  const safePercentage = Number.isNaN(percentage) ? 0 : percentage;
  const clamped = Math.min(100, Math.max(0, safePercentage));
  const strokeDashoffset = circumference - (clamped / 100) * circumference;

  return (
    <div className="flex items-center gap-4">
      <div className="relative w-20 h-20 flex items-center justify-center shrink-0">
        <svg className="w-20 h-20 -rotate-90" viewBox="0 0 88 88">
          <circle
            cx="44"
            cy="44"
            r={radius}
            stroke="currentColor"
            strokeWidth="8"
            fill="transparent"
            className="opacity-15 text-slate-500"
          />
          <circle
            cx="44"
            cy="44"
            r={radius}
            stroke={color}
            strokeWidth="8"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            fill="transparent"
            className="transition-all duration-700 ease-out"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center font-mono text-xs font-black theme-text-main">
          {clamped}%
        </div>
      </div>
      <div>
        <div className="text-[11px] font-bold uppercase tracking-wider theme-text-muted">{label}</div>
        <div className="text-lg font-black font-mono mt-0.5 theme-text-main">{sublabel}</div>
      </div>
    </div>
  );
}