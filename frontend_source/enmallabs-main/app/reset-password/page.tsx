'use client';

import { useState, Suspense } from 'react';
import Link from 'next/link';
import { motion } from 'framer-motion';
import { Lock as LockIcon, AlertCircle, Loader2, CheckCircle2 } from 'lucide-react';
import { getSupabase } from '@/lib/supabase';
import { useRouter } from 'next/navigation';
import { GlassButton } from '@/components/GlassButton';

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    }>
      <ResetPasswordContent />
    </Suspense>
  );
}

function ResetPasswordContent() {
  const router = useRouter();
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }

    if (password !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }

    setLoading(true);

    try {
      const supabase = getSupabase();
      
      // Update the user password
      const { error: updateError } = await supabase.auth.updateUser({
        password: password,
      });

      if (updateError) throw updateError;

      // Force sign out after successful reset to clear active recovery session
      await supabase.auth.signOut();

      // Redirect to login with success flag
      router.push('/login?reset=success');
    } catch (err: any) {
      setError(err.message || 'Failed to update password. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col items-center justify-center p-6 relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_50%,rgba(255,78,0,0.05),transparent)] pointer-events-none" />

      {/* Brand logo */}
      <div className="flex items-center gap-2 mb-12 relative z-10">
        <div className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_rgba(255,78,0,0.8)]" />
        <span className="text-white font-bold text-xl tracking-tight">Enma.</span>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-[420px] bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 relative z-10"
      >
        <div className="mb-10 text-center">
          <h1 className="font-serif text-3xl font-medium mb-3">New Password</h1>
          <p className="text-white/50 text-sm">Please set your new password below.</p>
        </div>

        {error && (
          <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm text-center flex items-center gap-3 justify-center">
            <AlertCircle className="w-4 h-4 shrink-0" />
            {error}
          </div>
        )}

        <form onSubmit={handleResetPassword} className="space-y-4">
          <div>
            <div className="relative">
              <LockIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
              <input
                type="password"
                placeholder="New Password (min 8 chars)"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
              />
            </div>
          </div>

          <div>
            <div className="relative">
              <LockIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
              <input
                type="password"
                placeholder="Confirm New Password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
              />
            </div>
          </div>

          <GlassButton
            type="submit"
            variant="white"
            className="w-full mt-4"
            disabled={loading || !password || !confirmPassword}
          >
            {loading ? (
              <span className="flex items-center gap-2 justify-center">
                <Loader2 className="w-4 h-4 animate-spin" />
                Updating Password...
              </span>
            ) : (
              'Reset Password'
            )}
          </GlassButton>
        </form>
      </motion.div>
    </div>
  );
}
