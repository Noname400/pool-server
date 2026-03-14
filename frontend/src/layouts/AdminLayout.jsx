import React from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from '../components/Sidebar';

export default function AdminLayout() {
  return (
    <div className="layout">
      <Sidebar variant="admin" />
      <div className="layout-main">
        <div className="topbar">
          <div className="topbar-title">
            GPU Pool
            <span className="topbar-badge">Admin</span>
          </div>
          <div className="topbar-right">
            <span className="text-secondary text-sm">Control Panel</span>
          </div>
        </div>
        <div className="page-content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
