import React, { useState, useEffect } from 'react';
import LoadingSpinner from '../../components/LoadingSpinner';
import { getSettings, updateSettings } from '../../api/client';

export default function AdminSettings() {
  const [settings, setSettings] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    async function load() {
      try {
        const data = await getSettings();
        setSettings(data || {});
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const handleChange = (key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
    setSuccess('');
  };

  const [originalPartx, setOriginalPartx] = useState({});

  useEffect(() => {
    if (settings.partx_start !== undefined || settings.partx_step !== undefined) {
      setOriginalPartx((prev) => {
        if (prev.start !== undefined) return prev;
        return { start: settings.partx_start, step: settings.partx_step };
      });
    }
  }, [settings.partx_start, settings.partx_step]);

  const handleSave = async () => {
    const startChanged = settings.partx_start !== originalPartx.start;
    const stepChanged = settings.partx_step !== originalPartx.step;

    if (startChanged || stepChanged) {
      const changes = [];
      if (startChanged) changes.push(`Start: ${originalPartx.start || 0} → ${settings.partx_start}`);
      if (stepChanged) changes.push(`Step: ${originalPartx.step || 0} → ${settings.partx_step}`);

      const confirmed = window.confirm(
        `⚠️ Внимание! Изменение PartX влияет на раздачу номеров всем тренерам.\n\n` +
        `Изменения:\n${changes.join('\n')}\n\n` +
        `Раздача пойдёт с позиции Step. Продолжить?`
      );
      if (!confirmed) return;
    }

    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await updateSettings(settings);
      setOriginalPartx({ start: settings.partx_start, step: settings.partx_step });
      setSuccess('Settings saved successfully');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingSpinner />;

  return (
    <div>
      <div className="page-header">
        <h2 className="page-title">Settings</h2>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <div className="card">
        <div className="form-section">
          <h4 className="form-section-title">Telegram Notifications</h4>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Bot Token</label>
              <input
                className="form-input mono"
                value={settings.telegram_bot_token || ''}
                onChange={(e) => handleChange('telegram_bot_token', e.target.value)}
                placeholder="123456:ABC-DEF..."
              />
            </div>
            <div className="form-group">
              <label className="form-label">Chat ID</label>
              <input
                className="form-input mono"
                value={settings.telegram_chat_id || ''}
                onChange={(e) => handleChange('telegram_chat_id', e.target.value)}
                placeholder="-1001234567890"
              />
            </div>
          </div>
        </div>

        <div className="form-section">
          <h4 className="form-section-title">PartX Generator</h4>
          <div className="form-row">
            <div className="form-group">
              <label className="form-label">Min X (Start)</label>
              <input
                className="form-input mono"
                type="number"
                min="0"
                value={settings.partx_start || ''}
                onChange={(e) => handleChange('partx_start', e.target.value)}
                placeholder="0"
              />
              <p className="form-hint">
                Нижняя граница. Если Start &gt; Step, Step автоматически подтянется до Start.
              </p>
            </div>
            <div className="form-group">
              <label className="form-label">Current Step (позиция раздачи)</label>
              <input
                className="form-input mono"
                type="number"
                min="0"
                value={settings.partx_step || '0'}
                onChange={(e) => handleChange('partx_step', e.target.value)}
              />
              <p className="form-hint" style={{ color: '#e67e22' }}>
                Следующий тренер получит номера начиная с этого значения. Изменять с осторожностью!
              </p>
            </div>
          </div>
        </div>

        <div className="form-section">
          <h4 className="form-section-title">Machine Verification</h4>
          <div className="form-group">
            <label className="form-label">Test Seeds (comma-separated X values)</label>
            <input
              className="form-input mono"
              value={settings.test_seeds || ''}
              onChange={(e) => handleChange('test_seeds', e.target.value)}
              placeholder="32256080, 32097598, 31120107, 31004556"
            />
            <p className="form-hint">
              New machines must find all these X values before receiving real ranges.
              Leave empty to skip verification.
            </p>
          </div>
        </div>

        <div className="form-section">
          <h4 className="form-section-title">Trainer Authentication</h4>
          <div className="form-group">
            <label className="form-label">Trainer Auth Token (from .env, read-only)</label>
            <input
              className="form-input mono"
              value={settings.trainer_auth_token || ''}
              disabled
            />
            <p className="form-hint">
              Configured via TRAINER_AUTH_TOKEN in .env file. Restart pool to change.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
