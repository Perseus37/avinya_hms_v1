import React from 'react';
import { Lock, Unlock } from 'lucide-react';
import { INR, fmtInr } from '../lib/constants';

export default function KeyGatedFeeOverride({
  autoFeeInr, suffix,
  isUnlocked, onUnlockChange,
  customFeeInr, onCustomFeeChange,
  notify
}) {
  const handleUnlock = () => {
    if (window.confirm("Custom pricing changes are sensitive and will be audited. Continue as the signed-in operator?")) {
      onUnlockChange(true);
      notify("Custom fee override unlocked for this session.");
    }
  };

  return (
    <div className="theme-elevated border border-slate-500/20 p-4 rounded-2xl flex items-center justify-between">
      <div>
        <div className="text-[10px] font-bold uppercase theme-text-muted mb-1">Commercial Contract Fee</div>
        {isUnlocked ? (
          <div className="flex items-center gap-2">
            <span className="font-black theme-accent-text">{INR}</span>
            <input
              type="number"
              step="1"
              min="0"
              value={customFeeInr}
              onChange={(e) => onCustomFeeChange(e.target.value)}
              placeholder="Enter custom fee"
              className="theme-input rounded-lg px-2 py-1 font-mono text-sm w-32"
            />
            <span className="text-[10px] theme-text-muted">{suffix}</span>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="font-black text-lg">
              {fmtInr(autoFeeInr)}
            </span>
            <span className="text-[10px] theme-text-muted">{suffix}</span>
          </div>
        )}
      </div>
      <div>
        {isUnlocked ? (
          <button
            type="button"
            onClick={() => { onUnlockChange(false); onCustomFeeChange(""); }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/10 text-rose-500 hover:bg-rose-500/20 transition-colors text-[10px] font-bold"
          >
            <Unlock className="w-3.5 h-3.5" /> Lock
          </button>
        ) : (
          <button
            type="button"
            onClick={handleUnlock}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg theme-panel hover:brightness-110 transition-colors text-[10px] font-bold"
          >
            <Lock className="w-3.5 h-3.5 theme-text-muted" /> Override
          </button>
        )}
      </div>
    </div>
  );
}