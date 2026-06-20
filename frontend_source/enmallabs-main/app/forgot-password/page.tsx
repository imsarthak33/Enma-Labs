'use client';

import { useState, Suspense } from 'react';
import Link from 'next/link';
import { motion, AnimatePresence } from 'framer-motion';
import { Mail, CheckCircle2, ArrowLeft, Loader2, AlertCircle, KeyRound } from 'lucide-react';
import { getSupabase } from '@/lib/supabase';
import { GlassButton } from '@/components/GlassButton';

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    }>
      <ForgotPasswordContent />
    </Suspense>
  );
}

function ForgotPasswordContent() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [linkSent, setLinkSent] = useState(false);

  const handleResetRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const supabase = getSupabase();
      const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/auth/callback?next=/reset-password`,
      });

      if (resetError) throw resetError;
      setLinkSent(true);
    } catch (err: any) {
      setError(err.message || 'Failed to send password reset email. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col items-center justify-center p-6 relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_60%_50%_at_50%_50%,rgba(255,78,0,0.05),transparent)] pointer-events-none" />

      {/* Brand logo */}
      <Link href="/" className="flex items-center gap-2 group mb-12 relative z-10">
        <div className="w-2 h-2 rounded-full bg-accent shadow-[0_0_12px_rgba(255,78,0,0.8)]" />
        <span className="text-white font-bold text-xl tracking-tight">Enma.</span>
      </Link>

      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="w-full max-w-[420px] bg-white/[0.02] border border-white/10 backdrop-blur-3xl rounded-3xl p-10 relative z-10"
      >
        <AnimatePresence mode="wait">
          {linkSent ? (
            /* ── Reset Link Sent State ── */
            <motion.div
              key="sent"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              className="text-center"
            >
              <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
                <CheckCircle2 className="w-8 h-8 text-emerald-400" />
              </div>
              <h1 className="font-serif text-3xl font-medium mb-3">Check Your Email</h1>
              <p className="text-white/50 text-sm mb-2">
                We&apos;ve sent a password reset link to
              </p>
              <p className="text-accent font-bold text-sm mb-8">{email}</p>
              <p className="text-white/30 text-xs leading-relaxed mb-8">
                Click the link in the email to set a new password. If you don&apos;t see it, check your spam folder.
              </p>
              <Link
                href="/login"
                className="flex items-center gap-2 mx-auto text-sm text-white/40 hover:text-white transition-colors justify-center hover:underline"
              >
                <ArrowLeft className="w-4 h-4" />
                Back to Log In
              </Link>
            </motion.div>
          ) : (
            /* ── Request Reset Form State ── */
            <motion.div
              key="form"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
            >
              <div className="mb-10 text-center">
                <h1 className="font-serif text-3xl font-medium mb-3">Reset Password</h1>
                <p className="text-white/50 text-sm">Enter your email and we&apos;ll send a password recovery link.</p>
              </div>

              {error && (
                <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm text-center flex items-center gap-3 justify-center">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {error}
                </div>
              )}

              <form onSubmit={handleResetRequest} className="space-y-5">
                <div>
                  <div className="relative">
                    <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-white/20" />
                    <input
                      type="email"
                      placeholder="Enter your email address"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white pl-11 pr-4 py-4 focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/20"
                    />
                  </div>
                </div>

                <GlassButton
                  type="submit"
                  variant="white"
                  className="w-full"
                  disabled={loading || !email}
                >
                  {loading ? (
                    <span className="flex items-center gap-2 justify-center">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Sending Link...
                    </span>
                  ) : (
                    'Send Reset Link'
                  )}
                </GlassButton>
              </form>

              <div className="mt-8 text-center">
                <Link href="/login" className="flex items-center gap-2 mx-auto text-xs text-white/30 hover:text-white transition-colors justify-center hover:underline">
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Log In
                </Link>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
