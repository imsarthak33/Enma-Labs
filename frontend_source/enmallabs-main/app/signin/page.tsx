'use client';

import { useState, useEffect, Suspense } from 'react';
import Link from 'next/link';
import { motion, AnimatePresence } from 'framer-motion';
import { Mail, CheckCircle2, ArrowLeft, Loader2, AlertCircle, KeyRound } from 'lucide-react';
import { getSupabase } from '@/lib/supabase';
import { useAuth } from '@/lib/auth-context';
import { useRouter } from 'next/navigation';
import { GlassButton } from '@/components/GlassButton';

export default function SignInPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    }>
      <SignInContent />
    </Suspense>
  );
}

function SignInContent() {
  const router = useRouter();
  const { user, loading: authLoading } = useAuth();

  const [email, setEmail] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [otpSent, setOtpSent] = useState(false);

  // If already authenticated, redirect
  useEffect(() => {
    if (!authLoading && user) {
      router.push('/dashboard');
    }
  }, [user, authLoading, router]);

  const handleSendOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const supabase = getSupabase();
      const { error: authError } = await supabase.auth.signInWithOtp({
        email,
        options: {
          shouldCreateUser: true,
        },
      });

      if (authError) throw authError;
      setOtpSent(true);
    } catch (err: any) {
      setError(err.message || 'Failed to send verification code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const supabase = getSupabase();
      const { error: verifyError } = await supabase.auth.verifyOtp({
        email,
        token: otp.trim(),
        type: 'email',
      });

      if (verifyError) throw verifyError;
      
      // Verification succeeded. The auth-context will pick up the session change.
      router.push('/onboarding');
    } catch (err: any) {
      setError(err.message || 'Invalid or expired code. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleLogin = async () => {
    setError('');
    try {
      const supabase = getSupabase();
      const { error: authError } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          redirectTo: `${window.location.origin}/auth/callback`,
        },
      });

      if (authError) throw authError;
    } catch (err: any) {
      setError(err.message || 'Failed to sign in with Google.');
    }
  };

  if (authLoading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-accent animate-spin" />
      </div>
    );
  }

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
          {otpSent ? (
            /* ── OTP Verification State ── */
            <motion.div
              key="otp-verify"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
            >
              <div className="mb-8 text-center">
                <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-accent/10 border border-accent/20 flex items-center justify-center text-accent">
                  <KeyRound className="w-6 h-6" />
                </div>
                <h1 className="font-serif text-3xl font-medium mb-3">Verify Code</h1>
                <p className="text-white/50 text-sm">
                  We sent a 6-digit verification code to
                </p>
                <p className="text-accent font-bold text-sm mt-1">{email}</p>
              </div>

              {error && (
                <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm text-center flex items-center gap-3 justify-center">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {error}
                </div>
              )}

              <form onSubmit={handleVerifyOtp} className="space-y-5">
                <div>
                  <input
                    type="text"
                    placeholder="Enter 6-digit OTP"
                    required
                    maxLength={6}
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, ''))}
                    className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white px-4 py-4 text-center text-2xl font-mono tracking-[0.5em] focus:outline-none focus:border-accent/50 focus:ring-4 focus:ring-accent/10 transition-all placeholder:text-white/10 placeholder:text-sm placeholder:font-sans placeholder:tracking-normal"
                  />
                </div>

                <GlassButton
                  type="submit"
                  variant="white"
                  className="w-full"
                  disabled={loading || otp.length !== 6}
                >
                  {loading ? (
                    <span className="flex items-center gap-2 justify-center">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Verifying...
                    </span>
                  ) : (
                    'Verify & Sign In'
                  )}
                </GlassButton>
              </form>

              <div className="mt-8 text-center space-y-4">
                <button
                  onClick={handleSendOtp}
                  className="text-xs text-white/50 hover:text-white hover:underline transition-colors block mx-auto"
                >
                  Resend verification code
                </button>
                <button
                  onClick={() => { setOtpSent(false); setOtp(''); }}
                  className="flex items-center gap-2 mx-auto text-xs text-white/30 hover:text-white transition-colors"
                >
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Use a different email
                </button>
              </div>
            </motion.div>
          ) : (
            /* ── Sign In Form State ── */
            <motion.div
              key="signin-form"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
            >
              <div className="mb-10 text-center">
                <h1 className="font-serif text-4xl font-medium mb-3">Sign Up</h1>
                <p className="text-white/50 text-sm">Register your CA firm with Enma Labs.</p>
              </div>

              {error && (
                <div className="mb-6 p-4 rounded-2xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm text-center flex items-center gap-3 justify-center">
                  <AlertCircle className="w-4 h-4 shrink-0" />
                  {error}
                </div>
              )}

              {/* Google Login */}
              <button
                onClick={handleGoogleLogin}
                className="w-full flex items-center justify-center gap-3 bg-white/[0.04] border border-white/10 rounded-xl px-4 py-4 text-white text-sm font-medium hover:bg-white/[0.08] hover:border-white/20 transition-all mb-6"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z"/>
                  <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                  <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                  <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                </svg>
                Continue with Google
              </button>

              <div className="flex items-center gap-4 mb-6">
                <div className="flex-1 h-px bg-white/5" />
                <span className="text-white/20 text-xs uppercase tracking-widest">or</span>
                <div className="flex-1 h-px bg-white/5" />
              </div>

              {/* Email OTP Input */}
              <form onSubmit={handleSendOtp} className="space-y-5">
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
                      Sending Code...
                    </span>
                  ) : (
                    'Send Verification Code'
                  )}
                </GlassButton>
              </form>

              <div className="mt-8 text-center text-sm text-white/40">
                Already have an account?{' '}
                <Link href="/login" className="text-accent hover:underline font-semibold">
                  Log In
                </Link>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
