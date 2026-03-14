import React from 'react';

const statusMap = {
  online: 'badge-online',
  active: 'badge-active',
  offline: 'badge-offline',
  inactive: 'badge-inactive',
  error: 'badge-error',
  pending: 'badge-pending',
  completed: 'badge-completed',
  approved: 'badge-approved',
  rejected: 'badge-rejected',
  suspended: 'badge-error',
  verified: 'badge-online',
  unverified: 'badge-pending',
};

const pulseStatuses = new Set(['online', 'active', 'pending']);

export default function StatusBadge({ status, label }) {
  if (!status) return null;
  const normalized = status.toLowerCase();
  const cls = statusMap[normalized] || 'badge-offline';
  const shouldPulse = pulseStatuses.has(normalized);

  return (
    <span className={`badge ${cls}`}>
      <span className={`badge-dot${shouldPulse ? ' pulse' : ''}`} />
      {label || status}
    </span>
  );
}
