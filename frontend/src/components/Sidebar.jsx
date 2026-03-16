import React from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

const adminLinks = [
  { to: '/admin', label: 'Overview', icon: '\u2302', end: true },
  { to: '/admin/machines', label: 'Machines', icon: '\u2699' },
  { to: '/admin/found-keys', label: 'Found Keys', icon: '\u{1F511}' },
  { to: '/admin/analytics', label: 'Analytics', icon: '\u{1F4CA}' },
  { to: '/admin/gate', label: 'Gate', icon: '\u{1F6E1}' },
  { to: '/admin/connect', label: 'Connect', icon: '\u2795' },
  { to: '/admin/settings', label: 'Settings', icon: '\u2699' },
];

export default function Sidebar({ variant = 'admin' }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="sidebar-logo-icon">{'\u26A1'}</div>
        <div className="sidebar-logo-text">GPU Pool v3</div>
      </div>

      <nav className="sidebar-nav">
        <div className="sidebar-section">Administration</div>
        {adminLinks.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              `sidebar-link${isActive ? ' active' : ''}`
            }
          >
            <span className="sidebar-link-icon">{link.icon}</span>
            <span>{link.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-user">
          <div className="sidebar-avatar">
            {(user?.username || 'U').charAt(0).toUpperCase()}
          </div>
          <div>
            <div className="sidebar-username">{user?.username || 'User'}</div>
            <div className="sidebar-role">{user?.role || 'admin'}</div>
          </div>
        </div>
        <button className="btn btn-ghost w-full" onClick={handleLogout}>
          Logout
        </button>
      </div>
    </aside>
  );
}
