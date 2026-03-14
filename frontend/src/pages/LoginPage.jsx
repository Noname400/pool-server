import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function LoginPage() {
  const [apiKey, setApiKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleChange = (e) => {
    setApiKey(e.target.value.trim());
    setError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (apiKey.length < 16) {
      setError('API key is too short');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const user = await login(apiKey);
      navigate('/admin');
    } catch (err) {
      setError(err.message || 'Invalid API key. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-bg" />
      <div className="login-card">
        <div className="login-logo">
          <div className="login-logo-icon">{'\u26A1'}</div>
          <h1 className="login-title">GPU Pool</h1>
          <p className="login-subtitle">Equipment Management System</p>
        </div>

        {error && <div className="login-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">API Key</label>
            <input
              type="text"
              className="form-input mono lg"
              placeholder="Enter API key"
              value={apiKey}
              onChange={handleChange}
              autoFocus
              disabled={loading}
            />
            <p className="form-hint">Enter your API key to authenticate</p>
          </div>

          <button
            type="submit"
            className="btn btn-primary btn-lg w-full"
            disabled={loading}
          >
            {loading ? 'Connecting...' : 'Connect to Pool'}
          </button>
        </form>
      </div>
    </div>
  );
}
