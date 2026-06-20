'use client';

import { useEffect, useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { getSupabase } from '@/lib/supabase';
import { Loader2 } from 'lucide-react';

export default function AuthCallbackPage() {
  const router = useRouter();
  const [error, setError] = useState('');
  const processedRef = useRef(false);

  useEffect(() => {
    if (processedRef.current) return;
    processedRef.current = true;

    const handleCallback = async () => {
      const supabase = getSupabase();

      // Try to get the session from URL hash (implicit flow) or code (PKCE flow)
      const hashParams = new URLSearchParams(window.location.hash.substring(1));
      const accessToken = hashParams.get('access_token');
      const refreshToken = hashParams.get('refresh_token');

      // Also check query params for PKCE code flow
      const urlParams = new URLSearchParams(window.location.search);
      const code = urlParams.get('code');
      const next = urlParams.get('next');

      try {
        if (accessToken && refreshToken) {
          // Implicit flow — set session from tokens in hash
          const { error: sessionError } = await supabase.auth.setSession({
            access_token: accessToken,
            refresh_token: refreshToken,
          });
          if (sessionError) throw sessionError;
        } else if (code) {
          // PKCE flow — exchange code for session
          const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
          if (exchangeError) throw exchangeError;
        } else {
          // No tokens or code — maybe Supabase already handled it via onAuthStateChange
          // Wait a moment for the auth state to settle
          await new Promise(resolve => setTimeout(resolve, 1000));
        }

        // Get the current user after session is set
        const { data: { user } } = await supabase.auth.getUser();

        if (!user) {
          throw new Error('No user found after authentication');
        }

        // Handle password recovery flow
        if (next === '/reset-password') {
          router.replace('/reset-password');
          return;
        }

        // Check if onboarding is completed
        const { data: firmData } = await supabase
          .from('ca_firms')
          .select('onboarding_completed')
          .eq('supabase_user_id', user.id)
          .single();

        if (!firmData || !firmData.onboarding_completed) {
          router.replace('/onboarding');
        } else {
          router.replace('/dashboard');
        }
      } catch (err: any) {
        console.error('Auth callback error:', err);
        setError(err.message || 'Authentication failed');
        // Redirect to login after a delay
        setTimeout(() => router.replace('/login?error=auth_callback_error'), 2000);
      }
    };

    handleCallback();
  }, [router]);

  return (
    <div className="min-h-screen bg-black flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        {error ? (
          <>
            <div className="w-12 h-12 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center">
              <span className="text-red-400 text-xl">!</span>
            </div>
            <p className="text-red-400 text-sm">{error}</p>
            <p className="text-white/30 text-xs">Redirecting to login...</p>
          </>
        ) : (
          <>
            <Loader2 className="w-8 h-8 text-accent animate-spin" />
            <p className="text-white/30 text-sm">Completing sign in...</p>
          </>
        )}
      </div>
    </div>
  );
}
