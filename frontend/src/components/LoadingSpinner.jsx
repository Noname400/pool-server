import React from 'react';

export default function LoadingSpinner({ fullPage, size = 'md' }) {
  return (
    <div className={`spinner-container${fullPage ? ' full-page' : ''}`}>
      <div className={`spinner${size === 'lg' ? ' lg' : ''}`} />
    </div>
  );
}
