import React from 'react';

export default function StatCard({ label, value, icon, trend, trendValue, subtitle, purple }) {
  return (
    <div className="stat-card">
      <div className="stat-card-header">
        <span className="stat-card-label">{label}</span>
        {icon && <span className="stat-card-icon">{icon}</span>}
      </div>
      <div className={`stat-card-value${purple ? ' purple' : ''}`}>{value}</div>
      {(trend || subtitle) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {trend && trendValue && (
            <span className={`stat-card-trend ${trend}`}>
              {trend === 'up' ? '\u25B2' : '\u25BC'} {trendValue}
            </span>
          )}
          {subtitle && <span className="stat-card-subtitle">{subtitle}</span>}
        </div>
      )}
    </div>
  );
}
