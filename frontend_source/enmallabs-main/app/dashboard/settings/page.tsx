'use client';

import { useState } from 'react';
import { useDashboard } from '../dashboard-context';
import { getSupabase } from '@/lib/supabase';
import { motion } from 'framer-motion';
import {
  Building2, User, Mail, Phone, Shield, Send, CheckCircle2,
  Clock, AlertCircle, Save, Loader2, ExternalLink,
} from 'lucide-react';
import { GlassButton } from '@/components/GlassButton';

export default function SettingsPage() {
  const { firmData, firmLoading, user } = useDashboard();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [formData, setFormData] = useState({
    firm_name: '',
    ca_name: '',
    phone: '',
  });

  const [newPassword, setNewPassword] = useState('');
  const [confirmNewPassword, setConfirmNewPassword] = useState('');
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [passwordSuccess, setPasswordSuccess] = useState(false);
  const [passwordError, setPasswordError] = useState('');

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError('');
    setPasswordSuccess(false);

    if (newPassword.length < 8) {
      setPasswordError('Password must be at least 8 characters long.');
      return;
    }

    if (newPassword !== confirmNewPassword) {
      setPasswordError('Passwords do not match.');
      return;
    }

    setPasswordLoading(true);
    try {
      const supabase = getSupabase();
      const { error } = await supabase.auth.updateUser({
        password: newPassword,
      });

      if (error) throw error;
      setPasswordSuccess(true);
      setNewPassword('');
      setConfirmNewPassword('');
    } catch (err: any) {
      setPasswordError(err.message || 'Failed to update password.');
    } finally {
      setPasswordLoading(false);
    }
  };

  const startEditing = () => {
    setFormData({
      firm_name: firmData?.firm_name || '',
      ca_name: firmData?.ca_name || '',
      phone: firmData?.phone || '',
    });
    setEditing(true);
    setSaveSuccess(false);
  };

  const handleSave = async () => {
    if (!firmData?.id) return;
    setSaving(true);
    const supabase = getSupabase();
    const { error } = await supabase
      .from('ca_firms')
      .update({
        firm_name: formData.firm_name,
        ca_name: formData.ca_name,
        phone: formData.phone,
        updated_at: new Date().toISOString(),
      })
      .eq('id', firmData.id);

    setSaving(false);
    if (!error) {
      setEditing(false);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    }
  };

  if (firmLoading) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="h-8 w-32 bg-white/10 rounded animate-pulse mb-8" />
        <div className="space-y-6">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="glass-card rounded-[24px] p-8">
              <div className="h-5 w-40 bg-white/10 rounded animate-pulse mb-6" />
              <div className="space-y-4">
                <div className="h-12 bg-white/5 rounded-xl animate-pulse" />
                <div className="h-12 bg-white/5 rounded-xl animate-pulse" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-8">
        <h1 className="font-serif text-3xl font-medium mb-2">Settings</h1>
        <p className="text-white/40 text-sm">Manage your firm profile and integrations.</p>
      </div>

      {/* Save Success */}
      {saveSuccess && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className="mb-6 p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center gap-3"
        >
          <CheckCircle2 className="w-5 h-5 text-emerald-400" />
          <span className="text-sm text-emerald-400">Settings saved successfully.</span>
        </motion.div>
      )}

      <div className="space-y-6">
        {/* Firm Profile */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card rounded-[24px] p-8"
        >
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center">
                <Building2 className="w-5 h-5 text-accent" />
              </div>
              <h2 className="font-serif text-xl font-medium">Firm Profile</h2>
            </div>
            {!editing && (
              <button
                onClick={startEditing}
                className="px-4 py-2 rounded-xl bg-white/5 border border-white/10 text-xs font-bold text-white/60 hover:text-white hover:bg-white/10 transition-all"
              >
                Edit
              </button>
            )}
          </div>

          <div className="space-y-5">
            <SettingsField
              icon={Building2}
              label="Firm Name"
              value={editing ? formData.firm_name : (firmData?.firm_name || '—')}
              editing={editing}
              onChange={(v) => setFormData((p) => ({ ...p, firm_name: v }))}
            />
            <SettingsField
              icon={User}
              label="CA Name"
              value={editing ? formData.ca_name : (firmData?.ca_name || '—')}
              editing={editing}
              onChange={(v) => setFormData((p) => ({ ...p, ca_name: v }))}
            />
            <SettingsField
              icon={Mail}
              label="Email"
              value={firmData?.email || user?.email || '—'}
              editing={false}
              disabled
            />
            <SettingsField
              icon={Phone}
              label="Phone"
              value={editing ? formData.phone : (firmData?.phone || '—')}
              editing={editing}
              onChange={(v) => setFormData((p) => ({ ...p, phone: v }))}
            />
          </div>

          {editing && (
            <div className="flex items-center gap-3 mt-6 pt-6 border-t border-white/5">
              <button
                onClick={handleSave}
                disabled={saving}
                className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent/20 border border-accent/30 text-white text-sm font-bold hover:bg-accent/30 transition-all disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {saving ? 'Saving...' : 'Save Changes'}
              </button>
              <button
                onClick={() => setEditing(false)}
                className="px-5 py-2.5 rounded-xl bg-white/5 border border-white/10 text-sm font-bold text-white/50 hover:text-white transition-all"
              >
                Cancel
              </button>
            </div>
          )}
        </motion.div>

        {/* Telegram Integration */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="glass-card rounded-[24px] p-8"
        >
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-[#229ED9]/10 border border-[#229ED9]/20 flex items-center justify-center">
              <Send className="w-5 h-5 text-[#229ED9]" />
            </div>
            <h2 className="font-serif text-xl font-medium">Telegram Integration</h2>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between py-3">
              <div className="flex items-center gap-3">
                <span className="text-sm text-white/60">Connection Status</span>
              </div>
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${firmData?.telegram_chat_id ? 'bg-emerald-500 animate-pulse' : 'bg-white/20'}`} />
                <span className={`text-sm font-bold ${firmData?.telegram_chat_id ? 'text-emerald-400' : 'text-white/40'}`}>
                  {firmData?.telegram_chat_id ? 'Connected' : 'Not Connected'}
                </span>
              </div>
            </div>

            {firmData?.telegram_chat_id && (
              <>
                <div className="flex items-center justify-between py-3 border-t border-white/5">
                  <span className="text-sm text-white/60">Chat ID</span>
                  <span className="text-sm font-mono text-white/40">{firmData.telegram_chat_id}</span>
                </div>
                {firmData.telegram_linked_at && (
                  <div className="flex items-center justify-between py-3 border-t border-white/5">
                    <span className="text-sm text-white/60">Connected Since</span>
                    <span className="text-sm text-white/40">
                      {new Date(firmData.telegram_linked_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
                    </span>
                  </div>
                )}
              </>
            )}

            <a
              href="https://t.me/enma12bot"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 px-4 py-3 rounded-xl bg-[#229ED9]/10 border border-[#229ED9]/20 text-[#229ED9] text-sm font-bold hover:bg-[#229ED9]/20 transition-all mt-4"
            >
              <Send className="w-4 h-4" />
              {firmData?.telegram_chat_id ? 'Open Telegram Bot' : 'Connect Telegram'}
              <ExternalLink className="w-3.5 h-3.5 ml-auto" />
            </a>
          </div>
        </motion.div>

        {/* DPA & Compliance */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          className="glass-card rounded-[24px] p-8"
        >
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
              <Shield className="w-5 h-5 text-emerald-400" />
            </div>
            <h2 className="font-serif text-xl font-medium">Compliance & DPA</h2>
          </div>

          <div className="space-y-4">
            <div className="flex items-center justify-between py-3">
              <span className="text-sm text-white/60">DPA Status</span>
              <div className="flex items-center gap-2">
                {firmData?.dpa_consented ? (
                  <>
                    <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                    <span className="text-sm font-bold text-emerald-400">Signed</span>
                  </>
                ) : (
                  <>
                    <AlertCircle className="w-4 h-4 text-amber-400" />
                    <span className="text-sm font-bold text-amber-400">Pending</span>
                  </>
                )}
              </div>
            </div>

            {firmData?.dpa_consented_at && (
              <div className="flex items-center justify-between py-3 border-t border-white/5">
                <span className="text-sm text-white/60">Signed At</span>
                <span className="text-sm text-white/40">
                  {new Date(firmData.dpa_consented_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            )}

            <div className="flex items-center justify-between py-3 border-t border-white/5">
              <span className="text-sm text-white/60">Subscription Plan</span>
              <span className="text-sm font-bold text-accent capitalize">{firmData?.subscription_plan || 'Trial'}</span>
            </div>

            <div className="flex items-center justify-between py-3 border-t border-white/5">
              <span className="text-sm text-white/60">Account Created</span>
              <span className="text-sm text-white/40">
                {firmData?.created_at
                  ? new Date(firmData.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
                  : '—'}
              </span>
            </div>

            <div className="flex items-center justify-between py-3 border-t border-white/5">
              <span className="text-sm text-white/60">Account Status</span>
              <div className="flex items-center gap-2">
                <div className={`w-2 h-2 rounded-full ${firmData?.is_active ? 'bg-emerald-500' : 'bg-red-500'}`} />
                <span className={`text-sm font-bold ${firmData?.is_active ? 'text-emerald-400' : 'text-red-400'}`}>
                  {firmData?.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            </div>
          </div>
        </motion.div>

        {/* Security & Password */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
          className="glass-card rounded-[24px] p-8"
        >
          <div className="flex items-center gap-3 mb-6">
            <div className="w-10 h-10 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center">
              <Shield className="w-5 h-5 text-accent" />
            </div>
            <h2 className="font-serif text-xl font-medium">Security & Password</h2>
          </div>

          {passwordSuccess && (
            <div className="mb-6 p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm flex items-center gap-3">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>Password updated successfully.</span>
            </div>
          )}

          {passwordError && (
            <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-center gap-3">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{passwordError}</span>
            </div>
          )}

          <form onSubmit={handleChangePassword} className="space-y-5">
            <div>
              <label className="text-[10px] font-bold uppercase tracking-wider text-white/30 block mb-2">New Password</label>
              <input
                type="password"
                placeholder="Enter new password (min 8 chars)"
                required
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white text-sm px-4 py-3.5 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all placeholder:text-white/20"
              />
            </div>
            <div>
              <label className="text-[10px] font-bold uppercase tracking-wider text-white/30 block mb-2">Confirm New Password</label>
              <input
                type="password"
                placeholder="Confirm new password"
                required
                value={confirmNewPassword}
                onChange={(e) => setConfirmNewPassword(e.target.value)}
                className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white text-sm px-4 py-3.5 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all placeholder:text-white/20"
              />
            </div>
            <GlassButton
              type="submit"
              variant="white"
              className="px-6 py-3"
              disabled={passwordLoading || !newPassword || !confirmNewPassword}
            >
              {passwordLoading ? (
                <span className="flex items-center gap-2 justify-center">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Updating...
                </span>
              ) : (
                'Update Password'
              )}
            </GlassButton>
          </form>
        </motion.div>
      </div>
    </div>
  );
}

function SettingsField({ icon: Icon, label, value, editing, onChange, disabled }: {
  icon: any; label: string; value: string; editing: boolean; onChange?: (v: string) => void; disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-4">
      <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center shrink-0">
        <Icon className="w-4 h-4 text-white/20" />
      </div>
      <div className="flex-1">
        <label className="text-[10px] font-bold uppercase tracking-wider text-white/30 block mb-1">{label}</label>
        {editing && !disabled ? (
          <input
            type="text"
            value={value}
            onChange={(e) => onChange?.(e.target.value)}
            className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white text-sm px-4 py-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all"
          />
        ) : (
          <p className={`text-sm ${disabled ? 'text-white/30' : 'text-white/80'}`}>{value}</p>
        )}
      </div>
    </div>
  );
}
